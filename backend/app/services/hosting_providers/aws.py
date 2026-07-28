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
- **Списка услуг.** EC2 — другая служба (`ec2.<region>.amazonaws.com`, своя
  пагинация и свой регион у каждого инстанса), это отдельный адаптер, а не довесок
  к Cost Explorer. `services()` возвращает [].
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
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
                 payload: bytes, stamp: str) -> dict[str, str]:
    """Готовые заголовки подписанного POST-запроса к Cost Explorer."""
    headers = {
        "content-type": _CONTENT_TYPE,
        "host": host,
        "x-amz-date": stamp,
        "x-amz-target": _TARGET,
    }
    canon, signed = canonical_request("POST", "/", "", headers, payload)
    datestamp = stamp[:8]
    scope = f"{datestamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join([_ALGORITHM, stamp, scope, _sha256_hex(canon.encode())])
    signature = hmac.new(signing_key(secret, datestamp, region, _SERVICE),
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


class AwsAdapter(ProviderAdapter):
    KIND = "aws"
    TITLE = "AWS (Cost Explorer)"
    FIELDS = [
        CredField("access_key_id", "Access Key ID"),
        CredField("secret_access_key", "Secret Access Key", "password"),
        CredField("region", "Регион (по умолчанию us-east-1)", "text", required=False),
    ]
    # Ни баланса, ни списка услуг у Cost Explorer нет — см. модульный докстринг.
    CAPS = {"payments"}

    def _region(self, creds: dict) -> str:
        region = str((creds or {}).get("region") or "").strip().lower() or _DEFAULT_REGION
        return region if _REGION_RE.fullmatch(region) else ""

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
