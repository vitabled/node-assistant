"""BILLmanager (ISPsystem) — один адаптер на всех, кто на этой биллинг-панели.

FirstVDS, AdminVPS и десятки других провайдеров продают через один и тот же
BILLmanager, поэтому `kind` один, а различает их только адрес панели —
как `openstack.py` для Keystone-облаков. TITLE называет известные бренды, чтобы
человек нашёл своего провайдера в списке.

Протокол, и почему каждая деталь важна:

- **Точка входа — `<панель>/billmgr`**, параметры `func=…&out=json`. Клиенты
  копируют адрес и с `/billmgr`, и без него, поэтому суффикс добавляется только
  если его нет (тот же случай, что `/v3` в openrc у OpenStack).
- **`func=auth` → id сессии в `doc.auth.$id`**, дальше он едет параметром `auth`.
  Сессия кэшируется в памяти: без кэша каждый опрос дашборда и каждый тик
  фонового синка авторизовались бы заново — а логин с паролем самое неприятное
  место, чтобы ловить лимиты и блокировки.
- **Ошибка приезжает с HTTP 200** — внутри `doc.error`. Проверять только статус
  недостаточно: «неверный пароль» выглядел бы как успех с пустыми данными.
- **Скаляр — объект `{"$": "значение"}`**, а не строка: наследие XML-ядра
  ISPsystem. `doc.balance` это `{"$": "1234.56"}`, а не `"1234.56"`.
- **Одна запись приходит объектом, а не списком из одного элемента** — классика
  того же XML-ядра, и на ней легко потерять единственный платёж.
- **`base_url` — ПОЛЬЗОВАТЕЛЬСКИЙ ввод**, поэтому `net_guard.is_safe_url` и при
  `verify`, И перед каждым запросом: сохранённый адрес может позже
  переразрешиться во внутренний (DNS-rebinding). Правило `openstack.py`.
- **Креды уходят ТЕЛОМ POST, а не в query.** BILLmanager принимает и то и другое,
  но в query логин с паролем осели бы в access-логе панели и в строке ошибки
  httpx (грабли `beget.py`).

Услуг не читаем: `CAPS` = balance + payments. Список услуг в BILLmanager
разложен по типам продуктов (`func=vds`, `func=dedic`, `func=domain`, …), у
каждого провайдера включён свой набор — единого «дай все услуги» нет, а
перебирать наугад значит получить пустой список там, где продукт называется
иначе.

⚠️ **Заказ: `CAPS` НЕ заявляет `order`, и это не недоделка.** Оформление заказа
в BILLmanager многошаговое, и каждый шаг зависит от настроек КОНКРЕТНОГО
провайдера — предсказуемого одного вызова тут нет:

- **Имя функции плавает.** У одних сборок это `func=vds.order.param`, у других
  `func=v2.vds.order.param` c `force_use_new_cart=on`. Сам продукт тоже называется
  по-разному (`vds`, `dedic` или то, как провайдер назвал свой тип услуги).
- **Конфигурация задаётся параметрами `addon_<id>=<id значения>`, где оба числа —
  записи БД этого провайдера.** У FirstVDS в примере заказа это `addon_56329=93`,
  `addon_56336=off`, `addon_56330=21`; в примерах самой ISPsystem — `addon_10`,
  `addon_11`, `addon_12`. Сопоставить общий `spec` (`cpu`, `ram_gb`, `disk_gb`) с
  такими номерами можно только разобрав форму заказа конкретного тарифа и угадав,
  какой addon означает ядра, какой память, а какой диск.
- **`skipbasket=on` списывает деньги сразу**, без корзины и подтверждения. Цена
  ошибки в этом угадывании — оплаченный сервер не той конфигурации, поэтому
  кнопка «купить», собранная на догадках, здесь опаснее её отсутствия.

Каталога тарифов по той же причине тоже нет: `/providers/{uuid}/order-options`
гейтится на `CAPS`, поэтому `order_options()` без `order` в CAPS всё равно
недостижим через API (та же ловушка описана в `selectel.py`) — сетевой запрос к
ручке, не подтверждённой для КЛИЕНТСКОЙ сессии, был бы мёртвым кодом. Если
заказ когда-нибудь будут доделывать: разбирать надо форму `*.order.param`
конкретного провайдера, а не общий список функций.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.services import net_guard
from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.billmanager")

_UNSAFE = "адрес панели недопустим: нужен http(s) с публичным (маршрутизируемым) хостом"

# Сессия BILLmanager протухает по бездействию (у разных сборок порог свой).
# 15 минут — заведомо меньше любого вендорского значения, а промах кэша стоит
# ровно одного лишнего запроса.
_SESSION_TTL = 15 * 60

# sha256(адрес + логин + пароль) → (id сессии, когда протухнет). Модульный
# уровень намеренно: опрос дашборда и фоновый синк живут в одном процессе.
_SESSIONS: dict[str, tuple[str, float]] = {}

_BALANCE_FUNC = "usrparam"   # параметры пользователя личного кабинета
_PAYMENT_FUNC = "payment"    # входящие платежи клиента

# Отказ словами, а не молчаливая кнопка. Обоснование — в шапке модуля.
_ORDER_UNSUPPORTED = (
    "Заказ в BILLmanager многошаговый и зависит от настроек конкретного "
    "провайдера (номера addon_* свои у каждого тарифа) — оформите в панели"
)

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_SPACE_RE = re.compile(r"\s")

_AMOUNT_KEYS = ("balance", "balance_str", "money", "credit", "sum")
_CURRENCY_KEYS = ("currency", "currency_iso", "iso", "money_iso")
# Валюта часто есть только в отформатированной для человека строке баланса.
_CURRENCY_SNIFF = (("руб", "RUB"), ("₽", "RUB"), ("rub", "RUB"),
                   ("usd", "USD"), ("$", "USD"),
                   ("eur", "EUR"), ("€", "EUR"))

_PAY_TS_KEYS = ("paydate", "date", "createdate", "dt")
_PAY_AMOUNT_KEYS = ("amount", "subtotal", "sum", "total")
_PAY_NOTE_KEYS = ("paymethodname", "name", "status_name", "number", "status")

# Типы ошибок, означающие «сессия/креды не приняты» — их стоит показать как
# понятную причину и сбросить кэш сессии.
_AUTH_ERRORS = ("auth", "badauth", "access", "noauth", "session")


def endpoint(base_url: str) -> str:
    """Адрес панели → полный URL точки входа BILLmanager."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.lower().endswith("/billmgr"):
        return base
    return base + "/billmgr"


