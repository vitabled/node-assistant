"""
AI agent config + chat API (Ф4). Account-gated.

- GET  /api/ai/config → provider/model/limits + `has_key` (the key is NEVER
  returned — only whether one is stored).
- POST /api/ai/config → persist provider/model/base_url/limits; a non-empty
  `api_key` is Fernet-encrypted into the vault, an omitted/blank one keeps the
  existing key.
- POST /api/ai/chat  → streams the tool-calling loop as JSONL events
  (tool_call / tool_result / text / done / error) so the UI shows tools live.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

import json

from app.models.settings import PROVIDER_BASE_URLS
from app.services import (accounts, ai_agent, ai_chat_persist, ai_runs,
                          ai_tools, ai_uploads, ai_web, net_guard, storage)

router = APIRouter(prefix="/api/ai")

_PROVIDERS = ("openai", "anthropic")
_GATEWAYS = ("none", "cliproxy")


class AiConfigBody(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    base_url: str = Field("https://api.openai.com/v1", max_length=300)
    # Пусто = «выводить из провайдера». Ручной режим — для сторонних
    # OpenAI-совместимых эндпоинтов (OpenRouter, локальная llama.cpp).
    base_url_auto: bool = True
    model: str = Field("gpt-4o-mini", max_length=120)
    api_key: str | None = None  # write-only; blank/None keeps the existing key
    # Потолок 60: измеренный перенос каталога на 205 записей занял 33 вызова
    # (2 открытия + поиск + 25 чтений + 5 пакетных записей). Ниже — не влезает
    # в один заход, выше — уже про разгон, а не про работу.
    # ⚠️ 0 = АВТО (см. `ai_agent.AUTO_MAX_STEPS`): нижняя граница опущена до 0
    # именно ради этого режима, 1..60 — прежний ручной режим без изменений.
    max_steps: int = Field(12, ge=0, le=60)
    # Потолок вывода за тёрн. Нижняя граница не «1»: на 256 токенах не помещается
    # даже короткое тело запроса, и агент упирался бы в обрыв на каждом шаге.
    # 0 = АВТО: агент сам поднимает потолок ×1.5 на обрыве по длине.
    max_tokens: int = Field(8192, ge=0, le=64000)
    auto_token_budget: int = Field(0, ge=0, le=10_000_000)
    readonly: bool = True
    active_preset_id: str = Field("", max_length=64)  # Plan I; "" = default preset
    gateway: str = "none"  # Plan J; none | cliproxy
    use_mcp: bool = False  # Wave-7 Plan E Ф2: borrow the MCP server's tools
    # Веб-доступ. `web_api_key` — write-only, как и ключ провайдера.
    web_enabled: bool = True
    web_provider: str = "duckduckgo"
    web_api_key: str | None = None
    web_base_url: str = Field("", max_length=300)
    web_max_results: int = Field(5, ge=1, le=ai_web.MAX_RESULTS)

    @field_validator("provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        if v not in _PROVIDERS:
            raise ValueError(f"provider должен быть одним из {_PROVIDERS}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def _max_tokens(cls, v: int) -> int:
        # Разрешаем ровно 0 (авто) и прежний коридор 256..64000. «Мусорные»
        # 1..255 остаются ошибкой: на них ответ обрывался бы на каждом шаге.
        if v != 0 and v < 256:
            raise ValueError("max_tokens: 0 (авто) либо 256..64000")
        return v

    @field_validator("gateway")
    @classmethod
    def _gateway(cls, v: str) -> str:
        if v not in _GATEWAYS:
            raise ValueError(f"gateway должен быть одним из {_GATEWAYS}")
        return v

    @field_validator("web_provider")
    @classmethod
    def _web_provider(cls, v: str) -> str:
        if v not in ai_web.WEB_PROVIDERS:
            raise ValueError(f"web_provider должен быть одним из {ai_web.WEB_PROVIDERS}")
        return v

    @field_validator("web_base_url")
    @classmethod
    def _web_base_url(cls, v: str) -> str:
        # Адрес своего SearXNG задаёт пользователь, а ходит по нему НАШ сервер
        # изнутри сети — гард обязателен и здесь, и при каждом запросе (то же
        # правило, что у `openstack.auth_url` и реестра чекеров).
        v = (v or "").strip()
        if v and not net_guard.is_safe_url(v):
            raise ValueError("нужен http(s) на публичный хост (защита от SSRF)")
        return v


class AttachmentImage(BaseModel):
    """Картинка, вынесенная из текста вложения. В промпт НЕ попадает: модель
    видит на её месте маркер «изображение #N» и сохраняет её по номеру."""
    index: int = Field(0, ge=0)
    mime: str = Field("image/jpeg", max_length=60)
    data_b64: str = Field("", max_length=25_000_000)


