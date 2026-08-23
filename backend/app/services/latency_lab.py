"""
Thin async client for the Latency Lab Personal API (console.latencylab.ru).

Auth: `Authorization: Bearer ll_…` (personal API key from the Latency Lab
profile). Тот же контракт, что у `bedolaga_client`: **ничего не бросает** —
каждый метод отдаёт `(data, error)`, поэтому мёртвый или неверно настроенный
сервис не роняет страницу панели, которая его опрашивает.

Границы personal-ключа (важны для вызывающего кода):
  * подсеть — только `/23`…`/32`, шире → HTTP 403;
  * multiscan считается за ОДИН запрос суточного лимита, поштучные
    `subnet_scan` — за каждый;
  * `async=true` → сразу `{status: "pending", req_id}`, дальше поллинг
    `job_status(req_id)`.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from app.models.settings import AppSettings, LatencyLabConfig
from app.services import ai_agent, storage

_TIMEOUT = 30  # синхронный скан подсети на агенте заметно дольше обычной ручки

#: Операторы модемов Latency Lab (OperatorId в спеке).
OPERATORS: tuple[str, ...] = ("tmobile", "megafon", "beeline", "mts", "t2")

#: Ключи операторов «Подсетей» → id операторов Latency Lab. Разошлось одно имя:
#: в подсетях исторически `tele2`, у сервиса — `t2`.
OPERATOR_ALIASES: dict[str, str] = {"tele2": "t2"}


def normalize_operator(op: str) -> str:
    op = (op or "").strip().lower()
    return OPERATOR_ALIASES.get(op, op)


#: Синонимы статусов job'а Latency Lab → канонические значения поллинга.
_JOB_DONE = {"done", "success", "completed", "finished", "ok"}
_JOB_ERROR = {"error", "failed"}


def normalize_job_status(status: Any) -> str:
    """Канонический статус job'а: done/error/cancelled/pending.

    Latency Lab местами отдаёт синонимы («success» вместо «done»,
    «failed» вместо «error») — панель ждёт ровно "done" и иначе
    крутила бы спиннер вечно (одна подсеть — вечный скан)."""
    st = str(status or "").strip().lower()
    if st in _JOB_DONE:
        return "done"
    if st in _JOB_ERROR:
        return "error"
    if st in ("cancelled", "canceled"):
        return "cancelled"
    return "pending"


def config(account_id: Optional[str] = None) -> LatencyLabConfig:
    return AppSettings(**storage.load_settings(account_id)).latency


def api_key(cfg: Optional[LatencyLabConfig] = None,
            account_id: Optional[str] = None) -> str:
    """Открытый ключ — ТОЛЬКО для исходящего запроса, наружу не отдаётся."""
    cfg = cfg or config(account_id)
    return ai_agent.decrypt_key(cfg.api_key_enc) or ""


def encrypt_key(plaintext: str) -> str:
    """Fernet-шифротекст ключа (общий вольт проекта — см. ai_agent)."""
    return ai_agent.encrypt_key(plaintext)


def mask_key(cfg: Optional[LatencyLabConfig] = None,
             account_id: Optional[str] = None) -> str:
    """Подсказка вида `ll_…4f2a`. Сам ключ не восстановим по ней."""
    raw = api_key(cfg, account_id)
    if not raw:
        return ""
    return f"…{raw[-4:]}" if len(raw) > 4 else "****"


def scan_quota(cfg: Optional[LatencyLabConfig] = None,
               account_id: Optional[str] = None) -> tuple[int, int]:
    """(использовано за окно, лимит). Лимит 0 — без лимита.

    Считает метки `scan_history` в окне [now - scan_window_hours*3600, now].
    """
    cfg = cfg or config(account_id)
    limit = int(cfg.scan_limit or 0)
    if limit <= 0:
        return 0, 0
    window = int(cfg.scan_window_hours or 24) * 3600
    now = time.time()
    used = sum(1 for ts in cfg.scan_history
               if isinstance(ts, (int, float)) and now - window <= ts <= now)
    return used, limit


def scan_reset(cfg: Optional[LatencyLabConfig] = None,
               now: Optional[float] = None,
               account_id: Optional[str] = None) -> tuple[float, int]:
    """(reset_ts, reset_in_seconds) — когда лимит сканов «обнулится».

    Окно скользящее: счётчик уменьшится, когда САМАЯ СТАРАЯ метка в окне
    выпадет из него — через (oldest + window) - now. Пустое окно → полное
    окно от now («лимит обновится через N часов»). Лимит 0 → (0.0, 0) —
    сбрасывать нечего. `now` — только для тестов.
    """
    cfg = cfg or config(account_id)
    limit = int(cfg.scan_limit or 0)
    window = int(cfg.scan_window_hours or 24) * 3600
    if limit <= 0:
        return 0.0, 0
    now = time.time() if now is None else now
    marks = [ts for ts in cfg.scan_history
             if isinstance(ts, (int, float)) and now - window <= ts <= now]
    oldest = min(marks) if marks else now
    reset_ts = oldest + window
    return reset_ts, max(0, int(reset_ts - now))


def record_scan(account_id: Optional[str] = None) -> None:
    """Метка успешного запуска скана (scan_history в settings.json).

    Зовётся только когда Latency Lab ПРИНЯЛ скан — не на ошибке сервиса.
    История режется до последних 1000 меток.
    """
    data = storage.load_settings(account_id)
    lat = data.setdefault("latency", {})
    history = lat.get("scan_history")
    if not isinstance(history, list):
        history = []
    history.append(time.time())
    lat["scan_history"] = history[-1000:]
    storage.save_settings(data, account_id)


class LatencyLabClient:
    def __init__(self, base_url: str, token: str, node_id: str = "orel"):
        self.base_url = (base_url or "https://console.latencylab.ru").rstrip("/")
        self.token = token
        self.node_id = node_id or "orel"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/json"}

    @staticmethod
    def _error(r: httpx.Response) -> str:
        """Текст ошибки от сервиса, если он его прислал (`{ok:false,error}`)."""
        try:
            body = r.json()
            if isinstance(body, dict) and body.get("error"):
                return str(body["error"])[:200]
        except Exception:  # noqa: BLE001 — тело может быть не JSON
            pass
        return f"HTTP {r.status_code}"

    async def _request(self, method: str, path: str,
                       params: Optional[dict] = None,
                       json_body: Optional[dict] = None,
                       ) -> tuple[Optional[Any], Optional[str]]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.request(method, f"{self.base_url}{path}",
                                         headers=self._headers(),
                                         params=params, json=json_body)
            if r.status_code == 401:
                return None, "Неверный API-ключ Latency Lab или он отозван"
            if r.status_code == 403:
                return None, self._error(r)
            if r.status_code == 404:
                return None, self._error(r) if r.content else f"Не найдено: {path}"
            if r.status_code >= 400:
                return None, self._error(r)
            return (r.json() if r.content else {}), None
        except httpx.TimeoutException:
            return None, "Таймаут запроса к Latency Lab"
        except Exception as exc:  # noqa: BLE001 — контракт: не бросаем
            return None, str(exc)[:200]

    async def _get(self, path: str, params: Optional[dict] = None):
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json_body: Optional[dict] = None):
        return await self._request("POST", path, json_body=json_body or {})

    # ── Auth / Meta ────────────────────────────────────────────
    async def auth_me(self):
        return await self._get("/api/lab/auth/me")

    async def operators(self):
        return await self._get("/api/lab/operators", {"node_id": self.node_id})

    async def status(self):
        return await self._get("/api/lab/status", {"node_id": self.node_id})

    # ── Скан ───────────────────────────────────────────────────
    async def multiscan(self, text: str, operators: Optional[list[str]] = None,
                        is_async: bool = False):
        """Мультискан по online-операторам. Один запрос суточного лимита."""
        body: dict = {"text": text, "node_id": self.node_id}
        if operators:
            body["operators"] = [normalize_operator(o) for o in operators]
        if is_async:
            body["async"] = True
        return await self._post("/api/lab/multiscan", body)

    async def subnet_scan(self, operator: str, target: str,
                          is_async: bool = False):
        """Скан подсети ОДНИМ оператором (для API-ключа только /23…/32)."""
        body: dict = {"operator": normalize_operator(operator),
                      "target": target, "node_id": self.node_id}
        if is_async:
            body["async"] = True
        return await self._post("/api/lab/subnet-scan", body)

    # ── Jobs ───────────────────────────────────────────────────
    async def job_status(self, req_id: str):
        return await self._get(f"/api/lab/job/{req_id}")

    async def cancel(self, req_id: str):
        return await self._post("/api/lab/cancel", {"req_id": req_id})


def client(account_id: Optional[str] = None) -> Optional[LatencyLabClient]:
    """Клиент по настройкам аккаунта или None, если интеграция не готова.

    None ровно в двух случаях: выключена или ключа нет. Различать их вызывающему
    не нужно — сообщение пользователю одно и то же.
    """
    cfg = config(account_id)
    if not cfg.enabled:
        return None
    key = api_key(cfg, account_id)
    if not key:
        return None
    return LatencyLabClient(cfg.base_url, key, cfg.node_id)
