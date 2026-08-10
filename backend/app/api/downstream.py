"""Преобразование downstream-ошибок (Remnawave, Cloudflare) в HTTP-ответы.

⚠️ 401/403 от внешнего сервиса НИКОГДА не уходят наружу. apiClient фронтенда
считает любой 401 на /api смертью сессии и разлогинивает пользователя —
поэтому панель с протухшим токеном раньше выбивала оператора в бесконечный
логаут: добавил панель → check вернул 401 панели → потерял свою сессию.
Downstream-401 — это проблема шлюза, а не сессии: отдаём 502 с исходным
статусом в тексте, чтобы диагноз читался.
"""

from fastapi import HTTPException


def downstream_exception(status: int | None, detail: str, service: str = "Сервис") -> HTTPException:
    """HTTPException для ошибки внешнего сервиса. 401/403 → 502 с пометкой."""
    if status in (401, 403):
        return HTTPException(502, f"{service} ответил {status}: {detail}")
    return HTTPException(status or 502, detail)
