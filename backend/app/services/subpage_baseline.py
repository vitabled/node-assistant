"""Baseline of the subscription-page frontend, pulled out of the live image (Ф4).

An overlay variant only makes sense against something: this module fetches the
`/opt/app/frontend` tree that `remnawave/subscription-page` ships (verified on
7.2.6: `index.html` + `assets/`, 160 files, ~7 MB) and caches it per image
digest, so the editor can show what a file looks like before it is overridden.

The cache is GLOBAL, not per-account: it holds nothing but the vendor's own
build, keyed by the digest that produced it. Two accounts pulling the same image
share one copy, and a re-pull of a digest we already have is a no-op.

⚠️ Order of docker commands matters. Measured on Docker 29: `docker create`
AUTO-PULLS a missing image, while `docker image inspect` does NOT (it fails with
"No such image"). So the digest is resolved AFTER the container is created, never
before — the plan had it the other way round and would have failed on a clean node.

⚠️ The tarball is produced on someone else's machine. Every member is validated
before extraction (no absolute paths, no `..`, no symlinks/hardlinks/devices,
size budget) — `tarfile` will happily write outside the destination otherwise.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import tarfile
import time
from pathlib import Path
from typing import Any, Optional

from app.services import accounts

# The vendor build is ~7 MB / 160 files; leave room for a fatter future release
# but not for a mistake.
MAX_BASELINE_BYTES = 64 * 1024 * 1024
MAX_BASELINE_FILES = 2000
_REMOTE_DIR = "/tmp/na-subpage-baseline"
_ARCHIVE = f"{_REMOTE_DIR}/frontend.tgz"
IMAGE_PATH = "/opt/app/frontend"


class BaselineError(Exception):
    pass


def _root() -> Path:
    d = accounts.DATA_DIR / "subpage_baselines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def digest_dir(digest: str) -> Path:
    """Directory for one digest. `digest` is never interpolated raw — it is
    reduced to a filesystem-safe token first."""
    return _root() / safe_digest(digest)


def safe_digest(digest: str) -> str:
    """`sha256:abc…` → `sha256_abc…`, restricted to [A-Za-z0-9._-].

    A digest reaches us from a remote `docker inspect`, so it is untrusted input
    that ends up as a path segment."""
    token = "".join(c if (c.isalnum() or c in "._-") else "_" for c in (digest or ""))
    token = token.strip("._-")[:100]
    if not token:
        raise BaselineError("Пустой digest образа")
    return token


# ── remote script generators (pure) ───────────────────────────

def extract_tree_script(image: str) -> str:
    """Create a throwaway container, copy the frontend out, tar it up.

    Emits the resolved digest on stdout as `DIGEST=…` — resolved AFTER
    `docker create`, because inspect alone will not pull.
    """
    img = shlex.quote(image)
    d = shlex.quote(_REMOTE_DIR)
    return f"""set -euo pipefail
rm -rf {d}
mkdir -p {d}/frontend
CID=$(docker create {img})
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
docker cp "$CID:{IMAGE_PATH}/." {d}/frontend/
DIGEST=$(docker image inspect --format '{{{{index .RepoDigests 0}}}}' {img} 2>/dev/null || true)
if [ -z "$DIGEST" ]; then
  DIGEST=$(docker image inspect --format '{{{{.Id}}}}' {img})
fi
tar czf {d}/frontend.tgz -C {d}/frontend .
echo "DIGEST=$DIGEST"
echo "BYTES=$(wc -c < {d}/frontend.tgz)"
"""


def cleanup_script() -> str:
    return f"rm -rf {shlex.quote(_REMOTE_DIR)}\n"


def parse_probe(output: str) -> dict[str, str]:
    """Pull the `KEY=value` lines out of `extract_tree_script`'s stdout."""
    out: dict[str, str] = {}
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith("DIGEST=") or line.startswith("BYTES="):
            k, _, v = line.partition("=")
            out[k] = v.strip()
    return out


