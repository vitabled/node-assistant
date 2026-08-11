"""Проверка ВЫХОДНОГО IP ноды подписки (Wave-4 PR-8).

Анализ подписки резолвит ВХОДНОЙ адрес — но для релейных нод (вход в одной
стране, выход в другой) реальный egress виден только из-за ноды. Здесь xray
поднимается ЛОКАЛЬНО на backend'е (бинарь лениво скачивается и кэшируется в
DATA_DIR), трафик ip-api.com заворачивается в socks-туннель ссылки, и ответ
даёт выходной IP + его гео/организацию.

Безопасность: конфиг пишется во временный файл с правами 600 и удаляется;
сырая ссылка никуда не логируется (parse_xray_link даёт ошибки без фрагментов).
Одновременных проверок — не больше _SEM, каждая с жёстким таймаутом и kill.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import stat
import tempfile
import zipfile
from pathlib import Path

import httpx

from app.services import accounts
from app.services.test_tools import parse_xray_link

_SEM = asyncio.Semaphore(3)
_TUNNEL_WAIT = 15          # секунд на подъём socks-туннеля
_QUERY_TIMEOUT = 20        # секунд на запрос egress-инфо
_EGRESS_URL_HOST = "ip-api.com"
_EGRESS_FIELDS = "status,query,countryCode,city,as,org,isp,hosting,proxy"

_XRAY_RELEASE = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"


# ── бинарь xray (ленивое скачивание + кэш) ─────────────────────
def _xray_path() -> Path:
    # Глобальный (не per-account) кэш: бинарь общий для всех.
    return accounts.DATA_DIR / "xray-bin" / "xray"


async def ensure_xray_binary() -> Path:
    """Путь к исполняемому xray; при первом обращении скачивает официальный
    релиз XTLS/Xray-core (тот же источник, что у test-tools на нодах)."""
    p = _xray_path()
    if p.exists() and os.access(p, os.X_OK):
        return p
    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        raise RuntimeError(f"Авто-установка xray поддерживает только x86_64 (у нас {machine})")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / "xray-download.zip"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(_XRAY_RELEASE)
            r.raise_for_status()
            tmp.write_bytes(r.content)
        with zipfile.ZipFile(tmp) as z:
            data = z.read("xray")
        p.write_bytes(data)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return p
    except Exception as exc:
        raise RuntimeError(f"Не удалось скачать xray-core: {exc}") from None
    finally:
        tmp.unlink(missing_ok=True)


# ── минимальный SOCKS5-клиент (без внешних зависимостей) ───────
async def _socks_http_get(proxy_port: int, host: str, path: str, timeout: float) -> bytes:
    """Один HTTP GET через SOCKS5 (no-auth) на 127.0.0.1. Используем CONNECT по
    ДОМЕНУ (ATYP=3) — резолв уходит на выходную сторону туннеля, как и положено."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", proxy_port), timeout=timeout)
    try:
        writer.write(b"\x05\x01\x00")                     # greeting: 1 метод (no-auth)
        await writer.drain()
        resp = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        if resp != b"\x05\x00":
            raise RuntimeError("SOCKS5: сервер не принял no-auth")
        hb = host.encode("idna")
        writer.write(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + b"\x00\x50")
        await writer.drain()
        head = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        if head[1] != 0x00:
            raise RuntimeError(f"SOCKS5: CONNECT отклонён (код {head[1]})")
        atyp = head[3]
        if atyp == 1:
            await reader.readexactly(4 + 2)
        elif atyp == 3:
            ln = (await reader.readexactly(1))[0]
            await reader.readexactly(ln + 2)
        elif atyp == 4:
            await reader.readexactly(16 + 2)
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: node-assistant\r\n"
            "Accept: application/json\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        chunks = []
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _http_body(raw: bytes) -> bytes:
    """Тело HTTP-ответа (без chunked-декодирования — ip-api шлёт identity)."""
    head, _, body = raw.partition(b"\r\n\r\n")
    if b"chunked" in head.lower():
        out = bytearray()
        rest = body
        while rest:
            size_line, _, rest = rest.partition(b"\r\n")
            try:
                size = int(size_line.strip(), 16)
            except ValueError:
                break
            if size == 0:
                break
            out += rest[:size]
            rest = rest[size + 2:]
        return bytes(out)
    return body


# ── проверка одной ссылки ──────────────────────────────────────
async def check_egress(link: str, *, xray_bin: Path | None = None,
                       socks_port: int = 10808) -> dict:
    """share-link → выходной IP + гео/организация через туннель xray.
    Возвращает {ok, egress:{ip,cc,city,org,isp,hosting,proxy} | error}."""
    try:
        cfg = parse_xray_link(link)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    cfg["inbounds"][0]["port"] = socks_port

    bin_path = xray_bin or await ensure_xray_binary()
    async with _SEM:
        fd, cfg_path = tempfile.mkstemp(prefix="nai-egress-", suffix=".json")
        proc = None
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cfg, f)
            os.chmod(cfg_path, 0o600)
            proc = await asyncio.create_subprocess_exec(
                str(bin_path), "run", "-c", cfg_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

            # ждём подъёма туннеля: CONNECT через socks на cp.cloudflare.com
            up = False
            for _ in range(_TUNNEL_WAIT):
                try:
                    await _socks_http_get(socks_port, "cp.cloudflare.com", "/", 3)
                    up = True
                    break
                except Exception:
                    await asyncio.sleep(1)
            if not up:
                return {"ok": False, "error": "Туннель не поднялся — нода не отвечает или ссылка нерабочая"}

            raw = await _socks_http_get(
                socks_port, _EGRESS_URL_HOST, f"/json/?fields={_EGRESS_FIELDS}", _QUERY_TIMEOUT)
            d = json.loads(_http_body(raw).decode("utf-8", "replace"))
            if not isinstance(d, dict) or d.get("status") != "success":
                return {"ok": False, "error": "Сервис определения IP не ответил"}
            return {"ok": True, "egress": {
                "ip": str(d.get("query") or ""),
                "cc": str(d.get("countryCode") or ""),
                "city": str(d.get("city") or ""),
                "org": str(d.get("org") or d.get("isp") or ""),
                "isp": str(d.get("isp") or ""),
                "as": str(d.get("as") or ""),
                "hosting": bool(d.get("hosting")),
                "proxy": bool(d.get("proxy")),
            }}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Таймаут проверки выхода"}
        except Exception as exc:
            return {"ok": False, "error": f"Проверка выхода не удалась: {exc}"}
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            try:
                os.unlink(cfg_path)
            except OSError:
                pass