class Attachment(BaseModel):
    """Вложение чата. Файлы НЕ персистятся: они относятся к одному вопросу, а не
    к аккаунту, поэтому едут в теле запроса и живут ровно столько же."""
    name: str = Field("", max_length=200)
    mime: str = Field("", max_length=100)
    # Текстовый файл — как есть; картинка или архив — base64 (форму блока
    # выбирает ai_agent.build_user_content, она у провайдеров разная).
    # ⚠️ Потолок — на ВЕСЬ файл, а не на то, что уедет в промпт: в сообщение
    # кладётся только начало, остальное читается инструментами. Прежние
    # 40 000 резали каталог на 22 МБ до 0,18%, и модель делала вывод, что
    # данных в файле нет.
    text: str = Field("", max_length=ai_agent.MAX_TEXT_CHARS)
    # 50 МБ архива (потолок фронта) — это ~68 млн символов base64. Прежние
    # 6 млн отбивали любой сколько-нибудь содержательный архив ещё на
    # валидации тела, до всякой распаковки.
    data_b64: str = Field("", max_length=70_000_000)
    # Картинки из текстового файла едут отдельно от текста — см. AttachmentImage.
    images: list[AttachmentImage] = Field(default_factory=list, max_length=500)


class HistoryMsg(BaseModel):
    """Реплика прошлого хода. Историю ведёт клиент: сервер переписку не хранит,
    поэтому ей нечего утекать и нечего чистить по расписанию."""
    role: str = Field("user", max_length=16)
    content: str = Field("", max_length=ai_agent.MAX_HISTORY_CHARS)


class ChatBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    attachments: list[Attachment] = Field(default_factory=list,
                                          max_length=ai_agent.MAX_ATTACHMENTS)
    # Файлы, уже уехавшие отдельным `POST /chat/upload`. Тело чата остаётся
    # лёгким: раньше 50-мегабайтный архив ехал здесь же base64'ом (~67 МБ JSON)
    # и рвался на середине у любого, кто сидит за VPN. См. `ai_uploads`.
    upload_ids: list[str] = Field(default_factory=list,
                                  max_length=ai_agent.MAX_ATTACHMENTS)
    history: list[HistoryMsg] = Field(
        default_factory=list, max_length=ai_agent.MAX_HISTORY_MESSAGES * 2)
    # Идентификатор разговора: по нему вложение доживает до следующего
    # сообщения. Пусто = вложение живёт один запрос, как раньше.
    session_id: str = Field("", max_length=64)


