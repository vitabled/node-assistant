"""Turning a request body's SSH credentials into `SSHSession` kwargs (wave 9 Ф2).

Every call-site becomes a one-liner:

    ssh = SSHSession(req.ip, port, req.ssh_user, **await ssh_auth.resolve(req))

Two design points:

- **Duck-typing, not a base class.** ~20 request models carry `ssh_password` /
  `ssh_key_ref`, most declared inline in their own router; requiring them all to
  inherit `models.ssh_creds.SshCreds` would be a mechanical change with no gain
  here, so the attributes are read with `getattr` defaults.
- **The private key is read fresh from the Хранилище on every resolve** — it is
  never returned to the browser and never cached, so revoking a vault entry takes
  effect on the next operation.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

# One fixed message for every failure mode of a key lookup: which of them it was
# (absent / wrong kind / undecryptable / no private_key field) is not actionable
# for the operator and could hint at other accounts' entry ids.
_KEY_UNAVAILABLE = "Ключ из Хранилища недоступен — проверьте запись"


async def resolve(req: Any, account_id: Optional[str] = None) -> dict:
    """`{"password": ...}` or `{"private_key": ..., "key_passphrase": ...}`.

    `account_id` is for background callers without a request context; when it is
    None the vault falls back to the `current_account` ContextVar (the same
    pattern as storage.py) — which the deploy queue re-publishes before running a
    job, so the pipeline works in the worker process too.
    """
    ref = (getattr(req, "ssh_key_ref", "") or "").strip()
    if not ref:
        return {"password": getattr(req, "ssh_password", "") or ""}

    # Imported inside the function: the SSH stack is imported by scripts/tests
    # that stub out heavy deps, and it has no other reason to pull in the vault.
    from app.services import vault_store

    entry = vault_store.get_entry(ref, account_id)
    if not entry or entry.get("kind") != "ssh_key":
        raise HTTPException(400, _KEY_UNAVAILABLE)
    fields = await vault_store.a_read_fields(ref, account_id) or {}
    private_key = fields.get("private_key") or ""
    if not private_key:
        raise HTTPException(400, _KEY_UNAVAILABLE)
    passphrase = fields.get("passphrase") or ""

    # Parsed (and the result discarded — SSHSession takes the PEM text) so an
    # unusable key is a readable 400 HERE. Otherwise SSHSession.__init__ raises
    # ValueError at call-sites that only guard connect() — e.g. /api/node/detect —
    # which FastAPI turns into a bare 500. Its message is already fixed Russian
    # text with no key material in it.
    from app.services.ssh_manager import _import_key

    try:
        _import_key(private_key, passphrase)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"private_key": private_key, "key_passphrase": passphrase}
