"""metrics_store._uptime_30d — correctness of the one-scan rewrite (task #3 opt).

The optimization derives the global % from per-node sums instead of a second
full-window AVG scan. That is only valid if it equals the old behaviour exactly:
global = Σonline / Σcount, which weights nodes by their sample count (NOT the
mean of per-node means). These tests pin that, plus that the query is served by
the covering index.
"""
import sqlite3
import time

import pytest

from app.services import metrics_store as ms


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "metrics.db"
    monkeypatch.setattr(ms, "_DB_PATH", str(path))
    ms._init()
    return path


def _insert2(rows):
    """rows: (ts, stable_id, online, checker_id)."""
    with ms._connect() as conn:
        conn.executemany(
            "INSERT INTO proxy_samples(ts, stable_id, name, group_name, online, "
            "latency_ms, checker_id) VALUES (?, ?, ?, '', ?, -1, ?)",
            [(ts, sid, sid, online, cid) for ts, sid, online, cid in rows],
        )


def test_per_node_uptime_percent(db):
    now = int(time.time())
    # n1: 3/4 online = 75%; n2: 1/2 online = 50%
    _insert2([
        (now - 10, "n1", 1, "local"), (now - 9, "n1", 1, "local"),
        (now - 8, "n1", 1, "local"), (now - 7, "n1", 0, "local"),
        (now - 6, "n2", 1, "local"), (now - 5, "n2", 0, "local"),
    ])
    r = ms._uptime_30d("local")
    assert r["per_node"] == {"n1": 75.0, "n2": 50.0}


def test_global_is_sample_weighted_not_mean_of_means(db):
    """The crux of the rewrite: a node with many samples must weigh more than a
    node with few. Mean-of-means would give a different (wrong) answer."""
    now = int(time.time())
    rows = []
    # n1: 100 samples, all online (100%). n2: 1 sample, offline (0%).
    for i in range(100):
        rows.append((now - 1000 - i, "n1", 1, "local"))
    rows.append((now - 5, "n2", 0, "local"))
    _insert2(rows)
    r = ms._uptime_30d("local")
    # sample-weighted: 100 online / 101 total = 99.01%
    assert r["global"] == 99.01
    # mean-of-means would be (100 + 0)/2 = 50.0 — NOT what we want
    assert r["global"] != 50.0


def test_excludes_rows_older_than_30_days(db):
    now = int(time.time())
    _insert2([
        (now - 40 * 86400, "n1", 0, "local"),   # outside the window
        (now - 10, "n1", 1, "local"),
    ])
    assert ms._uptime_30d("local")["per_node"] == {"n1": 100.0}


def test_isolated_per_checker(db):
    now = int(time.time())
    _insert2([
        (now - 10, "n1", 1, "local"),
        (now - 10, "n1", 0, "remote1"),
    ])
    assert ms._uptime_30d("local")["per_node"] == {"n1": 100.0}
    assert ms._uptime_30d("remote1")["per_node"] == {"n1": 0.0}


def test_empty_is_none(db):
    r = ms._uptime_30d("local")
    assert r == {"global": None, "per_node": {}}


def test_query_uses_the_covering_index(db):
    """Guards the optimization itself: if a schema change breaks the index, the
    query silently falls back to a table scan and the 3x speedup is gone."""
    now = int(time.time())
    _insert2([(now - 10, "n1", 1, "local")])
    since = now - 30 * 86400
    with ms._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT stable_id, SUM(online), COUNT(*) "
            "FROM proxy_samples WHERE ts >= ? AND checker_id = ? GROUP BY stable_id",
            (since, "local"),
        ).fetchall()
    detail = " ".join(row["detail"] for row in plan)
    assert "COVERING INDEX" in detail
    # a grouped covering scan needs no temporary B-tree sort
    assert "USE TEMP B-TREE" not in detail