def _public(account_id: str | None = None) -> dict:
    cfg = ai_agent._cfg(account_id)
    return {
        "enabled": cfg.enabled,
        "provider": cfg.provider,
        # Отдаём АДРЕС НАЗНАЧЕНИЯ, а не сырое поле: в авторежиме он выводится из
        # провайдера, и форма должна показывать то же, куда реально уйдёт запрос.
        "base_url": cfg.effective_base_url(),
        "base_url_auto": cfg.base_url_auto,
        # Каталог штатных адресов — чтобы форма не держала свою копию: она
        # отстанет, а цена рассинхрона — 401 с верным ключом.
        "provider_defaults": dict(PROVIDER_BASE_URLS),
        "model": cfg.model,
        "max_steps": cfg.max_steps,
        "max_tokens": cfg.max_tokens,
        # Производные флаги для формы: 0 = «авто». Отдаём явно, чтобы фронт не
        # заводил свою копию правила «ноль значит особый режим» — она отстанет.
        "auto_steps": cfg.max_steps <= 0,
        "auto_tokens": cfg.max_tokens <= 0,
        "auto_max_steps": ai_agent.AUTO_MAX_STEPS,
        "auto_token_budget": getattr(cfg, "auto_token_budget", 0) or ai_agent.AUTO_TOKEN_BUDGET,
        "readonly": cfg.readonly,
        "active_preset_id": cfg.active_preset_id,
        "gateway": cfg.gateway,
        "use_mcp": cfg.use_mcp,
        "web_enabled": cfg.web_enabled,
        "web_provider": cfg.web_provider,
        "web_base_url": cfg.web_base_url,
        "web_max_results": cfg.web_max_results,
        "web_needs_key": ai_web.needs_key(cfg.web_provider),
        "has_web_key": bool(cfg.web_api_key_enc),  # never the key itself
        "has_key": bool(cfg.api_key_enc),  # never the key itself
        # Есть ли ЧЕМ авторизоваться: через CLIProxyAPI ключ провайдера не нужен,
        # доступ даёт OAuth-аккаунт внутри шлюза. Фронт гейтит композер по этому
        # полю, а не по `has_key`.
        "auth_ready": bool(ai_agent.effective_target(cfg)[1]),
    }


@router.get("/config")
async def get_config() -> dict:
    return _public()


@router.post("/config")
async def save_config(body: AiConfigBody) -> dict:
    data = storage.load_settings()
    current = ai_agent._cfg()
    ai_cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "provider": body.provider,  # already validated to be a known provider
        "base_url": body.base_url.strip(),
        "base_url_auto": body.base_url_auto,
        "model": body.model.strip(),
        "max_steps": body.max_steps,
        "max_tokens": body.max_tokens,
        "auto_token_budget": body.auto_token_budget,
        "readonly": body.readonly,
        "active_preset_id": body.active_preset_id.strip(),
        "gateway": body.gateway,
        "use_mcp": body.use_mcp,
        "web_enabled": body.web_enabled,
        "web_provider": body.web_provider,
        "web_base_url": body.web_base_url.strip(),
        "web_max_results": body.web_max_results,
    }
    # Only overwrite the key when a fresh non-blank one is supplied.
    if body.api_key and body.api_key.strip():
        ai_cfg["api_key_enc"] = ai_agent.encrypt_key(body.api_key.strip())
    if body.web_api_key and body.web_api_key.strip():
        ai_cfg["web_api_key_enc"] = ai_agent.encrypt_key(body.web_api_key.strip())
    data["ai"] = ai_cfg
    storage.save_settings(data)
    return {"ok": True, **_public()}


@router.get("/models")
async def list_models() -> dict:
    """Model ids from the configured endpoint — ЛЮБОГО провайдера, не только
    шлюза CLIProxyAPI: и OpenAI-совместимые, и Anthropic отдают один и тот же
    `{"data":[{"id":…}]}`. Гейт по gateway снят (Волна 6, План C Ф2), иначе
    каталог не подгружался бы у тех, кто ходит к провайдеру напрямую.

    Никогда не ошибается: пустой список = «вводите модель вручную»."""
    cfg = ai_agent._cfg()
    _, key = ai_agent.effective_target(cfg)
    return {"models": await ai_agent.list_models(cfg, key)}