def _cache_key(url: str, username: str, password: str) -> str:
    raw = "\0".join((url, username, password)).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()


def _val(node: Any) -> str:
    """Скаляр ISPsystem: `{"$": "текст"}` → `"текст"`."""
    if isinstance(node, dict):
        return str(node.get("$", "")).strip()
    if node is None:
        return ""
    return str(node).strip()


def _num(text: str) -> Optional[float]:
    """Число из значения BILLmanager: и `"1234.56"`, и `"1 234,56 руб."`.

    Пробелы срезаются регуляркой, а не `replace`: разряды панель разделяет
    НЕРАЗРЫВНЫМ пробелом, и юникодный `\\s` ловит его наравне с обычным.
    """
    cleaned = _SPACE_RE.sub("", text or "")
    found = _NUM_RE.search(cleaned)
    if not found:
        return None
    try:
        return float(found.group(0).replace(",", "."))
    except ValueError:
        return None


def doc_error(doc: Any) -> tuple[str, bool]:
    """(текст ошибки, это ли отказ авторизации). "" — ошибки нет.

    Отдельный флаг нужен, чтобы сбросить кэш сессии ровно тогда, когда она
    протухла, и не сбрасывать её из-за, скажем, отсутствующего func."""
    if not isinstance(doc, dict):
        return "", False
    err = doc.get("error")
    if not isinstance(err, dict):
        return "", False
    kind = str(err.get("$type") or err.get("type") or "").strip().lower()
    is_auth = any(marker in kind for marker in _AUTH_ERRORS)
    if is_auth:
        return "неверные креды", True
    message = _val(err.get("msg")) or _val(err.get("object")) or kind
    return (message or "панель отклонила запрос"), False


def elems(doc: Any) -> list[dict]:
    """Записи списка. Одна запись приходит объектом, а не списком из одного."""
    if not isinstance(doc, dict):
        return []
    elem = doc.get("elem")
    if isinstance(elem, dict):
        return [elem]
    if isinstance(elem, list):
        return [e for e in elem if isinstance(e, dict)]
    return []


