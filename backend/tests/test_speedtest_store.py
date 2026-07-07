"""Ф2 (wave1) — speedtest history store: record/history/latest/retention/isolation."""

import asyncio
import time
import uuid

from app.services import speedtest_store as store


def _acc() -> str:
    return f"acc-{uuid.uuid4().hex[:8]}"


def test_record_and_history_roundtrip_newest_first():
    acc = _acc()
    asyncio.run(
        store.record_run(
            acc,
            {
                "resource_key": "1.2.3.4",
                "kind": "node",
                "iperf_mbps": 940.5,
                "iperf_jitter": 0.8,
                "ping_ms": 12.3,
                "traceroute": "1 10.0.0.1 0.5 ms",
                "st_down": 850.1,
                "st_up": 300.2,
                "st_ping": 9.9,
                "xray_down": 120.0,
                "xray_up": 80.0,
                "xray_ping": 45.0,
                "cpu": "4 × Intel Xeon",
                "ram_mb": 7936,
                "disk": "40G · использовано 12G (32%)",
            },
        )
    )
    asyncio.run(
        store.record_run(
            acc, {"resource_key": "1.2.3.4", "kind": "node", "iperf_mbps": 500.0}
        )
    )
    hist = asyncio.run(store.history(acc, "1.2.3.4"))
    assert len(hist) == 2
    assert hist[0]["iperf_mbps"] == 500.0  # newest first
    assert hist[0]["st_down"] is None  # missing fields stay null
    assert hist[1]["st_down"] == 850.1
    assert hist[1]["cpu"] == "4 × Intel Xeon"
    assert hist[1]["ram_mb"] == 7936
    assert hist[1]["traceroute"] == "1 10.0.0.1 0.5 ms"
    assert hist[0]["ts"] >= hist[1]["ts"]


def test_latest_and_empty_history():
    acc = _acc()
    assert asyncio.run(store.history(acc, "9.9.9.9")) == []
    assert asyncio.run(store.latest(acc, "9.9.9.9")) is None
    asyncio.run(store.record_run(acc, {"resource_key": "9.9.9.9", "iperf_mbps": 1.0}))
    latest = asyncio.run(store.latest(acc, "9.9.9.9"))
    assert latest["iperf_mbps"] == 1.0
    assert latest["kind"] == "node"  # default kind


def test_history_limit_and_key_isolation():
    acc = _acc()
    for i in range(5):
        asyncio.run(
            store.record_run(acc, {"resource_key": "1.1.1.1", "iperf_mbps": float(i)})
        )
    asyncio.run(store.record_run(acc, {"resource_key": "2.2.2.2", "iperf_mbps": 777.0}))
    assert len(asyncio.run(store.history(acc, "1.1.1.1", limit=3))) == 3
    hist = asyncio.run(store.history(acc, "1.1.1.1"))
    assert len(hist) == 5
    assert all(h["resource_key"] == "1.1.1.1" for h in hist)


def test_retention_90_days():
    acc = _acc()
    # seed an old row directly (controlled ts); a fresh write purges it
    with store._connect(acc) as conn:
        conn.execute(
            "INSERT INTO runs (ts, resource_key, kind) VALUES (?, ?, ?)",
            (int(time.time()) - 91 * 86400, "1.2.3.4", "node"),
        )
    asyncio.run(store.record_run(acc, {"resource_key": "1.2.3.4", "iperf_mbps": 2.0}))
    hist = asyncio.run(store.history(acc, "1.2.3.4", limit=50))
    assert len(hist) == 1
    assert hist[0]["iperf_mbps"] == 2.0


def test_account_isolation():
    a, b = _acc(), _acc()
    asyncio.run(store.record_run(a, {"resource_key": "1.2.3.4", "iperf_mbps": 10.0}))
    assert asyncio.run(store.history(b, "1.2.3.4")) == []
    assert len(asyncio.run(store.history(a, "1.2.3.4"))) == 1


def test_unknown_row_keys_are_ignored():
    acc = _acc()
    asyncio.run(
        store.record_run(
            acc, {"resource_key": "3.3.3.3", "bogus": "x", "iperf_mbps": 5.0}
        )
    )
    assert asyncio.run(store.latest(acc, "3.3.3.3"))["iperf_mbps"] == 5.0
