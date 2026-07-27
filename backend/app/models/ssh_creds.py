"""SSH credentials mixin — a password OR a Хранилище key reference.

Every request body that opens an SSH session carries its credentials per request
(project rule: nothing at rest unless the operator explicitly put it in the
Хранилище). Since wave 9 there are two shapes, and exactly one of them must be
present:

  - `ssh_password` — as before, the plaintext password;
  - `ssh_key_ref`  — the id of a Хранилище entry (kind=ssh_key). Only the REF
    travels to/from the browser: the private key is resolved server-side by
    `services/ssh_auth.py`, so it never lands in `localStorage`.

⚠️ `ssh_password` used to be `Field(..., min_length=1)` — a body without it was a
422 at the field level. Making it optional must NOT turn "forgot the password"
into a 500 at connect time, hence the model validator below keeps it a 422 with
a readable message.
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class SshCreds(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = ""
    ssh_key_ref: str = ""

    @model_validator(mode="after")
    def _auth_present(self) -> "SshCreds":
        if not self.ssh_password and not self.ssh_key_ref.strip():
            raise ValueError("Укажите SSH-пароль или выберите ключ из Хранилища")
        return self
