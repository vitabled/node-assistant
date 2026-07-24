"""Wave-7 Plan G Ф6 — materialising an overlay variant on the node.

Most of the risk is in the SHELL these generators emit: it runs as root on the
box, and the bind-mount experiment (see the plan) showed the literal `rm -rf
<dir>` from the plan is wrong on two counts. These tests pin the corrected shape.
"""
from app.models.panel_deploy import PanelDeployRequest
from app.services import panel_pipeline as pp

# NOTE: no asyncssh stub. It is a real dependency (ssh_manager evaluates
# `asyncssh.SSHReader` at class-body time), and the empty `setdefault` stub used
# by some sibling tests makes this file un-runnable on its own.


def _req(**over):
    base = dict(target="both", ip="203.0.113.1", ssh_password="x",
               panel_domain="p.example", sub_domain="s.example",
               email="a@b.co", cf_api_key="cf", reverse_proxy="caddy",
               subpage_api_token="tok")
    base.update(over)
    return PanelDeployRequest(**base)


# ── compose volume shape ──────────────────────────────────────
def test_overlay_mounts_the_directory():
    req = _req(subpage_variant_id="abc123def456")
    compose = pp._subpage_compose(req)
    assert "- ./frontend:/opt/app/frontend" in compose
    assert "index.html:/opt/app/frontend/index.html" not in compose


def test_legacy_html_still_mounts_the_file():
    req = _req(subpage_html="<!doctype html><%- panelData %>")
    compose = pp._subpage_compose(req)
    assert "- ./index.html:/opt/app/frontend/index.html" in compose


def test_variant_wins_over_legacy_html():
    """Both set → the variant's directory mount, not the file mount."""
    req = _req(subpage_variant_id="v1", subpage_html="<%- panelData %>")
    compose = pp._subpage_compose(req)
    assert "- ./frontend:/opt/app/frontend" in compose
    assert "index.html:/opt/app/frontend/index.html" not in compose


def test_no_subpage_has_no_volume():
    compose = pp._subpage_compose(_req())
    assert "volumes:" not in compose


# ── materialisation script ────────────────────────────────────
def test_materialize_uses_find_delete_not_rm_rf():
    """`rm -rf <dir>` returns rc=1 under a live mount (aborts set -e) and empties
    it under the running container; `find -mindepth 1 -delete` does neither."""
    s = pp._materialize_frontend_script("remnawave/subscription-page:7.2.6")
    assert "find /opt/remnawave-subpage/frontend -mindepth 1 -delete" in s
    assert "rm -rf /opt/remnawave-subpage/frontend\n" not in s
    assert "rm -rf /opt/remnawave-subpage/frontend " not in s


def test_materialize_creates_before_inspecting():
    """docker create auto-pulls a missing image; docker cp needs the container."""
    s = pp._materialize_frontend_script("img:1")
    assert "docker create" in s and "docker cp" in s
    assert s.index("docker create") < s.index("docker cp")


def test_materialize_quotes_the_image_and_removes_the_container():
    s = pp._materialize_frontend_script("evil; rm -rf /")
    assert "'evil; rm -rf /'" in s
    assert "docker rm -f" in s and "trap" in s


def test_digest_check_only_warns():
    s = pp._digest_check_script("img:1", "sha256:expected")
    assert "ВНИМАНИЕ" in s
    assert "exit 1" not in s   # mismatch is never fatal


def test_unzip_rejects_unsafe_members_and_cleans_up():
    s = pp._unzip_overlay_script("/tmp/o.zip")
    assert "unzip -o" in s
    assert "Небезопасный путь" in s and "exit 1" in s
    # shlex.quote leaves a metacharacter-free path unquoted.
    assert "rm -f /tmp/o.zip" in s


def test_unzip_quotes_a_hostile_remote_path():
    s = pp._unzip_overlay_script("/tmp/a b;rm -rf /.zip")
    assert "'/tmp/a b;rm -rf /.zip'" in s


def test_force_recreate_only_for_overlay(monkeypatch):
    # The `up` command is chosen inline in _install_subpage; assert the helper
    # that decides is wired to the variant flag.
    assert pp._subpage_uses_overlay(_req(subpage_variant_id="v1")) is True
    assert pp._subpage_uses_overlay(_req(subpage_html="<%- panelData %>")) is False
    assert pp._subpage_uses_overlay(_req()) is False


# ── model ─────────────────────────────────────────────────────
def test_variant_id_defaults_empty_and_accepts_a_value():
    assert _req().subpage_variant_id == ""
    assert _req(subpage_variant_id="deadbeef0000").subpage_variant_id == "deadbeef0000"
