"""Oracle Cloud (OCI) adapter — instances + monthly spend (Wave-9 Plan C, Ф5).

OCI authenticates every request with an **HTTP signature** (draft-cavage), which
is why this is the heaviest adapter: there is no token to fetch, each call is
signed with the API key of the user. `cryptography` does the RSA part, so the
official `oci` SDK (dozens of transitive packages for two endpoints) stays out.

What the signature must look like, and why each detail matters:

- The signing string is the header list, **in the advertised order**, one
  `name: value` per line joined by `\\n`. For a GET that is
  `(request-target)`, `date`, `host`; a POST appends `x-content-sha256`,
  `content-type`, `content-length` — in exactly that order. OCI rebuilds the
  string from the `headers="…"` field, so the order in the string and in that
  field are one decision, made in one function here.
- `(request-target)` is `<lowercase method> <path>[?query]` — the query is part of
  it, so the URL is assembled once and both signed and sent verbatim (re-encoding
  it later would break the signature).
- `keyId` is `{tenancy}/{user}/{fingerprint}`, algorithm `rsa-sha256`
  (`PKCS1v15` + SHA-256 — NOT the PSS padding Yandex needs).
- ⚠️ **A clock skew over 5 minutes gets a 401**, indistinguishable from a bad key.
  If verify fails on a freshly pasted key, check the host clock first.

No balance: OCI is post-paid with credits and has no «account balance» endpoint,
so `CAPS` advertises the monthly spend via `payments()` instead and infra-billing
keeps the manual amount.

Заказ (`order`) — `POST /20160918/instances` (LaunchInstance), ответ **200** с
объектом инстанса. Подпись та же самая, но для POST в неё входят ещё
`x-content-sha256`, `content-type` и `content-length` — это уже умеет
`sign_headers()`, отдельного кода подписи у заказа нет.

Четыре особенности, каждая из которых меняет поведение формы:

- **⚠️ Цены у OCI в API нет вовсе.** Ни `ListShapes`, ни ответ создания не
  называют сумму: тарификация живёт в биллинге. Поэтому у каждого плана
  `price=None`, а `quote_order` не реализован — маршрут покупки поддерживает
  такой заказ отдельной галочкой «сумма заранее неизвестна» (как у OpenStack).
- **⚠️ Свободный селектор формы (`regions`) несёт ДОМЕН ДОСТУПНОСТИ**, а не
  регион: регион задан в кредах (он же в имени хоста подписанного запроса), а
  `LaunchInstance` обязательно требует `availabilityDomain`. Тот же приём, что у
  `openstack`, где в этом селекторе едет сеть.
- **⚠️ `subnetId` обязателен, и его в `spec` нет** — выводим из каталога:
  берём подсеть, которая живёт в выбранном домене доступности (или региональную
  — у неё `availabilityDomain` пуст), публичные раньше приватных. Подсети нет →
  ОТКАЗ без создающего запроса: «создался, но недоступен» хуже, чем не создался.
  `assignPublicIp` намеренно НЕ передаётся — иначе публичный IP на приватной
  подсети даёт 400; без него действует умолчание самой подсети.
- **Гибкие формы (`*.Flex`) требуют `shapeConfig`** с ядрами и памятью. Нет их в
  спеке → отказ на нашей стороне, а не заведомый 400 у вендора. Отсюда же
  `custom` в каталоге: он появляется, только если у аккаунта реально есть хоть
  одна гибкая форма, и границы берутся из `ocpuOptions`/`memoryOptions`.

`ListAvailabilityDomains` живёт в СВОЁМ сервисе (Identity), а не в `iaas.*`, и
OCI публикует его под двумя написаниями хоста — пробуем оба по очереди.
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.hosting_providers.base import (
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.oracle")

_CORE_TPL = "https://iaas.{region}.oraclecloud.com/20160918"
_IAAS_TPL = _CORE_TPL + "/instances"
_USAGE_TPL = "https://usageapi.{region}.oci.oraclecloud.com/20200107/usage"
# Identity — отдельный сервис со своим хостом, и OCI публикует его под двумя
# написаниями. Пробуем по очереди: промах даёт 404, а не тишину.
_IDENTITY_TPLS = (
    "https://identity.{region}.oci.oraclecloud.com/20160918",
    "https://identity.{region}.oraclecloud.com/20160918",
)

# Загрузочный том: меньше 50 ГБ OCI не принимает, потолок — 32 ТБ.
_BOOT_MIN_GB = 50
_BOOT_MAX_GB = 32768

# The region is interpolated into a HOSTNAME, so it is validated as a strict
# slug — otherwise a crafted value could redirect the signed request elsewhere.
_REGION_RE = re.compile(r"[a-z0-9-]{2,40}")

_JSON = "application/json"


def _load_key(pem: str) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key((pem or "").strip().encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("нужен RSA-ключ API")
    return key


def signing_string(method: str, url: str, date: str, body: Optional[bytes] = None,
                   content_type: str = _JSON) -> tuple[str, list[str]]:
    """(string to sign, header names) — built together so they cannot drift."""
    parsed = urllib.parse.urlparse(url)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    names = ["(request-target)", "date", "host"]
    lines = [f"(request-target): {method.lower()} {target}",
             f"date: {date}",
             f"host: {parsed.netloc}"]
    if body is not None:
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        names += ["x-content-sha256", "content-type", "content-length"]
        lines += [f"x-content-sha256: {digest}",
                  f"content-type: {content_type}",
                  f"content-length: {len(body)}"]
    return "\n".join(lines), names


def sign_headers(creds: dict, method: str, url: str,
                 body: Optional[bytes] = None) -> dict[str, str]:
    """Signed request headers. Raises ValueError/TypeError on an unusable key —
    the caller turns that into a message, never into a traceback."""
    date = email.utils.formatdate(usegmt=True)
    string, names = signing_string(method, url, date, body)
    key = _load_key(str((creds or {}).get("private_key") or ""))
    signature = base64.b64encode(
        key.sign(string.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    key_id = "{}/{}/{}".format(
        str(creds.get("tenancy_ocid") or "").strip(),
        str(creds.get("user_ocid") or "").strip(),
        str(creds.get("fingerprint") or "").strip(),
    )
    headers = {
        "date": date,
        "accept": _JSON,
        "authorization": (
            'Signature version="1",keyId="{}",algorithm="rsa-sha256",'
            'headers="{}",signature="{}"'.format(key_id, " ".join(names), signature)
        ),
    }
    if body is not None:
        headers["x-content-sha256"] = base64.b64encode(
            hashlib.sha256(body).digest()).decode()
        headers["content-type"] = _JSON
        headers["content-length"] = str(len(body))
    return headers


def month_range(now: Optional[datetime] = None) -> tuple[str, str]:
    """[start of this month, start of next month) as UTC midnight timestamps —
    OCI refuses MONTHLY granularity for anything off a month boundary."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _rows(data: Any) -> list[dict]:
    """Строки ответа OCI: Core-ручки отдают ГОЛЫЙ массив, обёртки — `{"items": …}`."""
    raw = data.get("items") if isinstance(data, dict) else data
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def oci_reason(body: Any, fallback: str) -> str:
    """Причина отказа словами OCI: `{"code": "LimitExceeded", "message": …}`.

    Для заказа это не украшательство — «кончилась ёмкость в домене доступности»
    и «нет прав на компартмент» лечатся совершенно по-разному."""
    if isinstance(body, dict):
        message = str(body.get("message") or "").strip()
        if message:
            code = str(body.get("code") or "").strip()
            return f"{fallback}: {message}" + (f" ({code})" if code else "")
    return fallback