@router.post("/chat/upload")
async def chat_upload(file: UploadFile = File(...),
                      session_id: str = Form("")) -> dict:
    """Принять ОДИН файл вложения отдельным запросом (фаза 1 из двух).

    ⚠️ Ради этой ручки всё и делалось. Раньше архив ехал base64'ом в теле
    `POST /chat`, тело раздувалось до ~67 МБ, и у клиента за VPN запрос рвался на
    середине (`SSL_read() failed: bad record mac`) — без ответа, без ошибки и,
    главное, без прогресса: браузер не умеет показывать процент отправки для
    `fetch`. Здесь тело запроса — сам файл, и XHR на фронте показывает
    `upload.onprogress` честными процентами.

    `session_id` — ВЛАДЕЛЕЦ файла. Раньше загрузка принадлежала аккаунту и
    протухала через сутки: человек приложил .tsv, агент разобрал половину, а
    назавтра файла не стало ни в чате, ни где-либо ещё. Теперь файл живёт
    столько же, сколько разговор, и уходит только вместе с ним
    (`DELETE /api/ai/chat/history` → `ai_uploads.purge_session`).
    """
    content = await file.read()
    try:
        return ai_uploads.save(file.filename or "файл",
                               file.content_type or "application/octet-stream",
                               content, session_id=session_id)
    except ai_uploads.UploadError as exc:
        raise HTTPException(400, str(exc))


@router.post("/chat")
async def chat(body: ChatBody) -> StreamingResponse:
    """Запустить ответ и стримить его.

    ⚠️ Цикл агента крутится ФОНОВОЙ задачей (`ai_runs`), а этот поток лишь читает
    её буфер: обрыв соединения — перезагрузка страницы, потеря сети — работу не
    прекращает. Раньше запрос жил внутри HTTP-ответа и умирал вместе с ним.

    ⚠️ И вопрос, и ответ сервер сохраняет САМ (`ai_chat_persist`), не полагаясь
    на браузер: иначе сценарий «отправил → закрыл вкладку → вернулся через
    сутки» терял результат уже проделанной работы. Клиент пишет то же самое,
    дубли снимает `ai_chat_store.append_once`.
    """
    account_id = accounts.current_account.get() or ""
    user_id = ai_agent.users_current_id() or account_id
    session_id = body.session_id or "default"
    cfg = ai_agent._cfg(account_id)

    # Файлы, уехавшие отдельным запросом, разворачиваем в обычные вложения:
    # дальше по коду про двухфазную отправку никто знать не должен. Пропавшая
    # загрузка (удалённый разговор, чужой аккаунт, опечатка) — отказ ДО
    # запуска: молча ответить без файла значит заставить агента выдумывать его
    # содержимое.
    attachments = [a.model_dump() for a in body.attachments]
    for uid in body.upload_ids:
        item = ai_uploads.to_attachment(uid)
        if item is None:
            raise HTTPException(
                400, "Загруженный файл не найден — возможно, разговор с ним был "
                     "удалён. Приложите файл заново.")
        attachments.append(item)
    if len(attachments) > ai_agent.MAX_ATTACHMENTS:
        raise HTTPException(400, f"Не больше {ai_agent.MAX_ATTACHMENTS} файлов.")

    if not cfg.enabled:
        async def off():
            yield json.dumps({"type": "error", "message": "ИИ-агент выключен."}) + "\n"
        return StreamingResponse(off(), media_type="application/x-ndjson")

    def make_events():
        return ai_agent.run_agent(
            body.prompt, cfg, account_id,
            attachments=attachments,
            history=[m.model_dump() for m in body.history],
            session_id=session_id,
        )

    # Вопрос — СРАЗУ, до запуска: именно долгий ответ и есть тот момент, когда
    # вкладку закрывают.
    ai_chat_persist.save_question(
        account_id, session_id, body.prompt,
        files=[str(a.get("name")) for a in attachments if a.get("name")],
    )

    # `account_id` берём из ЗАМЫКАНИЯ, а не из ContextVar внутри колбэка:
    # `finish()` может позвать и `POST /chat/stop` — то есть другой запрос,
    # чей контекст к этому разговору отношения не имеет.
    def _persist(events: list[dict]) -> None:
        ai_chat_persist.save_answer(account_id, session_id, events)

    run = ai_runs.start(user_id, session_id, make_events, _persist)

    async def gen():
        async for event in ai_runs.follow(run):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/chat/state")
