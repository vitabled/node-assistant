"""Сброс пароля суперпользователя с хоста: ``python -m app.reset_admin``.

Владелец панели может потерять пароль, а спросить его больше не у кого: своей
регистрации нет, а сменить пароль в «Настройки → Пользователи» может только тот,
кто уже вошёл. Единственный оставшийся путь — хост.

## Почему это НЕ ослабление модели доступа

⚠️ Не «закрывать ради безопасности» при следующем ревью: закрытие ничего не
защитит, а починку сломает.

У кого есть shell на хосте, тот читает `.env` (значит `ENCRYPTION_KEY`), а с ним
и все Fernet-волты, и подпись сессионных токенов. То есть он УЖЕ может выписать
себе JWT любого пользователя, включая владельца, и никакая наша проверка этому не
мешает. Ролевая модель защищает установку от людей, у которых есть доступ к
панели, а не от того, у кого есть root на её сервере.

Поэтому команда не добавляет доступа — она делает законный путь удобнее
незаконного. Без неё администратор в той же ситуации полез бы редактировать
`users.json` руками (и уронил бы реестр) либо подделывать токен.

## Что команда осознанно НЕ делает

* **Не создаёт владельца в пустом реестре.** Тихое создание из CLI стало бы
  вторым путём первичной настройки — со своими проверками, которые пришлось бы
  поддерживать наравне с ручкой `/api/auth/bootstrap`. Пустой реестр = панель
  ещё не настроена, и настраивается она визардом.
* **Не имеет `--reopen-bootstrap`.** Снести владельца ради визарда — значит
  открыть окно, в котором владельцем становится любой, кто в это время дошёл до
  панели по сети.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from typing import Optional

from app.services import users

# Пароль будут перечитывать с экрана и переносить руками, поэтому из алфавита
# убраны неразличимые начертания: 0/O/o и 1/l/I.
_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GENERATED_LEN = 20


def _generate() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_GENERATED_LEN))


def _read_password() -> str:
    """Пароль читается ТОЛЬКО отсюда — из stdin.

    Аргумента `--password` нет и не должно появиться: значение из argv видно в
    `/proc/<pid>/cmdline` любому процессу на хосте и остаётся в истории оболочки.
    Это то же правило, по которому секреты уходят на ноду через
    `SSHSession.get_script_output` (stdin), а не аргументом команды.

    Края обрезаем: перевод строки в конце добавит любой канал доставки
    (`read` в оболочке, `printf` через pipe), а отличить его от намеренного
    пробела в пароле здесь нечем.
    """
    return sys.stdin.read().strip()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.reset_admin",
        description="Сбросить пароль суперпользователя панели.",
    )
    p.add_argument("--login", default="",
                   help="логин суперпользователя (обязателен, если их несколько)")
    p.add_argument("--password-stdin", action="store_true",
                   help="прочитать новый пароль из stdin")
    p.add_argument("--keep-tokens", action="store_true",
                   help="не отзывать API-токены пользователя")
    p.add_argument("--generate", action="store_true",
                   help="сгенерировать пароль и напечатать его один раз")
    return p


def _pick(supers: list[dict], login: str) -> Optional[dict]:
    key = login.strip().lower()
    return next((u for u in supers if u["login"].strip().lower() == key), None)


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _revoke_tokens(target: dict) -> int:
    """Снять все API-токены пользователя. Возвращает сколько снято.

    Идём прямо в стор, а не через `api_tokens.revoke`: у того идентичность берётся
    из ContextVar запроса, которого в CLI нет.
    """
    try:
        from app.services import storage, users as _users

        ws = _users.workspace_of(target)
        toks = storage.load_api_tokens(ws)
        kept = [t for t in toks if t.get("user_id") != target["id"]]
        removed = len(toks) - len(kept)
        if removed:
            storage.save_api_tokens(kept, ws)
        return removed
    except Exception:  # noqa: BLE001 — пароль уже сменён, ронять из-за токенов нельзя
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)

    try:
        registry = users.list_users()
    except users.UserError as exc:
        return _fail(f"ошибка: {exc}")

    if not registry:
        return _fail("Реестр пуст, откройте панель и пройдите первичную настройку.")

    supers = [u for u in registry if u.get("is_superuser")]
    if not supers:
        return _fail("В реестре нет суперпользователя — восстановите users.json из бэкапа.")

    if args.login:
        target = _pick(supers, args.login)
        if target is None:
            return _fail(
                f"Суперпользователя «{args.login.strip()}» нет. Есть: "
                + ", ".join(u["login"] for u in supers)
            )
    elif len(supers) == 1:
        target = supers[0]
    else:
        # Контекст уже привилегированный (shell на хосте), скрывать логины не от
        # кого — а угадывать, чей пароль сбросить, нельзя.
        print("Суперпользователей несколько — укажите --login:", file=sys.stderr)
        for u in supers:
            print(f"  {u['login']}", file=sys.stderr)
        return 1

    if args.generate:
        password = _generate()
    elif args.password_stdin:
        password = _read_password()
        if not password:
            return _fail("В stdin не пришло пароля.")
    else:
        return _fail("Укажите --password-stdin (пароль читается из stdin) или --generate.")

    try:
        # set_password сам бампит token_version: сбрасывают пароль ровно тогда,
        # когда доступ надо отобрать, а сессионный JWT живёт без срока.
        users.set_password(target["id"], password)
    except users.UserError as exc:
        return _fail(f"ошибка: {exc}")

    # ⚠️ Смена пароля НЕ убивает API-токены (`nai_…`): они резолвятся по
    # HMAC-дайджесту секрета, а не по версии сессии — на то и рассчитаны, чтобы
    # переживать перелогины. Но сбрасывают пароль как раз тогда, когда доступ
    # надо отобрать целиком, и оставить работающий долгоживущий токен значило бы
    # сделать сброс декоративным. Поэтому отзываем их тоже, а `--keep-tokens`
    # оставлен для случая «просто забыл пароль, интеграции ломать не надо».
    revoked = 0
    if not args.keep_tokens:
        revoked = _revoke_tokens(target)

    print(f"Пароль пользователя «{target['login']}» изменён.")
    print("Прежние сессии этого пользователя больше не действуют.")
    if args.keep_tokens:
        print("API-токены оставлены (--keep-tokens): они продолжат работать.")
    elif revoked:
        print(f"Отозвано API-токенов: {revoked} — интеграции нужно перевыпустить.")
    if args.generate:
        print()
        print(f"  новый пароль: {password}")
        print("  Он нигде не сохранён — скопируйте его сейчас.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
