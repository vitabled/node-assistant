"""Yandex Cloud adapter — billing balance + compute instances (Wave-9 Plan C, Ф4).

Authorized by a service-account key: IAM exchanges a **signed JWT** for a
short-lived IAM token, and that token authorizes the billing/compute calls. The
JWT is assembled here by hand (base64url header + payload + signature) because
`PS256` needs nothing beyond `cryptography`, which is already a dependency for
Fernet — a new pip package for one three-line signature isn't worth it.

Quirks, each of which costs an afternoon if missed:

- **`alg` must be `PS256`** — the only algorithm IAM accepts for SA keys, i.e.
  RSA-PSS with MGF1(SHA-256) and a salt as long as the digest. Signing RS256
  (`PKCS1v15`) fails with a bare 401 that reads like «wrong key».
- **`kid` is the KEY id, `iss` the SERVICE-ACCOUNT id** — two similar-looking ids
  that are easy to swap; `aud` is the token endpoint itself and `exp ≤ iat+3600`.
- **The IAM token lives up to 12 h**, so it is cached in memory for 55 min per
  service account: the exchange is a signed round-trip we don't want on every
  dashboard poll or background sync tick.
- **`balance` is a STRING** in `billingAccounts` (`"1234.56"`) and the currency is
  one of RUB/USD/KZT — coerced through `float()` in a `try`, never assumed RUB.
- **A key exported by `yc iam key create` is JSON**, so its PEM arrives with
  literal `\\n` sequences; they are un-escaped before parsing, otherwise every
  paste straight from the CLI fails to load.
- **`folderId` is mandatory** for the instance list and there is no «list all
  folders of the SA» shortcut, so without the optional field the service list is
  honestly empty instead of a guess.

Заказ (`order`) — это КОНСТРУКТОР, и у него пять особенностей, каждая из которых
меняет поведение:

- **⚠️ Размеры в теле создания — БАЙТЫ, и int64 приезжает СТРОКОЙ.**
  `resourcesSpec.memory` и `bootDiskSpec.diskSpec.size` считаются из наших
  гигабайт (`_gb_to_bytes`), а `cores`/`memory`/`size` отправляются строками —
  это канонический JSON-маппинг proto3, которым размечен весь API Yandex.
  Ошибиться здесь значит заказать ВМ в миллиард раз не того размера.
- **⚠️ `subnetId` обязателен, но формой не выбирается.** Он выводится из
  каталога: `GET vpc/v1/subnets?folderId=…` → первая подсеть в выбранной зоне.
  Подсети в зоне нет — честный отказ ДО создающего запроса, а не ВМ без сети.
  `folder_id` для заказа обязателен (в кредах он опциональный, потому что нужен
  только списку ВМ) — без него заказ отказывает словами.
- **Платформы (`platformId`) отдельной ручки не имеют** — их набор публикуется
  только в документации, поэтому `plans` здесь КОНСТАНТА `_PLATFORMS`, а не
  каталог. Незнакомый `plan_id` уходит вендору как есть: новая платформа
  заработает без правки кода.
- **Цены Compute API не даёт вовсе** — ни у платформы, ни у конфигурации:
  тарификация живёт в биллинге (`billing/v1/skus` знает цены SKU, но сопоставить
  SKU конкретной сборке ядер/памяти/диска/платформы нечем — это было бы
  угадывание). Поэтому `price=None` у всех планов и `quote_order` не
  реализован; маршрут покупки требует отдельного подтверждения «сумма заранее
  неизвестна».
- **⚠️ ВМ создаётся БЕЗ SSH-ключа.** Ключ у Yandex передаётся метаданными
  (`metadata.ssh-keys`), а форма заказа их не присылает — добавлять выдуманный
  логин к чужому ключу нельзя. Купленная ВМ поднимется, но зайти на неё можно
  будет только после добавления ключа в консоли (Изменить ВМ → метаданные).
  `autoDelete` у загрузочного диска включён намеренно: иначе удаление ВМ
  оставляет платный диск-сирота.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

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

log = logging.getLogger("hosting.yandex")

_IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
_BILLING_URL = "https://billing.api.cloud.yandex.net/billing/v1/billingAccounts"
_COMPUTE_URL = "https://compute.api.cloud.yandex.net/compute/v1/instances"
_ZONES_URL = "https://compute.api.cloud.yandex.net/compute/v1/zones"
_IMAGES_URL = "https://compute.api.cloud.yandex.net/compute/v1/images"
_SUBNETS_URL = "https://vpc.api.cloud.yandex.net/vpc/v1/subnets"

# Публичные образы вендора лежат в отдельном каталоге, доступном всем на чтение.
_STANDARD_IMAGES = "standard-images"
_PAGE_SIZE = 1000        # максимум страницы у Yandex; standard-images в неё влезает

_GB = 1024 ** 3

_JWT_TTL = 3600          # IAM rejects anything longer
_IAM_TTL = 55 * 60       # token is valid up to 12 h; refresh well inside the hour

# Ручки со списком платформ у Compute API нет — набор берётся из документации.
_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("standard-v3", "Intel Ice Lake"),
    ("standard-v2", "Intel Cascade Lake"),
    ("standard-v1", "Intel Broadwell"),
)
_DEFAULT_PLATFORM = "standard-v3"

# service_account_id → (iam token, expires_at). Module-level on purpose: the
# dashboard poll and the background sync loop share one process and one cache.
_IAM_CACHE: dict[str, tuple[str, float]] = {}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _int(raw: Any) -> Optional[int]:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _float(raw: Any) -> Optional[float]:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _folder(creds: dict) -> str:
    return str((creds or {}).get("folder_id") or "").strip()


def _gb_to_bytes(value: float) -> int:
    """ГБ формы → байты API. Отдельной функцией, потому что перепутать здесь
    единицы значит заказать ВМ совсем не того размера."""
    return int(round(float(value) * _GB))


def _bytes_to_gb(raw: Any) -> int:
    """Байты вендора → ГБ, ВВЕРХ: `minDiskSize` — это минимум, и округление вниз
    предложило бы диск, который вендор не примет."""
    size = _float(raw)
    if not size or size <= 0:
        return 0
    return int((int(size) + _GB - 1) // _GB)


def _reason(body: Any, fallback: str) -> str:
    """Причина отказа словами Yandex: ошибка приезжает как
    `{"code", "message", "details"}`. Для заказа это не украшательство — «нет
    квоты» и «образ не найден» лечатся по-разному."""
    if isinstance(body, dict):
        message = str(body.get("message") or "").strip()
        if message:
            return f"{fallback}: {message}"
    return fallback


def latest_by_family(images: Any) -> list[dict]:
    """Самый свежий образ каждого семейства.

    В `standard-images` лежат все сборки сразу (у одного `ubuntu-2204-lts` их
    десятки), и показывать их списком бессмысленно. `createdAt` — RFC3339 в UTC
    («2026-04-11T09:56:03Z»), поэтому сравнение строк совпадает со сравнением
    дат. Чистая функция: именно на ней ловится смена формы ответа."""
    best: dict[str, dict] = {}
    for raw in images if isinstance(images, list) else []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        # Поле `status` у части образов отсутствует — считаем такой готовым:
        # отбрасывать по молчанию значит терять весь каталог на переименовании.
        if str(raw.get("status") or "READY").upper() != "READY":
            continue
        family = str(raw.get("family") or "").strip() or str(raw.get("name") or raw["id"])
        current = best.get(family)
        if current is None or str(raw.get("createdAt") or "") > str(current.get("createdAt") or ""):
            best[family] = raw
    return [best[key] for key in sorted(best)]


def _load_key(pem: str) -> rsa.RSAPrivateKey:
    text = (pem or "").strip()
    # `yc iam key create` emits the PEM inside JSON, i.e. with escaped newlines.
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    key = serialization.load_pem_private_key(text.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("нужен RSA-ключ сервисного аккаунта")
    return key


def build_jwt(creds: dict, now: Optional[int] = None) -> str:
    """Signed PS256 JWT for the IAM exchange. Pure — unit-tested against the
    public half of the key, which is what catches a wrong padding/digest."""
    iat = int(now if now is not None else time.time())
    key = _load_key(str((creds or {}).get("private_key") or ""))
    header = {"alg": "PS256", "typ": "JWT", "kid": str(creds.get("key_id") or "").strip()}
    payload = {
        "iss": str(creds.get("service_account_id") or "").strip(),
        "aud": _IAM_URL,
        "iat": iat,
        "exp": iat + _JWT_TTL,
    }
    signing_input = "{}.{}".format(
        _b64u(json.dumps(header, separators=(",", ":")).encode()),
        _b64u(json.dumps(payload, separators=(",", ":")).encode()),
    )
    signature = key.sign(
        signing_input.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256.digest_size),
        hashes.SHA256(),
    )
    return f"{signing_input}.{_b64u(signature)}"


class YandexAdapter(ProviderAdapter):
    KIND = "yandex"
    TITLE = "Yandex Cloud"
    FIELDS = [
        CredField("service_account_id", "ID сервисного аккаунта"),
        CredField("key_id", "ID ключа"),
        CredField("private_key", "Приватный ключ (PEM)", "textarea"),
        CredField("folder_id", "ID каталога (для списка ВМ и заказа)", "text",
                  required=False),
    ]
    CAPS = {"balance", "services", "order"}

    async def _iam_token(self, creds: dict) -> tuple[str, str]:
        """Cached IAM token → (token, error)."""
        sa_id = str((creds or {}).get("service_account_id") or "").strip()
        pem = str((creds or {}).get("private_key") or "")
        cached = _IAM_CACHE.get(sa_id)
        if cached and cached[1] > time.time():
            return cached[0], ""

        try:
            jwt = build_jwt(creds)
        except Exception as exc:  # unreadable/encrypted PEM, wrong key type
            return "", "не удалось прочитать приватный ключ: " + redact(str(exc), pem)

        try:
            async with self._client() as c:
                r = await c.post(_IAM_URL, json={"jwt": jwt})
        except httpx.HTTPError as exc:
            return "", f"Yandex IAM недоступен: {redact(str(exc), pem, jwt)}"

        if r.status_code >= 400:
            return "", map_http_error(r.status_code)
        try:
            token = str((r.json() or {}).get("iamToken") or "")
        except ValueError:
            return "", "Yandex IAM вернул не-JSON ответ"
        if not token:
            return "", "Yandex IAM не вернул токен"
        _IAM_CACHE[sa_id] = (token, time.time() + _IAM_TTL)
        return token, ""

    async def _get(self, creds: dict, url: str,
                   params: Optional[dict] = None) -> tuple[Any, str]:
        token, err = await self._iam_token(creds)
        if err:
            return None, err
        try:
            async with self._client() as c:
                r = await c.get(url, params=params,
                                headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            return None, f"Yandex Cloud недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Yandex Cloud вернул не-JSON ответ"

    async def _post(self, creds: dict, url: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: создание ВМ тратит деньги, а таймаут не
        означает «не создано»."""
        token, err = await self._iam_token(creds)
        if err:
            return None, err
        try:
            async with self._client() as c:
                r = await c.post(url, json=body,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            return None, f"Yandex Cloud недоступен: {redact(str(exc), token)}"

        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            return None, redact(_reason(data, map_http_error(r.status_code)), token)
        if not isinstance(data, dict):
            return None, "Yandex Cloud вернул не-JSON ответ"
        return data, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        # The IAM exchange IS the credential check; per-service permissions are
        # reported by the balance/services calls themselves.
        _token, err = await self._iam_token(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, _BILLING_URL)
        if err or not isinstance(data, dict):
            return None
        accounts = [a for a in (data.get("billingAccounts") or []) if isinstance(a, dict)]
        if not accounts:
            return None
        # First active account; a suspended one still has a balance worth showing,
        # so it is the fallback rather than a `None`.
        account = next((a for a in accounts if a.get("active")), accounts[0])
        try:
            amount = float(str(account["balance"]).strip())
        except (KeyError, TypeError, ValueError):
            log.warning("yandex: unexpected billingAccounts shape")
            return None
        return Balance(amount, str(account.get("currency") or "RUB").upper())

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        folder = str((creds or {}).get("folder_id") or "").strip()
        if not folder:
            return []
        data, err = await self._get(creds, _COMPUTE_URL, {"folderId": folder})
        if err or not isinstance(data, dict):
            return []
        return [_instance_item(raw) for raw in (data.get("instances") or [])
                if isinstance(raw, dict)]

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds) or not _folder(creds):
            # Без каталога заказывать некуда: и подсеть, и сама ВМ адресуются
            # `folderId`. Отказ здесь честнее пустой формы.
            return None
        zones, err = await self._get(creds, _ZONES_URL)
        if err or not isinstance(zones, dict):
            return None
        images, _err = await self._get(
            creds, _IMAGES_URL,
            {"folderId": _STANDARD_IMAGES, "pageSize": _PAGE_SIZE})

        catalog = [
            {"id": str(raw.get("id") or ""),
             "name": str(raw.get("description") or raw.get("name") or raw.get("family") or ""),
             "family": str(raw.get("family") or ""),
             # Минимум едет с образом: у Windows он выше, чем у Linux, и общий
             # «пол» конструктора этого не выражает.
             "min_disk_gb": _bytes_to_gb(raw.get("minDiskSize"))}
            for raw in latest_by_family((images or {}).get("images"))
        ]
        floors = [i["min_disk_gb"] for i in catalog if i["min_disk_gb"] > 0]

        return OrderOptions(
            # «Тариф» здесь — платформа: набор ядер/памяти/диска задаёт
            # конструктор, а цены Compute API не называет вовсе.
            plans=[OrderPlan(id=pid, name=f"{pid} — {title}",
                             specs="платформа; ядра, память и диск — в конструкторе",
                             price=None, currency="", period="hour")
                   for pid, title in _PLATFORMS],
            regions=[{"id": str(z.get("id") or ""),
                      "name": f"{z.get('id') or ''} ({z.get('regionId') or ''})".strip()}
                     for z in (zones.get("zones") or [])
                     if isinstance(z, dict) and z.get("id")
                     and str(z.get("status") or "UP").upper() == "UP"],
            images=[i for i in catalog if i["id"]],
            custom={
                # Границы платформозависимы (у standard-v3 ядра чётные, от 2), и
                # ручки с ними у API нет. `max: None` = «вендор не публикует
                # верхнюю границу» (семантика base.OrderOptions): выдуманный
                # потолок отрезал бы реальную конфигурацию, а соотношение памяти
                # к ядрам схема {min,max,step} всё равно не выражает —
                # последнее слово за валидацией провайдера.
                "cpu": {"min": 2, "max": None, "step": 2},
                "ram_gb": {"min": 1, "max": None, "step": 1},
                "disk_gb": {"min": min(floors) if floors else 10,
                            "max": None, "step": 1},
            },
        )

    async def _subnet_in_zone(self, creds: dict, folder: str,
                              zone: str) -> tuple[str, str]:
        """Подсеть каталога в выбранной зоне → (id подсети, причина отказа).

        `subnetId` в теле создания обязателен, но формой не выбирается: у
        каталога обычно одна подсеть на зону, и спрашивать её у пользователя
        значит показывать внутренности VPC. Несколько — берём первую (порядок
        отдаёт вендор). Ни одной — отказ, а не ВМ без сети."""
        data, err = await self._get(creds, _SUBNETS_URL,
                                    {"folderId": folder, "pageSize": _PAGE_SIZE})
        if err:
            return "", err
        if not isinstance(data, dict):
            return "", "Yandex VPC вернул неожиданный ответ"
        for raw in data.get("subnets") or []:
            if (isinstance(raw, dict) and raw.get("id")
                    and str(raw.get("zoneId") or "") == zone):
                return str(raw["id"]), ""
        return "", (f"в каталоге нет подсети в зоне {zone} — создайте сеть "
                    "и подсеть в консоли Yandex Cloud")

    async def create_order(self, creds: dict, spec: dict) -> dict:
        # Валюта — та, в которой ведётся платёжный аккаунт (RUB/USD/KZT); в
        # ответе на создание её нет, поэтому объявлять её здесь нечем.
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": ""}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}
        folder = _folder(creds)
        if not folder:
            return {**fail, "error": "не заполнен ID каталога (folder_id) в кредах "
                                     "провайдера — без него заказывать некуда"}

        spec = spec or {}
        zone = str(spec.get("region") or "").strip()
        image = str(spec.get("image") or "").strip()
        name = str(spec.get("name") or "").strip()
        cpu = _int(spec.get("cpu"))
        ram = _float(spec.get("ram_gb"))
        disk = _float(spec.get("disk_gb"))
        empty = [label for label, value in (
            ("имя ВМ", name), ("зона", zone), ("образ", image),
            ("CPU", cpu), ("RAM", ram), ("диск", disk),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        subnet, err = await self._subnet_in_zone(creds, folder, zone)
        if err:
            return {**fail, "error": err}

        body = {
            "folderId": folder,
            "name": name,
            "zoneId": zone,
            # Незнакомая платформа уходит вендору как есть — см. `_PLATFORMS`.
            "platformId": str(spec.get("plan_id") or "").strip() or _DEFAULT_PLATFORM,
            # int64 у Yandex — СТРОКА, а память и размер диска — БАЙТЫ.
            "resourcesSpec": {"cores": str(cpu), "memory": str(_gb_to_bytes(ram))},
            "bootDiskSpec": {
                # Без `autoDelete` удаление ВМ оставит платный диск-сироту.
                "autoDelete": True,
                "diskSpec": {"size": str(_gb_to_bytes(disk)), "imageId": image},
            },
            "networkInterfaceSpecs": [{
                "subnetId": subnet,
                # Публичный адрес: без него ВМ недоступна снаружи, а весь смысл
                # покупки здесь — получить сервер, на который можно зайти.
                "primaryV4AddressSpec": {"oneToOneNatSpec": {"ipVersion": "IPV4"}},
            }],
        }

        data, err = await self._post(creds, _COMPUTE_URL, body)
        if err:
            return {**fail, "error": err}
        # Ответ — ОПЕРАЦИЯ, а не ВМ: идентификатор лежит в `metadata.instanceId`,
        # и на момент ответа машина ещё создаётся.
        meta = (data or {}).get("metadata")
        instance_id = str((meta or {}).get("instanceId") or "") if isinstance(meta, dict) else ""
        if not instance_id:
            return {**fail, "error": "Yandex Cloud принял запрос, но не вернул id ВМ "
                                     "— проверьте консоль перед повторной попыткой"}
        return {
            "ok": True,
            "id": instance_id,
            "name": name,
            # Цену Compute API не называет — маршрут покупки требует отдельного
            # подтверждения «сумма заранее неизвестна».
            "price": None,
            "currency": "",
            "error": "",
        }


def _instance_ip(raw: dict) -> str:
    """Public (one-to-one NAT) address if the VM has one, else the internal one."""
    for nic in raw.get("networkInterfaces") or []:
        if not isinstance(nic, dict):
            continue
        primary = nic.get("primaryV4Address")
        if not isinstance(primary, dict):
            continue
        nat = primary.get("oneToOneNat")
        if isinstance(nat, dict) and nat.get("address"):
            return str(nat["address"])
        if primary.get("address"):
            return str(primary["address"])
    return ""


def _instance_item(raw: dict) -> ServiceItem:
    iid = str(raw.get("id") or "")
    return ServiceItem(
        id=iid,
        name=str(raw.get("name") or "").strip() or f"VM {iid}",
        kind="vm",
        cost=None,
        # Pay-as-you-go: there is no per-VM price in the compute API, and the
        # account currency may be RUB/USD/KZT — left empty rather than guessed.
        currency="",
        period="hour",
        status=str(raw.get("status") or ""),
        ip=_instance_ip(raw),
        region=str(raw.get("zoneId") or ""),
        paid_till="",
    )


ADAPTER = YandexAdapter()
ADAPTERS = [ADAPTER]
