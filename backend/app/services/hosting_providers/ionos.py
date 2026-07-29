"""IONOS — выделенный Billing API (счета).

База `https://api.ionos.com/billing/v3`, эндпоинт `/invoices`.

**Главная особенность — двойная авторизация.** IONOS принимает и **Bearer**
(токен из DCD → Token Manager), и **Basic** (e-mail + пароль личного кабинета).
Поэтому все три поля объявлены НЕобязательными, а способ выбирается по тому, что
заполнено: `check_fields()` тут не годится (он требует все `required`), проверку
делает `_auth_kwargs()` — ни токена, ни пары логин+пароль → «заполните токен или
логин с паролем».

**Баланса нет и он не заявлен в CAPS.** IONOS — постоплатная контрактная модель:
в billing-API публикуются счета, а не остаток средств. Достоверного поля остатка
не нашлось, поэтому `balance()` отдаёт `None` — UI покажет честное «баланс
вводится вручную», а не «синхронизация не удалась».

**Потребление ресурсов НЕ читается.** Отдельная ручка потребления в billing-API
есть по описанию вендора, но её точный путь и форма на живом контракте не
снимались. Угаданный URL дал бы пустой список, неотличимый от «расхода нет», —
это хуже отсутствия функции. Место для доработки после разведки на живом
контракте (тогда же проверить и `balance`).

⚠️ Форма ответа тоже не снималась: суммы/даты читаются из списка правдоподобных
имён (приём из `veesp`/`aeza`), а коллекции IONOS традиционно заворачивают строку
в `properties` — это снимается. Незнакомая форма даёт `[]` и warning в лог.

Заказ (`order`) идёт в ДРУГОЙ API того же вендора — Cloud API v6
(`https://api.ionos.com/cloudapi/v6`), авторизация та же (Bearer или Basic).
Сервер создаётся ВНУТРИ датацентра, и одного объекта сервера мало: без тома он
не загрузится, без NIC — не будет доступен. Cloud API умеет составное создание
(`entities.volumes` + `entities.nics` прямо в теле), поэтому создающий запрос
всё-таки ОДИН: `POST /datacenters/{id}/servers`, ответ **202**.

Что из-за этого устроено не как у других адаптеров:

- **Фиксированных тарифов у IONOS нет вовсе** — сервер задаётся ядрами, памятью
  и размером тома. Поэтому `plans` пуст, а каталог отдаёт `custom`
  (конструктор); границы берём из `/contracts` (лимиты договора), а не из
  головы, и при недоступности ручки честно оставляем `max: None`.
- **⚠️ Свободный селектор формы (`regions`) несёт ДАТАЦЕНТР**, а не локацию:
  сервер создаётся в конкретном датацентре, а его id в общем `spec` не
  предусмотрен. Тот же приём, что у `openstack` (там в этом селекторе сеть).
- **⚠️ Публичный образ требует пароль ЛИБО SSH-ключ.** Пароль отдавать наружу
  нельзя (в контракте заказа поля для секрета нет), поэтому нужен публичный
  SSH-ключ — он живёт в кредах провайдера. Ключа нет → отказ БЕЗ запроса:
  сервер, в который нельзя войти, оплачивается так же, как рабочий.
- **⚠️ LAN не выдумывается.** NIC ссылается на существующий LAN датацентра;
  берём публичный (иначе у сервера не будет интернета), при его отсутствии —
  первый попавшийся, а если LAN нет вовсе — отказ до создающего запроса.
- Цену Cloud API не называет ни в каталоге, ни в ответе: `price=None`,
  `quote_order` не реализован — маршрут покупки требует отдельного подтверждения
  «сумма заранее неизвестна».
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    OrderOptions,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.ionos")

_BASE = "https://api.ionos.com/billing/v3"
# Заказ живёт в другом API того же вендора (см. докстринг).
_CLOUD = "https://api.ionos.com/cloudapi/v6"

# Тип тома по умолчанию: HDD принимают все датацентры, SSD — не везде и дороже.
_DISK_TYPE = "HDD"
# Каталог публичных образов у IONOS исчисляется сотнями — форме столько не нужно.
_MAX_IMAGES = 200

_TS_KEYS = ("invoiceDate", "documentDate", "issueDate", "date", "createdDate",
            "created", "createdAt")
_AMOUNT_KEYS = ("totalGross", "grossAmount", "amountGross", "total", "amount",
                "sum", "totalNet", "netAmount")
_CURRENCY_KEYS = ("currency", "currencyCode", "currency_code")
_NOTE_KEYS = ("documentNumber", "invoiceNumber", "number", "description",
              "status", "id")


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in node:
            found = _num(node[key])
            if found is not None:
                return found
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        text = str(node.get(key) or "").strip()
        if text:
            return text
    return default


def _secrets(creds: dict) -> tuple[str, ...]:
    creds = creds or {}
    return (str(creds.get("token") or ""), str(creds.get("password") or ""))


def _items(payload: Any) -> list[dict]:
    """Строки коллекции Cloud API: всегда `{"items": [...]}`, но голый список
    тоже принимаем — дешевле, чем ловить смену формы отказом."""
    raw = payload.get("items") if isinstance(payload, dict) else payload
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def contract_limits(payload: Any) -> tuple[Optional[int], Optional[float]]:
    """(ядер на сервер, ГБ памяти на сервер) из `/contracts`. Чистая функция.

    `None` = «лимит не прочитали» (семантика `OrderOptions.custom`), а не
    «безлимит»: последнее слово в любом случае за проверкой вендора."""
    rows = _items(payload)
    if not rows and isinstance(payload, dict) and payload.get("properties"):
        rows = [payload]  # одиночный договор приходит объектом
    for row in rows:
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        limits = props.get("resourceLimits")
        if not isinstance(limits, dict):
            continue
        cores = _num(limits.get("coresPerServer"))
        ram_mb = _num(limits.get("ramPerServer"))
        return (int(cores) if cores else None,
                round(ram_mb / 1024, 0) if ram_mb else None)
    return None, None


def pick_lan(rows: list[dict]) -> Optional[int]:
    """LAN для NIC: публичный раньше приватного, идентификатор — ЧИСЛО.

    Публичный выбирается не из вкусовщины: в приватном LAN у заказанного сервера
    не будет выхода в интернет, а платить за него придётся так же."""
    numeric: list[tuple[bool, int]] = []
    for row in rows:
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        try:
            lan_id = int(str(row.get("id") or "").strip())
        except (TypeError, ValueError):
            continue
        numeric.append((not bool(props.get("public")), lan_id))
    if not numeric:
        return None
    numeric.sort(key=lambda pair: pair[0])  # стабильна: порядок вендора внутри группы
    return numeric[0][1]


def _auth_kwargs(creds: dict) -> tuple[dict, str]:
    """httpx-kwargs выбранного способа авторизации, либо причина отказа."""
    creds = creds or {}
    token = str(creds.get("token") or "").strip()
    username = str(creds.get("username") or "").strip()
    password = str(creds.get("password") or "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        return {"headers": headers}, ""
    if username and password:
        return {"headers": headers, "auth": (username, password)}, ""
    return {}, "заполните токен или логин с паролем"


def _rows(payload: Any) -> list[dict]:
    """Строки коллекции из любой из трёх виденных у IONOS форм."""
    raw = payload
    if isinstance(payload, dict):
        for key in ("items", "invoices", "data", "content"):
            found = payload.get(key)
            if isinstance(found, list):
                raw = found
                break
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        # Коллекции IONOS кладут поля записи внутрь `properties`.
        props = row.get("properties")
        out.append({**row, **props} if isinstance(props, dict) else row)
    return out


def ionos_reason(body: Any, fallback: str) -> str:
    """Причина отказа словами IONOS: `{"messages": [{"errorCode", "message"}]}`.

    Для заказа существенно: «нет квоты по ядрам» и «образ недоступен в этой
    локации» выглядят одинаковым 422, а лечатся по-разному."""
    if isinstance(body, dict):
        messages = body.get("messages")
        if isinstance(messages, list):
            texts = [str((m or {}).get("message") or "").strip()
                     for m in messages if isinstance(m, dict)]
            texts = [t for t in texts if t]
            if texts:
                return f"{fallback}: " + "; ".join(texts[:3])
        single = str(body.get("message") or "").strip()
        if single:
            return f"{fallback}: {single}"
    return fallback


class IonosAdapter(ProviderAdapter):
    KIND = "ionos"
    TITLE = "IONOS"
    FIELDS = [
        CredField("token", "API-токен (Token Manager)", "password", required=False),
        CredField("username", "E-mail личного кабинета", "text", required=False),
        CredField("password", "Пароль личного кабинета", "password", required=False),
        # Нужен только заказу: публичный образ IONOS не создаётся без пароля или
        # ключа, а пароль возвращать наружу контракт заказа не позволяет.
        CredField("ssh_public_key", "Публичный SSH-ключ (для заказа сервера)",
                  "textarea", required=False),
    ]
    # Без "balance": остатка средств в billing-API нет (см. докстроку).
    CAPS = {"payments", "order"}

    async def _request(self, creds: dict, method: str, url: str,
                       body: Optional[dict] = None,
                       *, detail: bool = False) -> tuple[Any, str]:
        kwargs, err = _auth_kwargs(creds)
        if err:
            return None, err
        if body is not None:
            kwargs["json"] = body
        try:
            async with self._client() as c:
                r = await c.request(method.upper(), url, **kwargs)
        except httpx.HTTPError as exc:
            return None, "IONOS недоступен: " + redact(str(exc), *_secrets(creds))

        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            fallback = map_http_error(r.status_code)
            return None, redact(ionos_reason(data, fallback) if detail else fallback,
                                *_secrets(creds))
        if data is None:
            return None, "IONOS вернул не-JSON ответ"
        return data, ""

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        return await self._request(creds, "GET", f"{_BASE}{path}")

    async def verify(self, creds: dict) -> tuple[bool, str]:
        _data, err = await self._get(creds, "/invoices")
        return (False, err) if err else (True, "")

    async def payments(self, creds: dict) -> list[dict]:
        data, err = await self._get(creds, "/invoices")
        if err:
            return []
        rows = _rows(data)
        if not rows:
            if data:
                log.warning("ionos: незнакомая форма ответа /invoices")
            return []
        out: list[dict] = []
        for raw in rows:
            out.append({
                "ts": _pick_str(raw, _TS_KEYS),
                "amount": _pick_number(raw, _AMOUNT_KEYS),
                "currency": _pick_str(raw, _CURRENCY_KEYS, "EUR").upper(),
                # Счёт — это всегда начисление; возвратов в /invoices не бывает.
                "type": "charge",
                "note": _pick_str(raw, _NOTE_KEYS),
            })
        return out

    # ── Заказ (Cloud API v6) ───────────────────────────────────
    async def _cloud(self, creds: dict, path: str) -> tuple[Any, str]:
        return await self._request(creds, "GET", f"{_CLOUD}{path}")

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        datacenters, err = await self._cloud(creds, "/datacenters?depth=1")
        if err:
            return None
        regions: list[dict] = []
        for row in _items(datacenters):
            dc_id = str(row.get("id") or "").strip()
            props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
            if not dc_id:
                continue
            location = str(props.get("location") or "").strip()
            title = str(props.get("name") or dc_id)
            regions.append({
                "id": dc_id,
                # Имя начинается со слова «Датацентр» намеренно: форма подписывает
                # этот селектор как регион, а выбирается здесь датацентр.
                "name": f"Датацентр {title}" + (f" ({location})" if location else ""),
                "location": location,
            })
        if not regions:
            log.warning("ionos: у аккаунта нет датацентров — заказывать некуда")
            return None

        images_raw, _img_err = await self._cloud(creds, "/images?depth=1")
        images: list[dict] = []
        for row in _items(images_raw):
            props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
            if not row.get("id") or not props.get("public"):
                continue
            if str(props.get("imageType") or "").upper() != "HDD":
                continue
            if str(props.get("licenceType") or "").upper() not in ("LINUX", "UNKNOWN"):
                continue
            images.append({
                "id": str(row.get("id")),
                "name": str(props.get("name") or row.get("id")),
                # Локация образа обязана совпасть с локацией датацентра, а какой
                # выберут — на этом шаге неизвестно: отдаём форме обе метки.
                "location": str(props.get("location") or ""),
                "min_disk_gb": _num(props.get("size")),
            })
        images.sort(key=lambda i: i["name"].lower())
        del images[_MAX_IMAGES:]

        contracts, _c_err = await self._cloud(creds, "/contracts")
        cores_max, ram_max = contract_limits(contracts)
        return OrderOptions(
            # Готовых тарифов у IONOS нет: сервер задаётся конструктором.
            plans=[],
            regions=regions,
            images=images,
            custom={
                "cpu": {"min": 1, "max": cores_max, "step": 1},
                "ram_gb": {"min": 1, "max": ram_max, "step": 1},
                # Минимум тома вендор не публикует, а образу нужен свой размер —
                # он уезжает в `min_disk_gb` образа, а не в выдуманный потолок.
                "disk_gb": {"min": 1, "max": None, "step": 1},
            },
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": ""}
        _kwargs, err = _auth_kwargs(creds)
        if err:
            return {**fail, "error": err}

        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        # Датацентр приезжает свободным селектором формы (см. докстринг).
        datacenter = str(spec.get("datacenter_id") or spec.get("region") or "").strip()
        image = str(spec.get("image") or "").strip()
        cpu = _num(spec.get("cpu"))
        ram = _num(spec.get("ram_gb"))
        disk = _num(spec.get("disk_gb"))
        empty = [label for label, value in (
            ("имя сервера", name), ("датацентр", datacenter), ("образ", image),
            ("ядра", cpu), ("память (ГБ)", ram), ("диск (ГБ)", disk),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        ssh_key = str((creds or {}).get("ssh_public_key") or "").strip()
        if not ssh_key:
            return {**fail, "error": "IONOS не создаёт сервер из публичного образа "
                                     "без пароля или SSH-ключа: заполните «Публичный "
                                     "SSH-ключ» в кредах провайдера"}

        # Идентификатор датацентра идёт сегментом пути — квотируем.
        dc_path = urllib.parse.quote(datacenter, safe="")
        lans, err = await self._cloud(creds, f"/datacenters/{dc_path}/lans?depth=1")
        if err:
            return {**fail, "error": err}
        lan_id = pick_lan(_items(lans))
        if lan_id is None:
            return {**fail, "error": "в датацентре нет LAN — создайте публичный LAN, "
                                     "иначе сервер останется без сети"}

        body = {
            "properties": {
                "name": name,
                "cores": int(cpu),
                # RAM у IONOS в МЕГАбайтах и кратна 256; целые (и четвертные) ГБ
                # этому отвечают. Округляем, а не отбрасываем дробь: молча выдать
                # 1 ГБ вместо запрошенных 1.5 — это не та машина, за которую платят.
                "ram": int(round(ram * 1024)),
            },
            "entities": {
                "volumes": {"items": [{"properties": {
                    "name": f"{name}-boot",
                    "size": int(round(disk)),
                    "type": _DISK_TYPE,
                    "image": image,
                    # Пароль НЕ задаём: вернуть его пользователю некуда.
                    "sshKeys": [ssh_key],
                }}]},
                "nics": {"items": [{"properties": {
                    "name": f"{name}-nic",
                    "lan": lan_id,
                    "dhcp": True,
                }}]},
            },
        }
        # РОВНО один создающий запрос, без ретраев.
        data, err = await self._request(
            creds, "POST", f"{_CLOUD}/datacenters/{dc_path}/servers", body,
            detail=True)
        if err:
            return {**fail, "error": err}
        if not isinstance(data, dict) or not data.get("id"):
            return {**fail, "error": "IONOS принял запрос, но не вернул сервер "
                                     "— проверьте DCD перед повторной попыткой"}
        props = data.get("properties") if isinstance(data.get("properties"), dict) else {}
        return {
            "ok": True,
            "id": str(data.get("id")),
            "name": str(props.get("name") or name),
            # Cloud API стоимости не называет — маршрут спросит подтверждение.
            "price": None,
            "currency": "",
            "error": "",
        }


ADAPTER = IonosAdapter()
