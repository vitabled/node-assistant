"""Ручки durable-переписки ассистента: GET/POST/DELETE /api/ai/chat/history.

⚠️ ОТДЕЛЬНЫЙ модуль, хотя префикс общий с `api/ai.py`. Причина не в стиле, а в
предмете: `api/ai.py` — это КОНФИГУРАЦИЯ агента и запуск ответа (провайдер,
ключи, лимиты, стрим), а здесь ХРАНИЛИЩЕ переписки, у которого своя модель
данных и своя судьба. Тот же приём уже применён к `api/ai_prompts.py`.

Что решаем. Переписка жила в браузере (`aiSessions.ts`) и умирала вместе с его
хранилищем: Safari стирает данные сайтов, куда не заходили неделю, приватное
окно не переживает вкладки, «очистить данные» уносит всё. Сервер историю не
хранил принципиально — и это было верно, пока цена вопроса была «переспросить»;
но при длинной работе с панелью терялся весь контекст задачи.

Границы ровно те же, что у остального per-account: переписка лежит в каталоге
аккаунта, чужую не отдаём и отдать не можем — `account_id` берётся из токена
(ContextVar `current_account`), а не из тела запроса.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, ai_chat_store

router = APIRouter(prefix="/api/ai")


def _account() -> str:
    aid = accounts.current_account.get() or ""
    if not aid:
        # До сюда без токена не дойти (роутер под `_auth` в main.py), но если
        # ContextVar пуст — писать некуда, и молча складывать переписку в чужой
        # каталог нельзя.
        raise HTTPException(401, "Нет активного аккаунта.")
    return aid


class HistoryMsgIn(BaseModel):
    """Реплика в том виде, в каком её присылает фронт.

    `files`/`tools` — довески для ГЛАЗА, не для модели: без них восстановленная
    переписка потеряла бы имена вложений и значки инструментов и выглядела бы
    беднее той, что была до чистки браузера.
    """
    role: str = Field("user", max_length=16)
    content: str = Field("", max_length=ai_chat_store.MAX_CONTENT_CHARS)
    ts: int = 0
    files: list[str] = Field(default_factory=list, max_length=20)
    tools: list[dict] = Field(default_factory=list, max_length=50)

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ai_chat_store.ROLES:
            raise ValueError(f"role должен быть одним из {ai_chat_store.ROLES}")
        return v


class HistoryBody(BaseModel):
    session_id: str = Field("", max_length=ai_chat_store.MAX_SESSION_ID)
    # Потолок вдвое выше хранимого: клиент вправе прислать больше, сервер
    # оставит хвост. Отказ на «слишком длинную» переписку означал бы, что
    # мигрировать её нельзя вовсе.
    messages: list[HistoryMsgIn] = Field(
        default_factory=list, max_length=ai_chat_store.MAX_MESSAGES * 2)
    #: `false` (по умолчанию) — перезаписать разговор целиком: этим фронт
    #: заливает найденное в localStorage при миграции. `true` — дописать: этим
    #: он сохраняет каждую новую реплику по ходу разговора.
    append: bool = False


@router.get("/chat/history")
async def get_history(session_id: str = "", all_sessions: bool = False) -> dict:
    """Переписка с сервера — источник истины.

    `all_sessions=true` отдаёт ВСЕ разговоры: этим клиент восстанавливается
    после чистки браузера, когда он не знает даже их идентификаторов.
    """
    aid = _account()
    if all_sessions:
        return {"sessions": ai_chat_store.all_sessions(aid)}
    return ai_chat_store.get_session(aid, session_id)


@router.get("/chat/sessions")
async def list_history_sessions() -> dict:
    """Оглавление без реплик — для списка разговоров."""
    return {"sessions": ai_chat_store.list_sessions(_account())}


@router.post("/chat/history")
async def save_history(body: HistoryBody) -> dict:
    aid = _account()
    msgs = [m.model_dump() for m in body.messages]
    if body.append:
        # ⚠️ `dedup=True`: ту же реплику сервер УЖЕ мог сохранить сам, по
        # завершении ответа (`ai_chat_persist`) — ради сценария с закрытой
        # вкладкой. Клиент об этом не знает и пишет как писал; повтор на стыке
        # снимается здесь, а не переговорами между ними.
        return ai_chat_store.append_messages(aid, body.session_id, msgs,
                                             dedup=True)
    return ai_chat_store.replace_session(aid, body.session_id, msgs)


@router.delete("/chat/history")
async def delete_history(session_id: str = "", all_sessions: bool = False) -> dict:
    """Забыть разговор. Пустой `session_id` — это `default`, а НЕ «все»:
    стереть всю историю опечаткой в запросе было бы слишком легко.
    """
    aid = _account()
    if all_sessions:
        ai_chat_store.clear_all(aid)
        return {"cleared": True}
    return {"cleared": ai_chat_store.clear_session(aid, session_id)}