def pick_subnet(subnets: list[dict], ad: str) -> Optional[dict]:
    """Подсеть для VNIC: региональная (без домена доступности) либо ровно в `ad`.

    Публичные раньше приватных — сервер, до которого не достучаться, обычно не то,
    что заказывали. Сортировка стабильная, поэтому внутри группы сохраняется
    порядок вендора. Чистая функция: выбор проверяется тестом отдельно."""
    fits = [s for s in subnets
            if isinstance(s, dict) and s.get("id")
            and str(s.get("lifecycleState") or "AVAILABLE").upper() == "AVAILABLE"
            and (not str(s.get("availabilityDomain") or "").strip()
                 or str(s.get("availabilityDomain")).strip() == ad)]
    fits.sort(key=lambda s: bool(s.get("prohibitPublicIpOnVnic")))
    return fits[0] if fits else None


def _is_flex(row: dict) -> bool:
    """Гибкая форма: у неё задаются ядра и память отдельно от имени."""
    return bool(row.get("isFlexible")) or isinstance(row.get("ocpuOptions"), dict) \
        or str(row.get("shape") or "").lower().endswith(".flex")


def shape_plans(rows: list[dict]) -> tuple[list[OrderPlan], Optional[dict]]:
    """(планы, границы конструктора) из ответа `ListShapes`. Чистая функция.

    Формы повторяются по доменам доступности — дедуп по имени. `custom` отдаётся
    ТОЛЬКО когда среди форм есть гибкая: иначе конструктор в форме предлагал бы
    ядра и память там, где размер фиксирован именем."""
    plans: list[OrderPlan] = []
    seen: set[str] = set()
    has_flex = False
    cpu_max: Optional[float] = None
    ram_max: Optional[float] = None

    for row in rows:
        name = str(row.get("shape") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ocpus = _num(row.get("ocpus")) or 0.0
        memory = _num(row.get("memoryInGBs")) or 0.0
        if _is_flex(row):
            has_flex = True
            ocpu_opts = row.get("ocpuOptions") if isinstance(row.get("ocpuOptions"), dict) else {}
            mem_opts = row.get("memoryOptions") if isinstance(row.get("memoryOptions"), dict) else {}
            c_min = _num(ocpu_opts.get("min")) or 1.0
            c_max = _num(ocpu_opts.get("max"))
            r_min = _num(mem_opts.get("minInGBs")) or 1.0
            r_max = _num(mem_opts.get("maxInGBs"))
            cpu_max = c_max if cpu_max is None else max(cpu_max, c_max or cpu_max)
            ram_max = r_max if ram_max is None else max(ram_max, r_max or ram_max)
            specs = (f"гибкая: {c_min:g}–{c_max:g} OCPU" if c_max else
                     f"гибкая: от {c_min:g} OCPU")
            specs += (f" · {r_min:g}–{r_max:g} ГБ RAM" if r_max else
                      f" · от {r_min:g} ГБ RAM")
        else:
            specs = f"{ocpus:g} OCPU · {memory:g} ГБ RAM"
        processor = str(row.get("processorDescription") or "").strip()
        plans.append(OrderPlan(
            id=name,
            name=name,
            specs=f"{specs} · {processor}".strip(" ·"),
            # Цены у OCI в API нет — см. модуль-докстринг.
            price=None,
            currency="",
            period="hour",
        ))

    custom = None
    if has_flex:
        # Границы могут быть и неизвестны (вендор не опубликовал `*Options`) —
        # `max: None` значит именно это, а не «безлимит».
        custom = {
            "cpu": {"min": 1, "max": int(cpu_max) if cpu_max else None, "step": 1},
            "ram_gb": {"min": 1, "max": int(ram_max) if ram_max else None, "step": 1},
            "disk_gb": {"min": _BOOT_MIN_GB, "max": _BOOT_MAX_GB, "step": 1},
        }
    return plans, custom


class OracleAdapter(ProviderAdapter):
    KIND = "oracle"
    TITLE = "Oracle Cloud (OCI)"
    FIELDS = [
        CredField("tenancy_ocid", "OCID тенанта"),
        CredField("user_ocid", "OCID пользователя"),
        CredField("fingerprint", "Отпечаток ключа"),
        CredField("private_key", "Приватный ключ (PEM)", "textarea"),
        CredField("region", "Регион (например eu-frankfurt-1)"),
        CredField("compartment_id", "OCID компартмента (по умолчанию — тенант)",
                  "text", required=False),
    ]
    # No "balance": OCI is post-paid, the spend is reported through payments().
    CAPS = {"services", "payments", "order"}

    async def _request(self, creds: dict, method: str, url: str,
                       body: Optional[bytes] = None,
                       *, detail: bool = False) -> tuple[Any, str]:
        """`detail=True` добавляет к отказу слова вендора (нужно заказу; для
        читающих ручек текст остаётся прежним)."""
        pem = str((creds or {}).get("private_key") or "")
        try:
            headers = sign_headers(creds, method, url, body)
        except Exception as exc:  # unreadable/encrypted PEM, wrong key type
            return None, "не удалось прочитать приватный ключ: " + redact(str(exc), pem)

        try:
            async with self._client() as c:
                r = await c.request(method.upper(), url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return None, f"Oracle Cloud недоступен: {redact(str(exc), pem)}"

        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            fallback = map_http_error(r.status_code)
            return None, redact(oci_reason(data, fallback) if detail else fallback, pem)
        if data is None:
            return None, "Oracle Cloud вернул не-JSON ответ"
        return data, ""

    def _region(self, creds: dict) -> str:
        region = str((creds or {}).get("region") or "").strip().lower()
        return region if _REGION_RE.fullmatch(region) else ""

    def _compartment(self, creds: dict) -> str:
        return (str((creds or {}).get("compartment_id") or "").strip()
                or str((creds or {}).get("tenancy_ocid") or "").strip())

    def _core_url(self, region: str, path: str, params: dict) -> str:
        """URL Core-сервиса. Собирается ОДИН раз: подписывается и уходит он же."""
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        base = f"{_CORE_TPL.format(region=region)}{path}"
        return f"{base}?{query}" if query else base

    def _instances_url(self, creds: dict) -> str:
        region = self._region(creds)
        if not region:
            return ""
        return self._core_url(region, "/instances",
                              {"compartmentId": self._compartment(creds)})

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        url = self._instances_url(creds)
        if not url:
            return False, "регион указан неверно"
        _data, err = await self._request(creds, "GET", url)
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        url = self._instances_url(creds)
        if not url:
            return []
        data, err = await self._request(creds, "GET", url)
        if err:
            return []
        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            log.warning("oracle: unexpected /instances shape")
            return []
        return [_instance_item(raw) for raw in rows if isinstance(raw, dict)]

    async def payments(self, creds: dict) -> list[dict]:
        """Monthly spend as ONE record — OCI has no ledger of payments, only a
        usage aggregation, and summing it is the closest honest equivalent."""
        if self.check_fields(creds):
            return []
        region = self._region(creds)
        tenancy = str((creds or {}).get("tenancy_ocid") or "").strip()
        if not region or not tenancy:
            return []
        start, end = month_range()
        body = json.dumps({
            "tenantId": tenancy,
            "timeUsageStarted": start,
            "timeUsageEnded": end,
            "granularity": "MONTHLY",
        }).encode()
        data, err = await self._request(creds, "POST",
                                        _USAGE_TPL.format(region=region), body)
        if err or not isinstance(data, dict):
            return []
        items = data.get("items")
        if not isinstance(items, list):
            aggregation = data.get("usageAggregation")
            items = aggregation.get("items") if isinstance(aggregation, dict) else None
        if not isinstance(items, list):
            return []

        total = 0.0
        currency = ""
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                total += float(item.get("computedAmount") or 0)
            except (TypeError, ValueError):
                continue
            currency = currency or str(item.get("currency") or "")
        if not items:
            return []
        return [{
            "ts": start,
            "amount": round(total, 2),
            "currency": currency.upper(),
            "type": "charge",
            "note": "расход за месяц",
        }]

    # ── Заказ ──────────────────────────────────────────────────
    async def _availability_domains(self, creds: dict, region: str) -> list[str]:
        """Домены доступности региона. Пусто = ни одно написание хоста Identity
        не ответило; вызывающий обязан отказать, а не «взять первый попавшийся»."""
        tenancy = str((creds or {}).get("tenancy_ocid") or "").strip()
        if not tenancy:
            return []
        query = urllib.parse.urlencode({"compartmentId": tenancy})
        for tpl in _IDENTITY_TPLS:
            url = f"{tpl.format(region=region)}/availabilityDomains?{query}"
            data, err = await self._request(creds, "GET", url)
            if err:
                continue
            names = [str(r.get("name") or "").strip() for r in _rows(data)]
            names = [n for n in names if n]
            if names:
                return names
        log.warning("oracle: домены доступности региона %s не получены", region)
        return []

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        region = self._region(creds)
        if not region:
            return None
        compartment = self._compartment(creds)

        shapes, err = await self._request(
            creds, "GET", self._core_url(region, "/shapes",
                                         {"compartmentId": compartment}))
        if err:
            return None
        plans, custom = shape_plans(_rows(shapes))

        images, _img_err = await self._request(
            creds, "GET", self._core_url(region, "/images", {
                "compartmentId": compartment,
                "lifecycleState": "AVAILABLE",
                # Каталог платформенных образов исчисляется сотнями — без
                # ограничения форма превратилась бы в простыню.
                "limit": "100",
            }))

        ads = await self._availability_domains(creds, region)

        return OrderOptions(
            plans=plans,
            # Свободный селектор несёт ДОМЕН ДОСТУПНОСТИ (регион — в кредах).
            regions=[{"id": ad, "name": f"Домен доступности {ad}"} for ad in ads],
            images=[{"id": str(i.get("id") or ""),
                     "name": " ".join(x for x in (
                         str(i.get("displayName") or ""),
                         str(i.get("operatingSystem") or ""),
                         str(i.get("operatingSystemVersion") or "")) if x)
                     or str(i.get("id") or "")}
                    for i in _rows(images)
                    if i.get("id")
                    and str(i.get("lifecycleState") or "AVAILABLE").upper() == "AVAILABLE"],
            custom=custom,
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": ""}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}
        region = self._region(creds)
        if not region:
            return {**fail, "error": "регион указан неверно"}

        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        shape = str(spec.get("plan_id") or "").strip()
        image = str(spec.get("image") or "").strip()
        empty = [label for label, value in (
            ("имя сервера", name), ("форма (shape)", shape), ("образ", image),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        cpu = _num(spec.get("cpu"))
        ram = _num(spec.get("ram_gb"))
        disk = _num(spec.get("disk_gb"))
        flexible = shape.lower().endswith(".flex")
        if flexible and not (cpu and ram):
            return {**fail, "error": f"форма {shape} гибкая — укажите ядра (OCPU) "
                                     f"и память (ГБ)"}
        if disk and disk < _BOOT_MIN_GB:
            return {**fail, "error": f"загрузочный том в OCI не меньше "
                                     f"{_BOOT_MIN_GB} ГБ"}

        compartment = self._compartment(creds)
        subnets, err = await self._request(
            creds, "GET", self._core_url(region, "/subnets",
                                         {"compartmentId": compartment}))
        if err:
            return {**fail, "error": err}
        rows = _rows(subnets)
        if not rows:
            return {**fail, "error": "в компартменте нет подсетей — создайте VCN "
                                     "с подсетью, инстанс без VNIC не заказать"}

        # Домен доступности приезжает свободным селектором формы (см. докстринг).
        ad = str(spec.get("availability_domain") or spec.get("region") or "").strip()
        if ad:
            subnet = pick_subnet(rows, ad)
            if not subnet:
                return {**fail, "error": f"в домене доступности {ad} нет подсети "
                                         f"— выберите другой домен или создайте подсеть"}
        else:
            subnet = None
            for candidate in await self._availability_domains(creds, region):
                found = pick_subnet(rows, candidate)
                if found:
                    ad, subnet = candidate, found
                    break
            if not subnet:
                return {**fail, "error": "не нашли домен доступности с подсетью "
                                         "— выберите его явно в форме заказа"}

        details: dict = {"sourceType": "image", "imageId": image}
        if disk:
            details["bootVolumeSizeInGBs"] = int(round(disk))
        body: dict = {
            "compartmentId": compartment,
            "availabilityDomain": ad,
            "shape": shape,
            "displayName": name,
            "sourceDetails": details,
            # `assignPublicIp` не задаём: умолчание подсети всегда согласовано
            # с её типом, а явное значение на приватной подсети даёт 400.
            "createVnicDetails": {"subnetId": str(subnet.get("id") or "")},
        }
        if flexible:
            body["shapeConfig"] = {"ocpus": float(cpu), "memoryInGBs": float(ram)}

        # РОВНО один создающий запрос, без ретраев: таймаут не значит «не создано».
        data, err = await self._request(
            creds, "POST", self._core_url(region, "/instances", {}),
            json.dumps(body).encode(), detail=True)
        if err:
            return {**fail, "error": err}
        if not isinstance(data, dict) or not data.get("id"):
            return {**fail, "error": "OCI принял запрос, но не вернул инстанс "
                                     "— проверьте консоль перед повторной попыткой"}
        return {
            "ok": True,
            "id": str(data.get("id")),
            "name": str(data.get("displayName") or name),
            # Цену OCI не называет — маршрут требует отдельного подтверждения.
            "price": None,
            "currency": "",
            "error": "",
        }


def _instance_item(raw: dict) -> ServiceItem:
    iid = str(raw.get("id") or "")
    return ServiceItem(
        id=iid,
        name=str(raw.get("displayName") or "").strip() or f"instance {iid[-8:]}",
        kind=str(raw.get("shape") or "instance"),
        cost=None,
        # Hourly post-paid; per-instance price is not in the compute API.
        currency="",
        period="hour",
        status=str(raw.get("lifecycleState") or ""),
        # The address needs a separate VNIC-attachment lookup per instance
        # (two extra signed requests each) — not worth it for a list view.
        ip="",
        region=str(raw.get("availabilityDomain") or raw.get("region") or ""),
        paid_till="",
    )


ADAPTER = OracleAdapter()
ADAPTERS = [ADAPTER]
