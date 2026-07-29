"""Beget adapter — account balance only (Wave-9 Plan C, Ф2).

`GET https://api.beget.com/api/user/getAccountInfo?login=&passwd=&output_format=json`

Two things make this adapter unusual:

- **The credentials are the control-panel login and password**, not an API token.
  The UI must say so (amber notice) — a leak here is the whole hosting account.
  Beget wants them as QUERY parameters, so the URL itself is a secret: nothing
  here logs a URL, and every returned string goes through `redact()` (which also
  masks the percent-encoded form the query string produces).
- **The envelope is DOUBLE**: `{status, answer: {status, result: {...}}}`. Either
  level can carry `status: "error"` with an error text, and the vendor spells the
  key differently per level (`error_text` outer, `errortext` inner) — both
  spellings are accepted so a rename on one level doesn't read as success.

Payments: no API.

⚠️ **У Beget ДВА разных API, и этот модуль ходит в оба.**

- Старый (баланс выше) — `api.beget.com/api/...`, логин и пароль в query, двойной
  конверт.
- Новый «облачный» — `api.beget.com/v1/...`, **JWT**: `POST /v1/auth`
  `{login, password}` отдаёт `{"token": …}`, дальше `Authorization: Bearer …`.
  Тем же логином и паролем, поэтому лишнего поля в форме не появилось. Токен
  кэшируется в памяти процесса (`_SESSIONS`): вход по паролю — самое неудачное
  место, чтобы ловить лимиты вендора.
  ⚠️ При включённой у аккаунта 2FA вход по логину и паролю невозможен вовсе
  (`CODE_REQUIRED_*`) — это отдельное сообщение, а не «неверный пароль».

Список серверов и заказ живут в новом API (`LTD-Beget/vps`, официальные proto):

- Услуги — `GET /v1/vps/server/list` (`{vps: [...], total_count}`), пагинация
  `offset`/`limit`.
- Каталог — `GET /v1/vps/configuration` (`{configurations: [...]}`);
  ⚠️ **`memory` и `disk_size` у Beget в МЕГАБАЙТАХ**, а наш контракт в
  гигабайтах — весь обмен идёт через `_mb_to_gb`/`_gb_to_mb`. Ошибка здесь
  означает сервер в 1024 раза не того размера.
- Конструктор — `GET /v1/vps/configurator/info` (границы, тоже в МБ) и
  `GET /v1/vps/configurator/calculation` (**штатный предварительный расчёт**:
  ничего не создаёт и возвращает `price_month`) — это и есть `quote_order`.
- Создание — `POST /v1/vps/server`: `configuration_id` ЛИБО
  `configuration_params` (вместе нельзя), источник диска — `software.id`
  (числовой `image`) либо `image_id` (строковый).
- ⚠️ **Сервер без способа доступа Beget не создаёт** (`INVALID_SECURITY_
  CONFIGURATION`: «не выбран ни логин, ни доступ по ключу»). Пароль мы не
  генерируем и наружу не отдаём — выдать пользователю сервер, в который он не
  войдёт, хуже отказа. Поэтому к заказу прикладываются SSH-ключи АККАУНТА
  (`GET /v1/vps/sshKey`), а если их нет — отказ ДО создания с просьбой завести
  ключ в панели.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.beget")

_URL = "https://api.beget.com/api/user/getAccountInfo"
_BASE = "https://api.beget.com"

_PER_PAGE = 100
_MAX_PAGES = 5

# JWT живёт долго, но кэш держим скромно: протухший токен виден как 401, а
# лишний вход по паролю — как подозрительная активность у вендора.
# ⚠️ Ключ — sha256(логин + пароль), а НЕ один логин: логин не секрет, и по
# логин-ключу чужой аккаунт панели подобрал бы себе готовый токен, введя
# правильный логин и любой пароль. Приём взят из `billmanager.py`.
_TOKEN_TTL = 50 * 60
_SESSIONS: dict[str, tuple[str, float]] = {}


def _cache_key(login: str, password: str) -> str:
    raw = "\0".join((login, password)).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()

# Текст вендора для случаев, где общая фраза по коду ничего не объясняет.
_AUTH_ERRORS = {
    "INCORRECT_CREDENTIALS": "неверные креды",
    "EMPTY_LOGIN": "не заполнено: логин",
    "EMPTY_PASSWORD": "не заполнено: пароль",
    "IP_BLOCKED": "Beget запретил доступ с этого IP-адреса",
    "CODE_REQUIRED": "у аккаунта включена двухфакторная аутентификация — "
                     "вход по логину и паролю через API недоступен",
}


def _num(raw: Any) -> Optional[float]:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _int(raw: Any) -> Optional[int]:
    value = _num(raw)
    return None if value is None else int(value)


def _mb_to_gb(raw: Any) -> Optional[float]:
    value = _num(raw)
    return None if value is None else round(value / 1024.0, 3)


def _gb_to_mb(raw: Any) -> Optional[int]:
    value = _num(raw)
    return None if value is None else int(round(value * 1024.0))


def _hostname(name: str) -> str:
    """Имя хоста из отображаемого имени: вендор отвергает всё, что не похоже на
    hostname (`INVALID_HOSTNAME`), а свободное имя формы им быть не обязано."""
    clean = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    return (clean[:63] or "vps").strip("-") or "vps"


def _envelope_error(node: Any) -> str:
    """Error text of one envelope level, "" if that level looks fine."""
    if not isinstance(node, dict):
        return "неожиданный формат ответа Beget"
    if str(node.get("status") or "").lower() != "error":
        return ""
    for key in ("errortext", "error_text", "errorcode", "error_code"):
        text = str(node.get(key) or "").strip()
        if text:
            return text
    return "Beget вернул ошибку без описания"


class BegetAdapter(ProviderAdapter):
    KIND = "beget"
    TITLE = "Beget"
    FIELDS = [
        CredField("login", "Логин личного кабинета"),
        CredField("password", "Пароль личного кабинета", "password"),
    ]
    CAPS = {"balance", "services", "order"}

    async def _account_info(self, creds: dict) -> tuple[Optional[dict], str]:
        """→ (`answer.result`, error). Unwraps both envelope levels."""
        login = str((creds or {}).get("login") or "").strip()
        password = str((creds or {}).get("password") or "")
        try:
            async with self._client() as c:
                r = await c.get(_URL, params={
                    "login": login, "passwd": password, "output_format": "json",
                })
        except httpx.HTTPError as exc:
            return None, f"Beget недоступен: {redact(str(exc), login, password)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "Beget вернул не-JSON ответ"

        err = _envelope_error(data)
        if err:
            return None, redact(err, login, password)
        answer = data.get("answer")
        err = _envelope_error(answer)
        if err:
            return None, redact(err, login, password)
        result = answer.get("result")
        if not isinstance(result, dict):
            return None, "неожиданный формат ответа Beget"
        return result, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _result, err = await self._account_info(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        result, err = await self._account_info(creds)
        if err or not result:
            return None
        try:
            return Balance(float(result["user_balance"]), "RUB")
        except (KeyError, TypeError, ValueError):
            log.warning("beget: unexpected getAccountInfo result shape")
            return None

    # ── Новый (облачный) API: JWT ──────────────────────────────
    async def _token(self, creds: dict) -> tuple[str, str]:
        """JWT для `api.beget.com/v1/...` → (токен, ошибка). Кэш на процесс."""
        login = str((creds or {}).get("login") or "").strip()
        password = str((creds or {}).get("password") or "")
        slot = _cache_key(login, password)
        cached = _SESSIONS.get(slot)
        if cached and cached[1] > time.monotonic():
            return cached[0], ""

        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}/v1/auth",
                                 json={"login": login, "password": password},
                                 headers={"Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            return "", f"Beget недоступен: {redact(str(exc), login, password)}"

        try:
            data = r.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("error"):
            code = str(data["error"]).strip().upper()
            # `CODE_REQUIRED_EMAIL`/`_SMS`/`_TOTP` — один и тот же случай 2FA.
            key = "CODE_REQUIRED" if code.startswith("CODE_REQUIRED") else code
            return "", _AUTH_ERRORS.get(key, redact(code, login, password))
        if r.status_code >= 400:
            return "", map_http_error(r.status_code)
        token = str((data or {}).get("token") or "").strip() if isinstance(data, dict) else ""
        if not token:
            return "", "Beget не вернул токен"
        _SESSIONS[slot] = (token, time.monotonic() + _TOKEN_TTL)
        return token, ""

    async def _api_get(self, creds: dict, path: str,
                       params: Optional[dict] = None) -> tuple[Any, str]:
        token, err = await self._token(creds)
        if err:
            return None, err
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", params=params, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"Beget недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            if r.status_code in (401, 403):
                # Протухший токен не должен превращаться в «неверные креды»
                # навсегда: следующий вызов войдёт заново.
                _SESSIONS.pop(_cache_key(
                    str((creds or {}).get("login") or "").strip(),
                    str((creds or {}).get("password") or "")), None)
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Beget вернул не-JSON ответ"

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        out: list[ServiceItem] = []
        offset = 0
        for _page in range(_MAX_PAGES):
            data, err = await self._api_get(creds, "/v1/vps/server/list",
                                            {"limit": _PER_PAGE, "offset": offset})
            if err or not isinstance(data, dict):
                break
            rows = data.get("vps")
            if not isinstance(rows, list):
                log.warning("beget: unexpected /v1/vps/server/list shape")
                break
            for raw in rows:
                if isinstance(raw, dict):
                    out.append(_vps_item(raw))
            offset += len(rows)
            total = _int(data.get("total_count"))
            if not rows or total is None or offset >= total:
                break
        return out

    # ── Заказ ──────────────────────────────────────────────────
    async def _configurations(self, creds: dict) -> tuple[list[dict], str]:
        data, err = await self._api_get(creds, "/v1/vps/configuration")
        if err or not isinstance(data, dict):
            return [], err or "Beget вернул неожиданный каталог конфигураций"
        rows = data.get("configurations")
        return ([c for c in rows if isinstance(c, dict)] if isinstance(rows, list) else []), ""

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        configs, err = await self._configurations(creds)
        if err:
            return None

        plans: list[OrderPlan] = []
        regions: dict[str, dict] = {}
        for raw in configs:
            cid = str(raw.get("id") or "").strip()
            if not cid or raw.get("available") is False:
                continue
            region = str(raw.get("region") or "").strip()
            ram_gb = _mb_to_gb(raw.get("memory")) or 0
            disk_gb = _mb_to_gb(raw.get("disk_size")) or 0
            specs = (f"{_int(raw.get('cpu_count')) or 0} vCPU · {ram_gb:g} ГБ RAM · "
                     f"{disk_gb:g} ГБ").strip()
            bandwidth = _int(raw.get("bandwidth_public"))
            if bandwidth:
                specs += f" · {bandwidth} Мбит/с"
            plans.append(OrderPlan(
                id=cid,
                name=str(raw.get("name") or cid),
                specs=specs,
                price=_num(raw.get("price_month")),
                currency="RUB",
                period="month",
                region=region,
            ))
            if region:
                regions.setdefault(region, {"id": region, "name": region,
                                            "configuration_ids": []})
                regions[region]["configuration_ids"].append(cid)

        software, _sw_err = await self._api_get(
            creds, "/v1/vps/marketplace/software/list")
        images: list[dict] = []
        rows = (software or {}).get("software") if isinstance(software, dict) else None
        for raw in (rows if isinstance(rows, list) else []):
            if not isinstance(raw, dict) or raw.get("is_available") is False:
                continue
            sid = _int(raw.get("id"))
            if sid is None:
                continue
            req = raw.get("requirements") if isinstance(raw.get("requirements"), dict) else {}
            images.append({
                "id": str(sid),
                "name": " ".join(x for x in (str(raw.get("display_name") or ""),
                                             str(raw.get("version") or "")) if x).strip()
                        or str(raw.get("name") or f"ПО {sid}"),
                # Требования едут с образом: у тяжёлого ПО порог выше, чем у
                # голой ОС, и общий «пол» конструктора этого не выражает.
                "min_cpu": _int(req.get("cpu_count")) or 0,
                "min_ram_gb": _mb_to_gb(req.get("memory")) or 0,
                "min_disk_gb": _mb_to_gb(req.get("disk_size")) or 0,
            })

        info, _info_err = await self._api_get(creds, "/v1/vps/configurator/info")
        return OrderOptions(plans=plans, regions=list(regions.values()),
                            images=images, custom=_custom_ranges(info))

    async def _order_body(self, creds: dict, spec: dict) -> tuple[Optional[dict], str]:
        """Тело `POST /v1/vps/server` по спецификации формы."""
        missing = self.check_fields(creds)
        if missing:
            return None, missing

        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        image = str(spec.get("image") or "").strip()
        if not name or not image:
            empty = ([] if name else ["имя сервера"]) + ([] if image else ["образ/ПО"])
            return None, "не заполнено: " + ", ".join(empty)

        body: dict = {"display_name": name, "hostname": _hostname(name)}
        software_id = _int(image)
        # `software.id` и `image_id` — разные ветки одного `oneof`: числовой
        # идентификатор относится к каталогу ПО, строковый — к образу.
        if software_id is not None:
            body["software"] = {"id": software_id}
        else:
            body["image_id"] = image

        plan_id = str(spec.get("plan_id") or "").strip()
        if plan_id:
            # Тариф и конструктор вместе слать нельзя — непустой тариф выигрывает.
            body["configuration_id"] = plan_id
        else:
            cpu = _int(spec.get("cpu"))
            memory = _gb_to_mb(spec.get("ram_gb"))
            disk = _gb_to_mb(spec.get("disk_gb"))
            empty = [label for label, value in (
                ("CPU", cpu), ("RAM", memory), ("диск", disk),
            ) if value is None]
            if empty:
                return None, "не заполнено: " + ", ".join(empty)
            body["configuration_params"] = {
                "cpu_count": cpu, "memory": memory, "disk_size": disk}
        region = str(spec.get("region") or "").strip()
        if region:
            body["region"] = region
        return body, ""

    async def _ssh_keys(self, creds: dict) -> tuple[list[int], str]:
        """Ключи аккаунта — единственный способ доступа, который мы задаём.

        Пароль пришлось бы сгенерировать и не вернуть (в контракте заказа поля
        для секрета нет), поэтому без ключей заказ не собирается."""
        data, err = await self._api_get(creds, "/v1/vps/sshKey")
        if err:
            return [], err
        rows = (data or {}).get("keys") if isinstance(data, dict) else None
        ids = [_int(k.get("id")) for k in (rows if isinstance(rows, list) else [])
               if isinstance(k, dict)]
        return [i for i in ids if i is not None], ""

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Стоимость конфигурации БЕЗ создания сервера.

        У тарифа сумма уже есть в каталоге, а для конструктора её называет
        штатный расчёт вендора (`configurator/calculation`)."""
        if self.check_fields(creds):
            return None
        spec = spec or {}
        plan_id = str(spec.get("plan_id") or "").strip()
        if plan_id:
            configs, err = await self._configurations(creds)
            if err:
                return None
            for raw in configs:
                if str(raw.get("id") or "") != plan_id:
                    continue
                price = _num(raw.get("price_month"))
                return {"price": price, "currency": "RUB"} if price is not None else None
            return None

        cpu = _int(spec.get("cpu"))
        memory = _gb_to_mb(spec.get("ram_gb"))
        disk = _gb_to_mb(spec.get("disk_gb"))
        if cpu is None or memory is None or disk is None:
            return None
        params = {"params.cpu_count": cpu, "params.memory": memory,
                  "params.disk_size": disk}
        region = str(spec.get("region") or "").strip()
        if region:
            params["region"] = region
        data, err = await self._api_get(creds, "/v1/vps/configurator/calculation", params)
        if err or not isinstance(data, dict):
            return None
        success = data.get("success")
        price = _num((success or {}).get("price_month")) if isinstance(success, dict) else None
        return {"price": price, "currency": "RUB"} if price is not None else None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "RUB"}
        body, err = await self._order_body(creds, spec)
        if err or body is None:
            return {**fail, "error": err or "не удалось собрать заказ"}

        keys, keys_err = await self._ssh_keys(creds)
        if keys_err:
            return {**fail, "error": keys_err}
        if not keys:
            return {**fail, "error": "у аккаунта Beget нет SSH-ключей, а сервер без "
                                     "способа доступа вендор не создаёт: добавьте ключ "
                                     "в панели и повторите заказ"}
        body["ssh_keys"] = keys

        token, token_err = await self._token(creds)
        if token_err:
            return {**fail, "error": token_err}
        # РОВНО один POST, без ретраев: создание сервера тратит деньги, и таймаут
        # не означает «не создано».
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}/v1/vps/server", json=body, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return {**fail, "error": f"Beget недоступен: {redact(str(exc), token)}"}

        try:
            data = r.json()
        except ValueError:
            data = None
        reason = _vps_error(data)
        if reason:
            return {**fail, "error": redact(reason, token)}
        if r.status_code >= 400:
            return {**fail, "error": map_http_error(r.status_code)}
        vps = (data or {}).get("vps") if isinstance(data, dict) else None
        if not isinstance(vps, dict) or not str(vps.get("id") or "").strip():
            # Заказ мог пройти: молчаливое «не получилось» опаснее просьбы
            # заглянуть в панель.
            return {**fail, "error": "Beget принял запрос, но не вернул сервер "
                                     "— проверьте панель перед повторной попыткой"}
        price = _num(((vps.get("configuration") or {}) if isinstance(
            vps.get("configuration"), dict) else {}).get("price_month"))
        return {
            "ok": True,
            "id": str(vps.get("id")),
            "name": str(vps.get("display_name") or body["display_name"]),
            "price": price,
            "currency": "RUB",
            "error": "",
        }


