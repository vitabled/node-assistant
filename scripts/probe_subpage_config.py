#!/usr/bin/env python3
"""Снять с ЖИВОЙ панели Remnawave форму поля `config` у subscription-page-configs
и — главное — семантику PATCH по нему (merge или replace).

От этого зависят три последние вещи Плана G (Ф6 / редактор config / селектор
варианта). Токен нигде не печатается; сами конфиги секретов не содержат.

Запуск (Git Bash или PowerShell), панель и токен — через окружение ИЛИ аргументы:
    PANEL=https://panel.example TOKEN=eyJ... python scripts/probe_subpage_config.py
    python scripts/probe_subpage_config.py https://panel.example eyJ...

Перед запуском СОЗДАЙТЕ в UI панели любое оформление с непустым содержимым —
create принимает только {name}, поэтому наполнить config скриптом нельзя.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

PANEL = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PANEL", "")).rstrip("/")
TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TOKEN", "")

if not PANEL or not TOKEN:
    sys.exit("Укажите панель и токен: PANEL=... TOKEN=... python scripts/probe_subpage_config.py")

# Некоторые панели за self-signed — не падаем на проверке цепочки (мы только читаем).
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def call(method: str, path: str, body: dict | None = None):
    url = PANEL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode()[:400]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": str(e)[:400]}


def shape(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, dict):
        return f"object · ключи верхнего уровня: {sorted(v)[:20]}"
    if isinstance(v, list):
        return f"array · длина {len(v)}"
    if isinstance(v, str):
        return f"string · длина {len(v)}"
    return type(v).__name__


print("=" * 70)
print("1) ЛИСТИНГ subscription-page-configs")
st, data = call("GET", "/api/subscription-page-configs")
print(f"   HTTP {st}")
resp = (data or {}).get("response", data)
configs = (resp or {}).get("configs", []) if isinstance(resp, dict) else []
print(f"   total={((resp or {}) if isinstance(resp, dict) else {}).get('total')} · получено записей: {len(configs)}")
if not configs:
    print("   !! Список пуст. Создайте в UI панели одно оформление с содержимым и повторите.")
    if isinstance(data, dict) and data.get("_error"):
        print("   ответ:", data["_error"])
    sys.exit(1)

for c in configs:
    print(f"   - {c.get('uuid')} · name={c.get('name')!r} · viewPosition={c.get('viewPosition')} · config: {shape(c.get('config'))}")

# Берём запись с НЕпустым config — на ней и проверяем PATCH.
target = next((c for c in configs if c.get("config") not in (None, {}, [])), configs[0])
uuid = target["uuid"]
before = target.get("config")
print()
print("=" * 70)
print(f"2) ГЛАВНОЕ: PATCH только с name на {uuid} — переживёт ли config?")
print(f"   config ДО: {shape(before)}")
if before in (None, {}, []):
    print("   !! У выбранной записи config пустой — тест нерепрезентативен.")
    print("      Наполните оформление в UI и повторите.")

st, _ = call("PATCH", "/api/subscription-page-configs",
             {"uuid": uuid, "name": (target.get("name") or "cfg")})
print(f"   PATCH HTTP {st}")
st, data = call("GET", f"/api/subscription-page-configs/{uuid}")
after = ((data or {}).get("response", data) or {}).get("config") if isinstance(data, dict) else None
print(f"   config ПОСЛЕ: {shape(after)}")
verdict = "СОХРАНИЛСЯ (merge — переименование безопасно)" if after not in (None, {}, []) \
    else "ОБНУЛЁН (replace — переименование сотрёт оформление!)"
print(f"   >>> ВЕРДИКТ: config после name-only PATCH {verdict}")

print()
print("=" * 70)
print("3) Привязка config → пользователь идёт через ВНЕШНИЙ сквад?")
st, data = call("GET", "/api/external-squads")
resp = (data or {}).get("response", data)
squads = resp if isinstance(resp, list) else (resp or {}).get("internalSquads", resp) if isinstance(resp, dict) else []
print(f"   HTTP {st}")
if isinstance(squads, list) and squads:
    for s in squads[:5]:
        if isinstance(s, dict):
            print(f"   - squad {s.get('uuid')} · subpageConfigUuid={s.get('subpageConfigUuid')}")
    has = any(isinstance(s, dict) and "subpageConfigUuid" in s for s in squads)
    print(f"   >>> поле subpageConfigUuid у внешних сквадов: {'ЕСТЬ' if has else 'нет'}")
else:
    print("   внешних сквадов нет или иная форма ответа:", shape(resp))

print()
print("=" * 70)
print("4) Полная форма ОДНОЙ записи config (для редактора Ф3):")
print(json.dumps(target.get("config"), ensure_ascii=False, indent=2)[:3000])
print()
print("Готово. Пришлите вывод целиком — токена в нём нет.")
