"""Alibaba Cloud — баланс и счёт за месяц через BSS OpenAPI 2017-12-14.

RPC-стиль: один эндпоинт `GET https://business.aliyuncs.com/`, операция задаётся
параметром `Action` (`QueryAccountBalance` / `QueryBillOverview`), остальное —
общие параметры подписи в том же query.

Подпись (HMAC-SHA1 по RPC-схеме Alibaba) устроена так:

1. **процентное кодирование по правилам Alibaba** — RFC-3986 с тремя отличиями от
   обычного `urlencode`: пробел даёт `%20` (а не `+`), «`~`» НЕ кодируется, «`*`»
   кодируется в `%2A`. Ровно это даёт `quote(value, safe="~")`;
2. параметры (кроме самой подписи) сортируются по ключу и склеиваются в
   `k=v&k=v` — **уже закодированными**;
3. `StringToSign = "GET&" + enc("/") + "&" + enc(canonical_query)`, то есть
   canonical query кодируется ЦЕЛИКОМ ещё раз;
4. ключ HMAC — `secret + "&"` (амперсанд обязателен), результат в base64.

Грабли:

- **`SignatureNonce` обязан быть уникальным**, а `Timestamp` — в UTC формата
  `YYYY-MM-DDTHH:MM:SSZ`; расхождение часов больше 15 минут отклоняется.
- **`AvailableAmount` приходит строкой с РАЗДЕЛИТЕЛЯМИ ТЫСЯЧ** — «1,234.56».
  Запятая здесь не десятичная (в отличие от других адаптеров, где мы меняем «,»
  на «.»), её надо ВЫРЕЗАТЬ, иначе баланс либо не распарсится, либо станет 1.23.
- **Неверный AccessKeyId отдаёт HTTP 404** (`InvalidAccessKeyId.NotFound`), а не
  401/403. Без разбора `Code` из тела пользователь увидел бы «ручка API не
  найдена» вместо «неверные креды».
- **Признак успеха пишется двумя способами**: `"Code": "200"` у баланса и
  `"Code": "Success"` у части ручек, плюс булев `Success`. Проверяем оба.
- **Список счёта вложен дважды** — `Data.Items.Item[]` (наследие RPC-XML).

──────────────────────────────────────────────────────────────────────────────
Заказ (`order`) — служба **ECS**: тот же RPC и та же подпись, но домен
`ecs.aliyuncs.com` и `Version=2014-05-26`. Что важно помнить:

- **⚠️ Успешный ответ ECS не содержит ни `Code`, ни `Success`** — там сразу
  `{"RequestId", "Regions"/"InstanceIdSets"/…}`. Проверка успеха у BSS осталась
  строгой (`_ok(..., strict=True)`), а ECS-вызовы идут с `strict=False`:
  отсутствие `Code` для них означает успех. Без этого ВСЕ вызовы ECS считались бы
  ошибкой.
- **⚠️ Обязательных полей у `RunInstances` пять**: `RegionId`, `InstanceType`,
  `ImageId`, `SecurityGroupId`, `VSwitchId`. Последние два вводить пользователю
  негде, поэтому они выводятся из каталога: берётся доступный VSwitch региона, а
  затем группа безопасности **из той же VPC** — разные VPC вендор не соединит, и
  запрос наугад стоил бы отказа. Нет ни того, ни другого → честный отказ БЕЗ
  создающего запроса.
- **⚠️ Регион в списке ровно один — из кредов** (поле «Регион», по умолчанию
  `cn-hangzhou`). Идентификаторы образов у Alibaba региональные, а форма заказа
  показывает плоские селекторы и не связывает регион с образом: список из тридцати
  регионов гарантировал бы `InvalidImageId.NotFound` при любом выборе, кроме
  одного. `DescribeRegions` вызывается ради человеческого имени региона.
- **Каталог типов сокращён одной страницей** `DescribeInstanceTypes`
  (их больше тысячи) и отсортирован по ядрам и памяти.
- **Публичный IP НЕ заказывается.** `InternetMaxBandwidthOut` по умолчанию 0, и
  мы его не поднимаем: это отдельные деньги за трафик, а тратить сверх
  запрошенного адаптер не вправе. Полосу можно передать явным
  `spec["bandwidth_mbps"]`; иначе адрес назначается в консоли.
- **Цены нет ни в каталоге, ни в ответе** — `price=None`, `quote_order` не
  реализован (расчёт живёт в отдельном BSS-API `GetPayAsYouGoPrice`, форма
  параметров которого здесь не подтверждена). Выдумывать сумму запрещено.
- **Ровно один создающий запрос**: `RunInstances` уходит один раз и не
  повторяется — таймаут не значит «не создано».
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.alibaba")

_ENDPOINT = "https://business.aliyuncs.com/"
_VERSION = "2017-12-14"

# ── ECS (заказ) ───────────────────────────────────────────────
_ECS_ENDPOINT = "https://ecs.aliyuncs.com/"
_ECS_VERSION = "2014-05-26"
_DEFAULT_REGION = "cn-hangzhou"

# Регион уходит параметром, а не в имя хоста, но всё равно проверяется слагом:
# мусор здесь — это заведомый отказ вендора и лишний запрос к чужому API.
_REGION_RE = re.compile(r"[a-z0-9-]{2,40}")

_TYPE_LIMIT = 100
_IMAGE_LIMIT = 100
_NET_LIMIT = 50

# Коды, которые означают «креды не те», независимо от HTTP-статуса.
_AUTH_CODES = {
    "InvalidAccessKeyId.NotFound",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "SignatureNonceUsed",
    "InvalidSecurityToken.Expired",
    "NoPermission",
}


def percent_encode(value: Any) -> str:
    """Кодирование по правилам Alibaba: `%20` вместо `+`, «~» как есть, «*»→`%2A`."""
    return urllib.parse.quote(str(value), safe="~")


def string_to_sign(method: str, params: dict) -> str:
    canonical = "&".join(f"{percent_encode(k)}={percent_encode(v)}"
                         for k, v in sorted(params.items()))
    return f"{method.upper()}&{percent_encode('/')}&{percent_encode(canonical)}"


def sign(params: dict, secret: str, method: str = "GET") -> str:
    digest = hmac.new((secret + "&").encode(),
                      string_to_sign(method, params).encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _nonce() -> str:
    return uuid.uuid4().hex


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _billing_cycle(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _num(value: Any) -> Optional[float]:
    """Суммы приходят строками с разделителями тысяч: «1,234.56» → 1234.56.
    Запятая ВЫРЕЗАЕТСЯ, а не превращается в точку — она не десятичная."""
    text = str(value if value is not None else "").replace(",", "").replace("\xa0", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _ok(payload: dict, strict: bool = True) -> bool:
    """Успех RPC-ответа.

    `strict=True` (BSS) — как было: успехом считается только явный признак.
    `strict=False` (ECS) — успешный ответ конверта вообще не несёт (`{"RequestId",
    "Regions"}`), поэтому отсутствие `Code` тоже успех; ошибка ECS приезжает с
    HTTP 4xx/5xx и разбирается статусной веткой."""
    if payload.get("Success") is True:
        return True
    code = str(payload.get("Code") or "")
    if not code:
        return not strict
    return code in ("200", "Success")


def _rpc_list(payload: Any, outer: str, inner: str) -> list[dict]:
    """`{outer: {inner: [...]}}` — двойная вложенность всех списков RPC."""
    box = (payload or {}).get(outer) if isinstance(payload, dict) else None
    rows = box.get(inner) if isinstance(box, dict) else box
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


class AlibabaAdapter(ProviderAdapter):
    KIND = "alibaba"
    TITLE = "Alibaba Cloud"
    FIELDS = [
        CredField("access_key_id", "AccessKey ID"),
        CredField("access_key_secret", "AccessKey Secret", "password"),
        CredField("region_id", "Регион ECS (по умолчанию cn-hangzhou)",
                  "text", required=False),
    ]
    CAPS = {"balance", "payments", "order"}

    async def _call(self, creds: dict, action: str,
                    extra: Optional[dict] = None, *,
                    endpoint: str = _ENDPOINT, version: str = _VERSION,
                    strict: bool = True, detail: bool = False) -> tuple[Any, str]:
        """Один подписанный RPC-запрос. Без ретраев — этим же методом уходит
        `RunInstances`.

        `detail=True` добавляет к отказу `Message` вендора: для заказа причина
        («нет квоты», «VSwitch не найден») важнее кода."""
        akid = str((creds or {}).get("access_key_id") or "").strip()
        secret = str((creds or {}).get("access_key_secret") or "").strip()
        params = {
            "Action": action,
            "Format": "JSON",
            "Version": version,
            "AccessKeyId": akid,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": _nonce(),
            "Timestamp": _timestamp(),
            **(extra or {}),
        }
        params["Signature"] = sign(params, secret)

        try:
            async with self._client() as c:
                r = await c.get(endpoint, params=params)
        except httpx.HTTPError as exc:
            return None, f"Alibaba Cloud недоступен: {redact(str(exc), secret, akid)}"

        if r.status_code >= 400:
            return None, _http_error(r, secret, akid, detail=detail)
        try:
            payload = r.json()
        except ValueError:
            return None, "Alibaba Cloud вернул не-JSON ответ"
        if not isinstance(payload, dict):
            return None, "Alibaba Cloud вернул неожиданный ответ"
        if not _ok(payload, strict):
            code = str(payload.get("Code") or "")
            if code in _AUTH_CODES:
                return None, "неверные креды"
            tail = " ".join(x for x in (code, str(payload.get("Message") or "")
                                        if detail else "") if x).strip()
            return None, "Alibaba Cloud отклонил запрос" + (
                f" ({redact(tail, secret, akid)})" if tail else "")
        return payload, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._call(creds, "QueryAccountBalance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._call(creds, "QueryAccountBalance")
        if err or not isinstance(data, dict):
            return None
        block = data.get("Data")
        if not isinstance(block, dict):
            log.warning("alibaba: в QueryAccountBalance нет Data")
            return None
        # AvailableAmount — доступная сумма (кэш + кредитный лимит); именно её
        # сравнивают с порогом низкого баланса.
        amount = _num(block.get("AvailableAmount"))
        if amount is None:
            amount = _num(block.get("AvailableCashAmount"))
        if amount is None:
            log.warning("alibaba: не разобрал AvailableAmount")
            return None
        return Balance(amount, str(block.get("Currency") or "CNY").upper())

    async def payments(self, creds: dict) -> list[dict]:
        """Строки счёта за текущий расчётный месяц — по одной на продукт."""
        if self.check_fields(creds):
            return []
        cycle = _billing_cycle()
        data, err = await self._call(creds, "QueryBillOverview", {"BillingCycle": cycle})
        if err or not isinstance(data, dict):
            return []
        block = data.get("Data") if isinstance(data.get("Data"), dict) else {}
        items = block.get("Items")
        if isinstance(items, dict):        # RPC-наследие: Data.Items.Item[]
            items = items.get("Item")
        if not isinstance(items, list):
            log.warning("alibaba: неожиданная форма QueryBillOverview")
            return []

        out: list[dict] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            amount = _num(raw.get("PretaxAmount"))
            if amount is None:
                amount = _num(raw.get("PretaxGrossAmount"))
            out.append({
                "ts": str(raw.get("BillingCycle") or block.get("BillingCycle") or cycle),
                "amount": amount,
                "currency": str(raw.get("Currency") or "CNY").upper(),
                "type": "charge",
                "note": str(raw.get("ProductName") or raw.get("ProductCode") or ""),
            })
        return out

    # ── ECS: каталог и заказ ──────────────────────────────────
    def _region(self, creds: dict, spec: Optional[dict] = None) -> str:
        """Регион заказа: явный из spec (для прямых вызовов API), иначе из кредов."""
        raw = str(((spec or {}).get("region") or "")).strip().lower()
        if not raw:
            raw = str((creds or {}).get("region_id") or "").strip().lower() or _DEFAULT_REGION
        return raw if _REGION_RE.fullmatch(raw) else ""

    async def _ecs(self, creds: dict, action: str, extra: Optional[dict] = None,
                   *, detail: bool = False) -> tuple[Any, str]:
        return await self._call(creds, action, extra, endpoint=_ECS_ENDPOINT,
                                version=_ECS_VERSION, strict=False, detail=detail)

    async def _region_entry(self, creds: dict, region: str) -> dict:
        """Единственная запись селектора регионов (см. докстринг — образы
        региональны). Имя человеческое, если `DescribeRegions` ответил."""
        entry = {"id": region, "name": region}
        data, err = await self._ecs(creds, "DescribeRegions")
        if err:
            return entry
        for row in _rpc_list(data, "Regions", "Region"):
            if str(row.get("RegionId") or "") != region:
                continue
            local = str(row.get("LocalName") or "").strip()
            return {"id": region, "name": f"{local} ({region})" if local else region}
        return entry

    async def _network(self, creds: dict, region: str) -> tuple[str, str, str]:
        """(VSwitchId, SecurityGroupId, ошибка) для региона.

        ⚠️ Оба обязательны для `RunInstances` и обязаны быть в ОДНОЙ VPC —
        поэтому сеть выбирается первой, а группа подбирается под её `VpcId`.
        Ничего не нашлось → ошибка словами, а не запрос наугад."""
        data, err = await self._ecs(creds, "DescribeVSwitches",
                                    {"RegionId": region, "PageSize": _NET_LIMIT})
        if err:
            return "", "", err
        vswitches = [v for v in _rpc_list(data, "VSwitches", "VSwitch")
                     if v.get("VSwitchId")
                     and str(v.get("Status") or "Available").lower() == "available"]
        if not vswitches:
            return "", "", (f"в регионе {region} нет доступной подсети (VSwitch) "
                            f"— создайте VPC и подсеть в консоли Alibaba Cloud")

        data, err = await self._ecs(creds, "DescribeSecurityGroups",
                                    {"RegionId": region, "PageSize": _NET_LIMIT})
        if err:
            return "", "", err
        by_vpc: dict[str, str] = {}
        for g in _rpc_list(data, "SecurityGroups", "SecurityGroup"):
            if g.get("SecurityGroupId"):
                by_vpc.setdefault(str(g.get("VpcId") or ""), str(g["SecurityGroupId"]))
        for vsw in vswitches:
            group = by_vpc.get(str(vsw.get("VpcId") or ""))
            if group:
                return str(vsw["VSwitchId"]), group, ""
        return "", "", (f"в регионе {region} нет группы безопасности в той же VPC, "
                        f"что и подсеть — создайте её в консоли Alibaba Cloud")

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        region = self._region(creds)
        if not region:
            return None
        data, err = await self._ecs(creds, "DescribeInstanceTypes",
                                    {"MaxResults": _TYPE_LIMIT})
        if err:
            return None

        ranked: list[tuple[float, float, OrderPlan]] = []
        for row in _rpc_list(data, "InstanceTypes", "InstanceType"):
            tid = str(row.get("InstanceTypeId") or "")
            if not tid:
                continue
            cpu = _num(row.get("CpuCoreCount")) or 0.0
            # MemorySize у ECS уже в ГИГАбайтах (в отличие от Nova и EC2).
            ram = _num(row.get("MemorySize")) or 0.0
            ranked.append((cpu, ram, OrderPlan(
                id=tid, name=tid,
                specs=f"{int(cpu)} vCPU · {ram:g} ГБ RAM",
                # Цены ни в каталоге, ни в ответе нет — см. модульный докстринг.
                price=None, currency="", period="hour", region=region,
            )))
        ranked.sort(key=lambda row: (row[0], row[1], row[2].id))

        images: list[dict] = []
        data, err = await self._ecs(creds, "DescribeImages", {
            "RegionId": region, "Status": "Available",
            "ImageOwnerAlias": "system", "PageSize": _IMAGE_LIMIT,
        })
        if err:
            log.warning("alibaba: каталог образов недоступен: %s", err)
        else:
            images = [{"id": str(i["ImageId"]),
                       "name": str(i.get("OSNameEn") or i.get("ImageName")
                                   or i.get("OSName") or i["ImageId"])}
                      for i in _rpc_list(data, "Images", "Image") if i.get("ImageId")]

        return OrderOptions(
            plans=[plan for _c, _r, plan in ranked],
            regions=[await self._region_entry(creds, region)],
            images=images,
            custom=None,  # размеры у ECS фиксированные (instance type)
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": ""}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        instance_type = str(spec.get("plan_id") or "").strip()
        image_id = str(spec.get("image") or "").strip()
        name = str(spec.get("name") or "").strip()
        empty = [label for label, value in (
            ("тип инстанса", instance_type), ("образ", image_id),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}
        region = self._region(creds, spec)
        if not region:
            return {**fail, "error": "регион указан неверно"}

        # Сеть и группа безопасности обязательны, а ввести их негде — выводим из
        # каталога. Не вышло → отказ ДО создающего запроса.
        vswitch = str(spec.get("vswitch_id") or "").strip()
        group = str(spec.get("security_group_id") or "").strip()
        if not (vswitch and group):
            vswitch, group, err = await self._network(creds, region)
            if err:
                return {**fail, "error": err}

        params: dict = {
            "RegionId": region,
            "InstanceType": instance_type,
            "ImageId": image_id,
            "SecurityGroupId": group,
            "VSwitchId": vswitch,
            "Amount": 1,
        }
        if name:
            params["InstanceName"] = name
        # Публичную полосу (а с ней и оплату трафика) включаем только по явной
        # просьбе — тратить сверх запрошенного адаптер не вправе.
        bandwidth = _num(spec.get("bandwidth_mbps"))
        if bandwidth and bandwidth > 0:
            params["InternetMaxBandwidthOut"] = int(bandwidth)

        data, err = await self._ecs(creds, "RunInstances", params, detail=True)
        if err:
            return {**fail, "error": err}
        ids = (data or {}).get("InstanceIdSets") if isinstance(data, dict) else None
        if isinstance(ids, dict):
            ids = ids.get("InstanceIdSet")
        instance_id = next((str(i) for i in (ids or []) if i), "") if isinstance(ids, list) else ""
        if not instance_id:
            return {**fail, "error": "Alibaba Cloud принял запрос, но не вернул инстанс "
                                     "— проверьте консоль перед повторной попыткой"}
        return {
            "ok": True,
            "id": instance_id,
            "name": name or instance_id,
            # Сумму вендор не называет — маршрут покупки требует отдельного
            # подтверждения «сумма заранее неизвестна».
            "price": None,
            "currency": "",
            "error": "",
        }


def _http_error(r: httpx.Response, *secrets: str, detail: bool = False) -> str:
    code = message = ""
    try:
        body = r.json()
        if isinstance(body, dict):
            code = str(body.get("Code") or "")
            message = str(body.get("Message") or "")
    except ValueError:
        code = message = ""
    # Неверный ключ приходит как 404 — без этой ветки пользователь получил бы
    # «ручка API не найдена» и искал бы несуществующую проблему.
    if code in _AUTH_CODES:
        return "неверные креды"
    base = map_http_error(r.status_code)
    # `Message` добавляется только там, где его попросили (заказ): читающим
    # методам хватает кода, а текст вендора может нести идентификаторы аккаунта.
    tail = " ".join(x for x in (code, message if detail else "") if x).strip()
    return f"{base} ({redact(tail, *secrets)})" if tail else base


ADAPTER = AlibabaAdapter()