def _vps_error(data: Any) -> str:
    """Текст отказа облачного API: он приезжает и с HTTP 200 — в поле `error`
    ответа (`{"error": {"code": …, "message": …}}`), а транспортные ошибки
    grpc-gateway кладут `message` в корень."""
    if not isinstance(data, dict):
        return ""
    node = data.get("error")
    if isinstance(node, dict):
        return (str(node.get("message") or "").strip()
                or str(node.get("code") or "").strip()
                or "Beget отклонил заказ без описания")
    if isinstance(node, str) and node.strip():
        return node.strip()
    if "vps" not in data and str(data.get("message") or "").strip():
        return str(data["message"]).strip()
    return ""


def _custom_ranges(info: Any) -> Optional[dict]:
    """Границы конструктора (МБ вендора → ГБ контракта).

    `is_available: false` означает, что конструктор сейчас закрыт — тогда формы
    конструктора быть не должно, остаются готовые тарифы."""
    if not isinstance(info, dict) or info.get("is_available") is False:
        return None
    settings = info.get("settings")
    if not isinstance(settings, dict):
        return None
    # (ключ схемы, ключ вендора, конвертер значения, конвертер шага)
    axes = (("cpu", "cpu_settings", _num, _num),
            ("ram_gb", "memory_settings", _mb_to_gb, _mb_to_gb),
            ("disk_gb", "disk_settings", _mb_to_gb, _mb_to_gb))
    out: dict[str, dict] = {}
    for key, vendor_key, conv, step_conv in axes:
        node = settings.get(vendor_key)
        if not isinstance(node, dict):
            continue
        # `available_range` — то, что реально можно заказать сейчас; `range` —
        # общие границы. Берём доступное, если вендор его прислал.
        rng = node.get("available_range") if isinstance(
            node.get("available_range"), dict) else node.get("range")
        rng = rng if isinstance(rng, dict) else {}
        out[key] = {
            "min": conv(rng.get("min")),
            "max": conv(rng.get("max")),
            "step": step_conv(node.get("step")) or 1,
        }
    return out or None


def _vps_item(raw: dict) -> ServiceItem:
    conf = raw.get("configuration") if isinstance(raw.get("configuration"), dict) else {}
    vid = str(raw.get("id") or "")
    return ServiceItem(
        id=vid,
        name=str(raw.get("display_name") or raw.get("hostname") or "").strip()
             or f"VPS {vid}",
        kind=str(conf.get("name") or "vps"),
        cost=_num(conf.get("price_month")),
        currency="RUB",
        period="month",
        status=str(raw.get("status") or ""),
        ip=str(raw.get("ip_address") or ""),
        region=str(conf.get("region") or ""),
        # Почасовая модель: предоплаченного «оплачено до» нет.
        paid_till="",
    )


ADAPTER = BegetAdapter()