class BillmanagerAdapter(ProviderAdapter):
    KIND = "billmanager"
    TITLE = "BILLmanager (FirstVDS, AdminVPS и др.)"
    FIELDS = [
        CredField("base_url", "Адрес панели (например https://my.firstvds.ru)"),
        CredField("username", "Логин личного кабинета"),
        CredField("password", "Пароль личного кабинета", "password"),
    ]
    # Без "services": единого списка услуг у BILLmanager нет (см. шапку модуля).
    CAPS = {"balance", "payments"}

    async def _post(self, creds: dict, params: dict) -> tuple[Any, str]:
        """Один запрос к billmgr → (doc, ошибка). Гард на адрес — здесь, а не в
        вызывающем: так он гарантированно срабатывает ПЕРЕД каждым запросом."""
        url = endpoint(str((creds or {}).get("base_url") or ""))
        password = str((creds or {}).get("password") or "")
        if not url or not net_guard.is_safe_url(url):
            return None, _UNSAFE

        try:
            async with self._client() as c:
                r = await c.post(url, data={**params, "out": "json"})
        except httpx.HTTPError as exc:
            # Маскируем ТОЛЬКО пароль. Логин секретом не является, а прогнать его
            # через redact() значит порезать сообщение: логин бывает в один-два
            # символа, и «refused» превратилось бы в «ref«redacted»sed».
            return None, f"панель недоступна: {redact(str(exc), password)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            body = r.json()
        except ValueError:
            return None, "панель вернула не-JSON ответ"
        doc = body.get("doc") if isinstance(body, dict) else None
        if not isinstance(doc, dict):
            return None, "панель вернула неожиданный ответ"
        return doc, ""

    async def _session(self, creds: dict) -> tuple[str, str]:
        """Кэшированный id сессии → (id, ошибка)."""
        url = endpoint(str((creds or {}).get("base_url") or ""))
        username = str((creds or {}).get("username") or "").strip()
        password = str((creds or {}).get("password") or "")
        slot = _cache_key(url, username, password)
        cached = _SESSIONS.get(slot)
        if cached and cached[1] > time.time():
            return cached[0], ""

        doc, err = await self._post(creds, {
            "func": "auth", "username": username, "password": password,
        })
        if err:
            return "", err
        message, _is_auth = doc_error(doc)
        if message:
            return "", message
        auth = doc.get("auth")
        sid = str(auth.get("$id") or "").strip() if isinstance(auth, dict) else ""
        if not sid:
            return "", "панель не вернула id сессии"
        _SESSIONS[slot] = (sid, time.time() + _SESSION_TTL)
        return sid, ""

    async def _call(self, creds: dict, func: str) -> tuple[Any, str]:
        sid, err = await self._session(creds)
        if err:
            return None, err
        doc, err = await self._post(creds, {"func": func, "auth": sid})
        if err:
            return None, err
        message, is_auth = doc_error(doc)
        if is_auth:
            # Сессию отозвали раньше срока — выкидываем из кэша, чтобы следующий
            # вызов авторизовался заново, а не ждал конца TTL.
            url = endpoint(str((creds or {}).get("base_url") or ""))
            _SESSIONS.pop(_cache_key(url,
                                     str((creds or {}).get("username") or "").strip(),
                                     str((creds or {}).get("password") or "")), None)
        if message:
            return None, message
        return doc, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _sid, err = await self._session(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        doc, err = await self._call(creds, _BALANCE_FUNC)
        if err or not isinstance(doc, dict):
            return None
        for key in _AMOUNT_KEYS:
            if key not in doc:
                continue
            raw = _val(doc[key])
            amount = _num(raw)
            if amount is None:
                continue
            return Balance(amount, _currency(doc, raw))
        log.warning("billmanager: в ответе func=%s нет узнаваемого баланса",
                    _BALANCE_FUNC)
        return None

    async def payments(self, creds: dict) -> list[dict]:
        """`func=payment` — это ВХОДЯЩИЕ платежи клиента, то есть пополнения.
        Начисления живут в другом разделе панели и здесь не читаются."""
        if self.check_fields(creds):
            return []
        doc, err = await self._call(creds, _PAYMENT_FUNC)
        if err:
            return []
        out: list[dict] = []
        for raw in elems(doc):
            formatted = _pick(raw, _PAY_AMOUNT_KEYS)
            amount = _num(formatted)
            out.append({
                "ts": _pick(raw, _PAY_TS_KEYS),
                "amount": amount if amount is not None else 0.0,
                "currency": _currency(raw, formatted),
                "type": "topup",
                "note": _pick(raw, _PAY_NOTE_KEYS),
            })
        return out

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Честный отказ БЕЗ запроса к панели: угадывать `addon_*` нельзя.

        Сети не касается сознательно — «попробовать и посмотреть» здесь означает
        отправить в чужой биллинг заказ, который при `skipbasket=on` оплачивается
        сразу."""
        return {
            "ok": False, "id": "", "name": "", "price": None, "currency": "RUB",
            "error": _ORDER_UNSUPPORTED,
        }


def _pick(node: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _val(node.get(key))
        if value:
            return value
    return ""


def _currency(node: dict, formatted: str = "") -> str:
    """ISO-код из отдельного поля, иначе из отформатированной суммы, иначе RUB
    (панели BILLmanager в нашем каталоге — российские магазины)."""
    explicit = _pick(node, _CURRENCY_KEYS)
    if explicit and len(explicit) <= 4 and explicit.isalpha():
        return explicit.upper()
    haystack = (formatted or explicit or "").lower()
    for marker, iso in _CURRENCY_SNIFF:
        if marker in haystack:
            return iso
    return "RUB"


ADAPTER = BillmanagerAdapter()
