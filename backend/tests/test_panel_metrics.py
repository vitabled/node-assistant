"""Wave-4 Plan C — Prometheus-text parser + summary (pure, no SSH)."""
from app.services import panel_metrics

# A trimmed but realistic Remnawave /metrics fixture (comments, labels, ±Inf/NaN,
# an optional timestamp, and an unrelated gauge to prove we don't over-collect).
FIXTURE = """\
# HELP remnawave_users_online_stats Online distinct users
# TYPE remnawave_users_online_stats gauge
remnawave_users_online_stats 42
# HELP remnawave_users_status Users per status
# TYPE remnawave_users_status gauge
remnawave_users_status{status="ACTIVE"} 100
remnawave_users_status{status="DISABLED"} 7
remnawave_users_status{status="LIMITED"} 3
remnawave_users_status{status="EXPIRED"} 12
# TYPE remnawave_node_status gauge
remnawave_node_status{node_uuid="a1",node_name="DE-1"} 1
remnawave_node_status{node_uuid="b2",node_name="US-1"} 0
remnawave_node_status{node_uuid="c3",node_name="FI-1"} 1 1720000000000
process_cpu_seconds_total 1234.5
some_broken_metric NaN
inf_metric +Inf
"""


def test_parse_prometheus_basic():
    parsed = panel_metrics.parse_prometheus(FIXTURE)
    assert "remnawave_users_online_stats" in parsed
    assert parsed["remnawave_users_online_stats"][0]["value"] == 42
    # labels parsed
    statuses = {s["labels"]["status"]: s["value"] for s in parsed["remnawave_users_status"]}
    assert statuses == {"ACTIVE": 100, "DISABLED": 7, "LIMITED": 3, "EXPIRED": 12}
    # optional timestamp ignored (value still 1)
    fi = [s for s in parsed["remnawave_node_status"] if s["labels"]["node_name"] == "FI-1"][0]
    assert fi["value"] == 1
    # NaN / +Inf samples dropped
    assert "some_broken_metric" not in parsed
    assert "inf_metric" not in parsed
    # unrelated metric still parsed (we don't filter at parse time)
    assert "process_cpu_seconds_total" in parsed


def test_summarize():
    s = panel_metrics.summarize(panel_metrics.parse_prometheus(FIXTURE))
    assert s["users_online"] == 42
    assert s["users_by_status"]["ACTIVE"] == 100
    assert s["nodes_total"] == 3
    assert s["nodes_online"] == 2  # DE-1 + FI-1
    assert s["metric_count"] >= 4
    names = {n["name"] for n in s["nodes"]}
    assert {"DE-1", "US-1", "FI-1"} <= names


def test_summarize_missing_metrics_graceful():
    # A panel version that renamed everything → empty/zero, never raises.
    s = panel_metrics.summarize(panel_metrics.parse_prometheus("other_metric 1\n"))
    assert s["users_online"] is None
    assert s["users_by_status"] == {}
    assert s["nodes_total"] == 0 and s["nodes_online"] == 0


def test_scrape_script_has_no_hardcoded_secret():
    # The script reads creds from the box .env; it must not embed any secret and
    # must target loopback only.
    script = panel_metrics.metrics_scrape_script()
    assert "127.0.0.1" in script
    assert "METRICS_USER" in script and "METRICS_PASS" in script
    # no obvious literal password/token baked in
    assert "password" not in script.lower()