async def chat_state(session_id: str = "") -> dict:
    """Идёт ли ответ в этом разговоре. Клиент спрашивает на загрузке страницы:
    после F5 он не знает, оставил ли работу."""
    account_id = accounts.current_account.get() or ""
    user_id = ai_agent.users_current_id() or account_id
    run = ai_runs.get(user_id, session_id or "default")
    if run is None:
        return {"active": False, "events": 0}
    return {"active": not run.done, "events": len(run.events)}


@router.get("/chat/resume")
async def chat_resume(session_id: str = "", start: int = 0) -> StreamingResponse:
    """Переподключиться к идущему (или только что завершённому) ответу.

    Отдаём события С НАЧАЛА: после перезагрузки клиент не знает, сколько он
    успел применить, а восстановить последнюю реплику по полному списку проще и
    надёжнее, чем вести учёт смещений на диске.
    """
    account_id = accounts.current_account.get() or ""
    user_id = ai_agent.users_current_id() or account_id
    run = ai_runs.get(user_id, session_id or "default")

    async def gen():
        if run is None:
            yield json.dumps({"type": "done"}) + "\n"
            return
        async for event in ai_runs.follow(run, start):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/chat/stop")
async def chat_stop(session_id: str = "") -> dict:
    account_id = accounts.current_account.get() or ""
    user_id = ai_agent.users_current_id() or account_id
    return {"stopped": ai_runs.stop(user_id, session_id or "default")}


class CompactBody(BaseModel):
    history: list[HistoryMsg] = Field(
        default_factory=list, max_length=ai_agent.MAX_HISTORY_MESSAGES * 4)


@router.post("/compact")
async def compact_history(body: CompactBody) -> dict:
    """Сжать переписку в выжимку — команда `/compact` в чате.

    Сессии хранит клиент, поэтому сюда приезжает вся переписка целиком, а обратно
    уезжает текст, которым клиент её заменит. Сервер по-прежнему ничего не хранит.
    """
    cfg = ai_agent._cfg()
    if not cfg.enabled:
        raise HTTPException(400, "ИИ-агент выключен.")
    try:
        summary = await ai_agent.compact(
            cfg, accounts.current_account.get() or "",
            [m.model_dump() for m in body.history])
    except ai_agent.AgentError as exc:
        raise HTTPException(400, str(exc))
    return {"summary": summary}


@router.get("/tools")
async def tools_status() -> dict:
    """What the assistant can actually reach right now.

    The UI shows this ABOVE the composer so the user knows the boundaries before
    asking, rather than learning them from a refusal. Three honest states:
    built-in only / built-in + Remnawave / built-in only because MCP belongs to
    another account.
    """
    from app.services import mcp_server

    account_id = accounts.current_account.get() or ""
    cfg = ai_agent._cfg(account_id)
    ctx = ai_agent.build_context(cfg, account_id)
    names = [t.name for t in ai_tools.available(ctx)]
    base = {
        "builtin": len(names),
        "tools": names,
        "writes": not cfg.readonly,
        "web": cfg.web_enabled,
        "web_provider": ai_web.provider_label(cfg.web_provider),
    }
    if not cfg.use_mcp:
        return {**base, "mcp": 0, "reason": "off"}
    try:
        status = await mcp_server.status()
    except Exception:
        return {**base, "mcp": 0, "reason": "unavailable"}
    state = status.get("container")
    if state == "foreign":
        return {**base, "mcp": 0, "reason": "foreign"}
    if state != "running" or not status.get("reachable"):
        return {**base, "mcp": 0, "reason": "unavailable"}
    mcp = await ai_agent._mcp_tools(cfg)
    return {
        **base,
        "mcp": len(mcp),
        "capped": len(mcp) >= ai_agent.MAX_MCP_TOOLS,
        "reason": "ok" if mcp else "unavailable",
    }
