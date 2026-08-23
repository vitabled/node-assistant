"""
Latency Lab — настройки интеграции и проверка ключа.

- GET  /api/latency/config → enabled/base_url/node_id/оператор по умолчанию +
  `has_key` и маска (`key_hint`). Сам ключ НЕ отдаётся никогда — контракт
  `AiConfig`.
- POST /api/latency/config → сохранение; непустой `api_key` шифруется Fernet,
  пустой/отсутствующий оставляет прежний.
- POST /api/latency/check → проверка ключа боем (`/api/lab/auth/me`).
- GET  /api/latency/operators → список операторов узла (`/api/lab/operators`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import latency_lab, net_guard, storage

router = APIRouter(prefix="/api/latency")

#: Штатный адрес сервиса. Он наш собственный константный дефолт, поэтому
#: SSRF-гард (а с ним и разрешение имени) применяется только к ОТКЛОНЕНИЯМ от
#: него: иначе сохранение настроек зависело бы от доступности DNS.
DEFAULT_BASE_URL = "https://console.latencylab.ru"


class LatencyConfigBody(BaseModel):
    enabled: bool = False
    base_url: str = Field(DEFAULT_BASE_URL, max_length=300)
    node_id: str = Field("orel", max_length=64)
    default_operator: str = Field("", max_length=32)
    api_key: str | None = None  # write-only; пусто/None — оставить прежний
    #: Лимит запусков сканов за окно; 0 — без лимита.
    scan_limit: int = Field(0, ge=0)
    #: Окно лимита в часах (1…720).
    scan_window_hours: int = Field(24, ge=1, le=720)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        # Адрес задаёт пользователь, а ходит по нему НАШ сервер изнутри сети —
        # тот же SSRF-гард, что у своего SearXNG и реестра чекеров.
        v = (v or "").strip().rstrip("/") or DEFAULT_BASE_URL
        if v != DEFAULT_BASE_URL and not net_guard.is_safe_url(v):
            raise ValueError("нужен http(s) на публичный хост (защита от SSRF)")
        return v

    @field_validator("default_operator")
    @classmethod
    def _operator(cls, v: str) -> str:
        v = latency_lab.normalize_operator(v)
        if v and v not in latency_lab.OPERATORS:
            raise ValueError(
                f"оператор должен быть одним из {latency_lab.OPERATORS} или пустым")
        return v


def _public() -> dict:
    cfg = latency_lab.config()
    used, _ = latency_lab.scan_quota(cfg)
    return {
        "enabled": cfg.enabled,
        "base_url": cfg.base_url,
        "node_id": cfg.node_id,
        "default_operator": cfg.default_operator,
        "scan_limit": cfg.scan_limit,
        "scan_window_hours": cfg.scan_window_hours,
        #: Запусков за окно (только счётчик; сами метки наружу не уходят).
        "scan_count": used,
        "operators": list(latency_lab.OPERATORS),
        "has_key": bool(cfg.api_key_enc),  # never the key itself
        "key_hint": latency_lab.mask_key(cfg),
    }


def _require_client():
    """Клиент или 400 с внятной причиной (выключено / нет ключа)."""
    cfg = latency_lab.config()
    if not cfg.enabled:
        raise HTTPException(400, "Latency Lab выключен в настройках")
    if not cfg.api_key_enc:
        raise HTTPException(400, "Не задан API-ключ Latency Lab")
    client = latency_lab.client()
    if client is None:
        raise HTTPException(400, "Не удалось расшифровать API-ключ Latency Lab")
    return client


@router.get("/config")
async def get_config() -> dict:
    return _public()


@router.post("/config")
async def save_config(body: LatencyConfigBody) -> dict:
    data = storage.load_settings()
    current = latency_lab.config()
    cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "base_url": body.base_url,
        "node_id": body.node_id.strip() or "orel",
        "default_operator": body.default_operator,
        "scan_limit": body.scan_limit,
        "scan_window_hours": body.scan_window_hours,
    }
    if body.api_key and body.api_key.strip():
        cfg["api_key_enc"] = latency_lab.encrypt_key(body.api_key.strip())
    data["latency"] = cfg
    storage.save_settings(data)
    return {"ok": True, **_public()}


@router.post("/check")
async def check_key() -> dict:
    """Проверка ключа боем. Не бросает на ошибке сервиса — отдаёт `ok:false`."""
    client = _require_client()
    data, err = await client.auth_me()
    if err:
        return {"ok": False, "error": err}
    result = (data or {}).get("result") or {}
    return {"ok": True, "user": result}


@router.get("/operators")
async def list_operators() -> dict:
    client = _require_client()
    data, err = await client.operators()
    if err:
        return {"ok": False, "error": err, "operators": []}
    result = (data or {}).get("result") or {}
    return {"ok": True, **result}