# ── tar extraction (the part that must not be trusting) ───────

def _tar_name(raw: str) -> str:
    """Strip the single leading `./` that `tar -C dir .` prepends — and nothing else.

    ⚠️ NOT `lstrip("./")`: that strips any run of `.` and `/` characters, so
    `/etc/evil` becomes `etc/evil` and `../evil` becomes `evil` — the exact two
    inputs the guard below exists to reject. (Caught by
    test_rejects_an_absolute_member / test_rejects_a_parent_traversal_member.)
    """
    name = raw or ""
    if name.startswith("./"):
        name = name[2:]
    return name


def _member_is_safe(name: str) -> bool:
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if ":" in name:
        return False
    parts = name.replace("\\", "/").split("/")
    return not any(p in ("", "..") for p in parts if p != ".")


def extract_archive(archive: Path, dest: Path) -> list[dict[str, Any]]:
    """Extract a baseline tarball into `dest`, returning the manifest.

    Rejects the whole archive on the first bad member rather than skipping it:
    a tarball carrying a symlink or a `..` path is not a vendor build, and
    half-extracting it would leave an unusable baseline behind.
    """
    dest.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    total = 0
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        if len(members) > MAX_BASELINE_FILES:
            raise BaselineError(f"В архиве больше {MAX_BASELINE_FILES} членов")
        for m in members:
            name = _tar_name(m.name)
            if m.isdir():
                if not _member_is_safe(name or "x"):
                    raise BaselineError(f"Небезопасный путь в архиве: {m.name!r}")
                continue
            if not m.isfile():
                # symlink / hardlink / device / fifo — a vendor frontend has none
                raise BaselineError(f"Недопустимый тип члена архива: {m.name!r}")
            if not _member_is_safe(name):
                raise BaselineError(f"Небезопасный путь в архиве: {m.name!r}")
            total += m.size
            if total > MAX_BASELINE_BYTES:
                raise BaselineError(f"Архив больше лимита {MAX_BASELINE_BYTES} байт")

        for m in members:
            name = _tar_name(m.name)
            if m.isdir() or not name:
                continue
            target = (dest / name).resolve()
            if dest.resolve() not in target.parents:
                raise BaselineError(f"Член архива выходит за каталог: {m.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            data = src.read()
            target.write_bytes(data)
            manifest.append({
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    return manifest


# ── local cache ───────────────────────────────────────────────

def _manifest_path(digest: str) -> Path:
    return digest_dir(digest) / "manifest.json"


def has_baseline(digest: str) -> bool:
    return _manifest_path(digest).exists()


def save_baseline(digest: str, image: str, archive: Path) -> dict[str, Any]:
    d = digest_dir(digest)
    files = extract_archive(archive, d / "files")
    meta = {
        "digest": digest,
        "image": image,
        "files_count": len(files),
        "bytes": sum(f["size"] for f in files),
        "pulled_at": int(time.time()),
        "files": files,
    }
    _manifest_path(digest).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return meta


def get_manifest(digest: str) -> Optional[dict[str, Any]]:
    try:
        return json.loads(_manifest_path(digest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_baselines() -> list[dict[str, Any]]:
    out = []
    for d in sorted(_root().iterdir()) if _root().exists() else []:
        meta = get_manifest(d.name)
        if meta:
            out.append({k: v for k, v in meta.items() if k != "files"})
    return out


def read_file(digest: str, relpath: str) -> Optional[bytes]:
    """One baseline member. Gated on the manifest, then re-checked with resolve()
    — the same two-step guard the overlay store uses."""
    meta = get_manifest(digest)
    if not meta:
        return None
    rel = (relpath or "").strip()
    if not any(f["path"] == rel for f in meta.get("files", [])):
        return None
    root = (digest_dir(digest) / "files").resolve()
    target = (root / rel).resolve()
    if root not in target.parents and root != target:
        return None
    try:
        return target.read_bytes()
    except OSError:
        return None
