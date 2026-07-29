"""AWS — расход за текущий месяц через Cost Explorer.

`POST https://ce.{region}.amazonaws.com/`, протокол JSON-1.1 RPC: операция задаётся
не путём, а заголовком `X-Amz-Target: AWSInsightsIndexService.GetCostAndUsage`
(историческое внутреннее имя Cost Explorer — оно и есть цель вызова, менять нельзя).

Подпись — **SigV4 вручную**: boto3 ради одного запроса тянуть нельзя, а вся SigV4
это четыре HMAC-SHA256 поверх stdlib. Порядок шагов обязателен:

1. canonical request — метод, путь, query, канонические заголовки (имена в нижнем
   регистре, отсортированы, значения со схлопнутыми пробелами), список подписанных
   имён и hex-SHA256 тела;
2. string to sign — алгоритм, метка времени, scope `<дата>/<регион>/ce/aws4_request`
   и hex-SHA256 от canonical request;
3. ключ подписи — цепочка HMAC: `"AWS4"+secret` → дата → регион → сервис →
   `"aws4_request"`;
4. заголовок `Authorization` со scope, списком подписанных заголовков и подписью.

Грабли, каждая из которых стоит часа:

- **`host` обязан быть подписан и совпадать с тем, что реально уйдёт в сокет.**
  Поэтому хост собирается ОДИН раз, и из него же строится URL.
- **`x-amz-date` и дата в scope — одно значение**: scope берёт первые 8 символов
  той же строки, иначе `SignatureDoesNotMatch` без пояснений.
- **Расхождение часов больше 5 минут → 403**, неотличимо от неверного ключа.
- **Каждый вызов Cost Explorer стоит $0.01** — это тарифицируемый API, а не
  бесплатная ручка. Поэтому `verify()` делает ровно тот же единственный запрос,
  что и `payments()`, и никаких «пробных» обращений здесь нет.

Чего у AWS нет:

- **Баланса.** Аккаунт постоплатный, «остатка» в API не существует (в консоли —
  счета за закрытые периоды), поэтому `balance` не заявлен в CAPS и метод отдаёт
  None: инфра-биллинг честно покажет «баланс вручную».
- **Списка услуг.** Перечисление инстансов (`DescribeInstances`) — отдельная
  задача со своей пагинацией и своим регионом у каждого инстанса; `services()`
  по-прежнему возвращает [].

──────────────────────────────────────────────────────────────────────────────
Заказ (`order`) — это **другая служба**: `ec2.{region}.amazonaws.com`, протокол
**Query** (form-urlencoded тело `Action=RunInstances&Version=2016-11-15&…`),
подпись той же SigV4, но со `service="ec2"` (функция подписи одна на модуль —
`sign_headers` принимает сервис, тип содержимого и необязательный `x-amz-target`;
у Query-запроса цели нет). Что из этого следует:

- **Ответ Query — XML, а не JSON.** Разбор идёт через `xml.etree` с игнорированием
  пространства имён (`DescribeImagesResponse` объявляет свой xmlns, и поиск по
  полному тегу молча не находил бы ничего). Размер ответа ограничен до разбора.
- **⚠️ Каталог образов сокращён, и это не лень.** `DescribeImages` без фильтров
  отдаёт ДЕСЯТКИ ТЫСЯЧ AMI на регион. Мы просим только свежие системные сборки
  двух семейств (Amazon Linux 2023 и Ubuntu LTS от Canonical), с ограничением
  страницы, и сортируем по дате создания — то есть в форме заказа заведомо НЕ
  весь каталог AWS. Нужен другой образ — его id вводится напрямую.
- **⚠️ Регион в списке ровно один — тот, что в кредах.** AMI региональны:
  `ami-…` из `us-east-1` в `eu-west-1` не существует. Форма показывает плоские
  селекторы «регион» и «образ» и не связывает их, поэтому список из тридцати
  регионов гарантировал бы `InvalidAMIID.NotFound` при любом выборе, кроме
  одного. `DescribeRegions` при этом вызывается — чтобы подтвердить регион и
  показать его endpoint, а заодно не предлагать регион, в который аккаунт не
  включён (`opt-in-status`).
- **Цены нет.** Стоимость EC2 живёт в Pricing API (другая служба, свой регион и
  своя схема), поэтому у планов `price=None`, а `quote_order` не реализован:
  маршрут покупки проводит такой заказ через отдельное подтверждение «сумма
  заранее неизвестна». Выдумывать цену запрещено.
- **Имя сервера — тег.** У EC2 нет поля имени: `TagSpecification.1` с ключом
  `Name` — это ровно то, что показывает консоль в колонке «Name».
- **Ровно один создающий запрос.** `RunInstances` уходит один раз и никогда не
  повторяется: таймаут не значит «не создано», а повтор оплачивает второй
  инстанс.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from xml.etree import ElementTree

import httpx

from app.services.hosting_providers.base import (
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.aws")

_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "ce"
_TARGET = "AWSInsightsIndexService.GetCostAndUsage"
_CONTENT_TYPE = "application/x-amz-json-1.1"
_HOST_TPL = "ce.{region}.amazonaws.com"
_DEFAULT_REGION = "us-east-1"

# Регион уходит в ИМЯ ХОСТА, поэтому проверяется как строгий слаг — иначе
# подставленное значение увело бы подписанный запрос на чужой хост (урок oracle.py).
_REGION_RE = re.compile(r"[a-z0-9-]{2,40}")

# ── EC2 (Query API) ───────────────────────────────────────────
_EC2_SERVICE = "ec2"
_EC2_VERSION = "2016-11-15"
_EC2_HOST_TPL = "ec2.{region}.amazonaws.com"
_EC2_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=utf-8"

# Ответ Query — XML, и разбирается он целиком в памяти. Отфильтрованный
# `DescribeImages` укладывается в сотни килобайт; всё, что заметно больше, —
# признак снятого фильтра, и парсить это незачем.
_MAX_XML_BYTES = 8 * 1024 * 1024
_IMAGE_LIMIT = 40
_TYPE_LIMIT = 100

# Владельцы и шаблоны имён, которыми сужается каталог AMI (см. докстринг).
# `099720109477` — постоянный owner-id Canonical для образов Ubuntu.
_IMAGE_OWNERS = ("amazon", "099720109477")
_IMAGE_NAME_PATTERNS = (
    "al2023-ami-2023.*-x86_64",
    "ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*",
    "ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-amd64-server-*",
)

# Коды EC2, означающие «креды не те», независимо от HTTP-статуса.
_EC2_AUTH_CODES = {
    "AuthFailure", "UnauthorizedOperation", "InvalidClientTokenId",
    "SignatureDoesNotMatch", "MissingAuthenticationToken", "OptInRequired",
    "AccessDenied", "AccessDeniedException", "RequestExpired",
}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def amz_date(now: Optional[datetime] = None) -> str:
    """Метка времени SigV4. Отдельная функция — тесты подменяют её, иначе подпись
    недетерминирована."""
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def canonical_request(method: str, path: str, query: str,
                      headers: dict[str, str], payload: bytes) -> tuple[str, str]:
    """(canonical request, список подписанных заголовков) — считаются вместе,
    чтобы порядок имён в строке и в `SignedHeaders` не разъехался."""
    items = sorted(((k.lower(), " ".join(str(v).split())) for k, v in headers.items()))
    canon_headers = "".join(f"{k}:{v}\n" for k, v in items)
    signed = ";".join(k for k, _ in items)
    canon = "\n".join([
        method.upper(),
        path or "/",
        query or "",
        canon_headers,   # уже оканчивается \n → в склейке появится пустая строка
        signed,
        _sha256_hex(payload),
    ])
    return canon, signed


def signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    key = _hmac(("AWS4" + secret).encode(), datestamp)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def sign_headers(access_key_id: str, secret: str, region: str, host: str,
                 payload: bytes, stamp: str, *, service: str = _SERVICE,
                 target: str = _TARGET,
                 content_type: str = _CONTENT_TYPE) -> dict[str, str]:
    """Готовые заголовки подписанного POST-запроса.

    Дефолты — Cost Explorer (JSON-1.1 c `x-amz-target`); EC2 передаёт свой сервис
    и тип содержимого, а `target=""` убирает заголовок цели: у Query-протокола её
    нет, и подписанный, но не отправленный заголовок сорвал бы проверку подписи.
    Один SigV4 на модуль — дублировать подпись под второй сервис нельзя."""
    headers = {
        "content-type": content_type,
        "host": host,
        "x-amz-date": stamp,
    }
    if target:
        headers["x-amz-target"] = target
    canon, signed = canonical_request("POST", "/", "", headers, payload)
    datestamp = stamp[:8]
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([_ALGORITHM, stamp, scope, _sha256_hex(canon.encode())])
    signature = hmac.new(signing_key(secret, datestamp, region, service),
                         string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"{_ALGORITHM} Credential={access_key_id}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    return headers


def month_period(now: Optional[datetime] = None) -> tuple[str, str]:
    """[первое число месяца, первое число следующего) — у Cost Explorer `End`
    ИСКЛЮЧАЮЩАЯ граница, а MONTHLY требует границ месяца."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _num(value: Any) -> Optional[float]:
    """Cost Explorer отдаёт суммы СТРОКАМИ («12.3456789»)."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ── Query/XML: тело запроса и разбор ответа ───────────────────
def query_body(action: str, params: Optional[dict] = None) -> bytes:
    """Тело Query-запроса. Байты подписываются как есть, поэтому кодировать их
    надо ровно один раз и отправлять без повторной сборки клиентом."""
    items = {"Action": action, "Version": _EC2_VERSION, **(params or {})}
    # Сортировка не требуется протоколом (query-строка пуста, подписывается тело),
    # но делает запрос детерминированным — иначе подпись не воспроизвести в тесте.
    return urllib.parse.urlencode(sorted((k, str(v)) for k, v in items.items())).encode()


def _local(tag: Any) -> str:
    """Локальное имя тега без пространства имён: ответы EC2 объявляют xmlns, и
    поиск по полному тегу («{http://…}instanceId») молча не находит ничего."""
    return str(tag).rsplit("}", 1)[-1]


def parse_xml(raw: bytes) -> Optional[ElementTree.Element]:
    if not raw or len(raw) > _MAX_XML_BYTES:
        return None
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return None


def xml_child(node: Any, name: str) -> Optional[ElementTree.Element]:
    for child in list(node if node is not None else []):
        if _local(child.tag) == name:
            return child
    return None


def xml_text(node: Any, *path: str) -> str:
    cur = node
    for name in path:
        cur = xml_child(cur, name)
        if cur is None:
            return ""
    return (cur.text or "").strip()


def xml_items(node: Any, container: str) -> list:
    """`<container><item>…</item><item>…</item></container>` — форма всех списков
    Query-протокола."""
    box = xml_child(node, container)
    return [c for c in list(box if box is not None else []) if _local(c.tag) == "item"]


def ec2_error(root: Optional[ElementTree.Element], status: int, *secrets: str) -> str:
    """Причина отказа словами EC2.

    Ошибка приходит как `<Response><Errors><Error><Code/><Message/>` — вложенность
    у разных операций отличается, поэтому ищем первый узел `Error` обходом."""
    code = message = ""
    if root is not None:
        for node in root.iter():
            if _local(node.tag) != "Error":
                continue
            code, message = xml_text(node, "Code"), xml_text(node, "Message")
            break
    if code in _EC2_AUTH_CODES:
        return "неверные креды"
    base = map_http_error(status) or "AWS отклонил запрос"
    detail = " ".join(x for x in (code, message) if x).strip()
    # Сообщение вендора иногда несёт ARN и id ключа — через redact, как всё
    # остальное, что уходит пользователю.
    return redact(f"{base}: {detail}" if detail else base, *secrets)


class AwsAdapter(ProviderAdapter):
    KIND = "aws"
    TITLE = "AWS (Cost Explorer)"
    FIELDS = [
        CredField("access_key_id", "Access Key ID"),
        CredField("secret_access_key", "Secret Access Key", "password"),
        CredField("region", "Регион (по умолчанию us-east-1)", "text", required=False),
    ]
    # Ни баланса, ни списка услуг у Cost Explorer нет — см. модульный докстринг.
    # «order» — это EC2 RunInstances, отдельная служба на том же ключе.
    CAPS = {"payments", "order"}

    def _region(self, creds: dict) -> str:
        region = str((creds or {}).get("region") or "").strip().lower() or _DEFAULT_REGION
        return region if _REGION_RE.fullmatch(region) else ""

    def _order_region(self, creds: dict, spec: Optional[dict] = None) -> str:
        """Регион заказа: явный из spec (для прямых вызовов API), иначе из кредов.

        Проверяется тем же строгим слагом — значение уходит в ИМЯ ХОСТА."""
        raw = str(((spec or {}).get("region") or "")).strip().lower()
        if not raw:
            return self._region(creds)
        return raw if _REGION_RE.fullmatch(raw) else ""

    async def _cost_and_usage(self, creds: dict) -> tuple[Any, str]:
        region = self._region(creds)
        if not region:
            return None, "регион указан неверно"
        access_key_id = str((creds or {}).get("access_key_id") or "").strip()
        secret = str((creds or {}).get("secret_access_key") or "").strip()

        start, end = month_period()
        payload = json.dumps({
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
        }).encode()

        host = _HOST_TPL.format(region=region)
        headers = sign_headers(access_key_id, secret, region, host, payload, amz_date())

        try:
            async with self._client() as c:
                r = await c.post(f"https://{host}/", content=payload, headers=headers)
        except httpx.HTTPError as exc:
            return None, f"AWS недоступен: {redact(str(exc), secret, access_key_id)}"

        if r.status_code >= 400:
            return None, _http_error(r, secret, access_key_id)
        try:
            return r.json(), ""
        except ValueError:
            return None, "AWS вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._cost_and_usage(creds)
        return (False, err) if err else (True, "")

    async def payments(self, creds: dict) -> list[dict]:
        """Расход за текущий месяц ОДНОЙ записью: ledger'а платежей у Cost Explorer
        нет, есть агрегат потребления — суммируем его."""
        if self.check_fields(creds):
            return []
        data, err = await self._cost_and_usage(creds)
        if err or not isinstance(data, dict):
            return []
        rows = [r for r in (data.get("ResultsByTime") or []) if isinstance(r, dict)]
        if not rows:
            log.warning("aws: в ответе нет ResultsByTime")
            return []

        total = 0.0
        currency = ""
        parsed = 0
        for row in rows:
            cost = ((row.get("Total") or {}).get("UnblendedCost") or {})
            amount = _num(cost.get("Amount")) if isinstance(cost, dict) else None
            if amount is None:
                continue
            total += amount
            parsed += 1
            currency = currency or str(cost.get("Unit") or "")
        if not parsed:
            # Строки есть, а сумм в них нет — это переименованное поле, а не нулевой
            # расход. Показать «0» значило бы соврать про потраченные деньги.
            log.warning("aws: в ResultsByTime не нашлось UnblendedCost.Amount")
            return []
        start = str(((rows[0].get("TimePeriod") or {}).get("Start")) or "")
        return [{
            "ts": start,
            "amount": round(total, 2),
            "currency": currency.upper() or "USD",
            "type": "charge",
            "note": "расход за месяц (Cost Explorer)",
        }]

    # ── EC2: каталог и заказ ──────────────────────────────────
    async def _ec2(self, creds: dict, region: str, action: str,
                   params: Optional[dict] = None
                   ) -> tuple[Optional[ElementTree.Element], str]:
        """Один подписанный Query-запрос к EC2. Без ретраев — этим же методом
        уходит `RunInstances`."""
        if not region:
            return None, "регион указан неверно"
        akid = str((creds or {}).get("access_key_id") or "").strip()
        secret = str((creds or {}).get("secret_access_key") or "").strip()

        payload = query_body(action, params)
        host = _EC2_HOST_TPL.format(region=region)
        headers = sign_headers(akid, secret, region, host, payload, amz_date(),
                               service=_EC2_SERVICE, target="",
                               content_type=_EC2_CONTENT_TYPE)
        try:
            async with self._client() as c:
                r = await c.post(f"https://{host}/", content=payload, headers=headers)
        except httpx.HTTPError as exc:
            return None, f"EC2 недоступен: {redact(str(exc), secret, akid)}"

        root = parse_xml(r.content)
        if r.status_code >= 400:
            return None, ec2_error(root, r.status_code, secret, akid)
        if root is None:
            return None, "EC2 вернул не-XML ответ"
        return root, ""

    async def _region_entry(self, creds: dict, region: str) -> dict:
        """Единственная запись селектора регионов (см. докстринг — AMI региональны).

        `DescribeRegions` фильтруется по имени самого региона: он и подтверждает
        существование региона, и показывает, что аккаунт в него включён."""
        entry = {"id": region, "name": region}
        root, err = await self._ec2(creds, region, "DescribeRegions",
                                    {"RegionName.1": region})
        if err or root is None:
            return entry
        for item in xml_items(root, "regionInfo"):
            if xml_text(item, "regionName") != region:
                continue
            if xml_text(item, "optInStatus") == "not-opted-in":
                # Запускать в невключённом регионе нельзя — честнее не предлагать.
                return {}
            endpoint = xml_text(item, "regionEndpoint")
            return {"id": region, "name": f"{region} ({endpoint})" if endpoint else region}
        return entry

    async def _images(self, creds: dict, region: str) -> list[dict]:
        """Сокращённый каталог AMI: два семейства, свежие сначала."""
        params: dict = {"MaxResults": 200,
                        "Filter.1.Name": "state", "Filter.1.Value.1": "available",
                        "Filter.2.Name": "architecture", "Filter.2.Value.1": "x86_64",
                        "Filter.3.Name": "root-device-type", "Filter.3.Value.1": "ebs",
                        "Filter.4.Name": "name"}
        for i, owner in enumerate(_IMAGE_OWNERS, start=1):
            params[f"Owner.{i}"] = owner
        for i, pattern in enumerate(_IMAGE_NAME_PATTERNS, start=1):
            params[f"Filter.4.Value.{i}"] = pattern

        root, err = await self._ec2(creds, region, "DescribeImages", params)
        if err or root is None:
            log.warning("aws: каталог образов недоступен: %s", err)
            return []
        rows = []
        for item in xml_items(root, "imagesSet"):
            image_id = xml_text(item, "imageId")
            if not image_id:
                continue
            rows.append((xml_text(item, "creationDate"), {
                "id": image_id,
                "name": xml_text(item, "name") or xml_text(item, "description") or image_id,
            }))
        # Дата в ISO-8601 с Z — лексикографический порядок совпадает с временным.
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _ts, row in rows[:_IMAGE_LIMIT]]

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        region = self._region(creds)
        if not region:
            return None
        root, err = await self._ec2(creds, region, "DescribeInstanceTypes", {
            "MaxResults": _TYPE_LIMIT,
            # Прошлые поколения не предлагаем: их и так не заказывают, а список
            # без фильтра переваливает за тысячу позиций.
            "Filter.1.Name": "current-generation", "Filter.1.Value.1": "true",
        })
        if err or root is None:
            return None

        ranked: list[tuple[float, float, OrderPlan]] = []
        for item in xml_items(root, "instanceTypeSet"):
            name = xml_text(item, "instanceType")
            if not name:
                continue
            vcpus = _num(xml_text(item, "vCpuInfo", "defaultVCpus")) or 0.0
            # Память EC2 отдаёт в МЕБИбайтах — на этом легко ошибиться в 1024 раза.
            ram_gb = (_num(xml_text(item, "memoryInfo", "sizeInMiB")) or 0.0) / 1024
            arch_box = xml_child(xml_child(item, "processorInfo"),
                                 "supportedArchitectures")
            arch = next(((c.text or "").strip() for c in list(arch_box or [])), "")
            specs = f"{int(vcpus)} vCPU · {ram_gb:g} ГБ RAM"
            if arch:
                specs += f" · {arch}"
            ranked.append((vcpus, ram_gb, OrderPlan(
                id=name, name=name, specs=specs,
                # Цены в EC2 API нет — см. модульный докстринг.
                price=None, currency="", period="hour", region=region,
            )))
        ranked.sort(key=lambda row: (row[0], row[1], row[2].id))

        entry = await self._region_entry(creds, region)
        return OrderOptions(
            plans=[plan for _c, _r, plan in ranked],
            regions=[entry] if entry else [],
            images=await self._images(creds, region),
            custom=None,  # размеры у EC2 фиксированные (instance type)
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
            ("тип инстанса", instance_type), ("образ (AMI)", image_id),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}
        region = self._order_region(creds, spec)
        if not region:
            return {**fail, "error": "регион указан неверно"}

        params: dict = {
            "ImageId": image_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
        }
        if name:
            # Имени у инстанса нет — консоль показывает тег Name.
            params.update({
                "TagSpecification.1.ResourceType": "instance",
                "TagSpecification.1.Tag.1.Key": "Name",
                "TagSpecification.1.Tag.1.Value": name,
            })

        root, err = await self._ec2(creds, region, "RunInstances", params)
        if err:
            return {**fail, "error": err}
        instance_id = ""
        for item in xml_items(root, "instancesSet"):
            instance_id = xml_text(item, "instanceId")
            if instance_id:
                break
        if not instance_id:
            return {**fail, "error": "AWS принял запрос, но не вернул инстанс "
                                     "— проверьте консоль перед повторной попыткой"}
        return {
            "ok": True,
            "id": instance_id,
            "name": name or instance_id,
            # Стоимость EC2 в этом API не называется — маршрут покупки требует
            # отдельного подтверждения «сумма заранее неизвестна».
            "price": None,
            "currency": "",
            "error": "",
        }


def _http_error(r: httpx.Response, *secrets: str) -> str:
    """У JSON-1.1 причина лежит в теле (`__type`), а статус часто просто 400.
    Само сообщение не берём: оно может нести ARN аккаунта, а типа хватает,
    чтобы понять — нет прав `ce:GetCostAndUsage` или неверный ключ."""
    kind = ""
    try:
        body = r.json()
        kind = str((body or {}).get("__type") or "") if isinstance(body, dict) else ""
    except ValueError:
        kind = ""
    kind = kind.rsplit("#", 1)[-1]
    if kind in ("UnrecognizedClientException", "InvalidSignatureException",
                "InvalidClientTokenId", "AccessDeniedException"):
        return "неверные креды"
    base = map_http_error(r.status_code)
    return f"{base} ({redact(kind, *secrets)})" if kind else base


ADAPTER = AwsAdapter()
