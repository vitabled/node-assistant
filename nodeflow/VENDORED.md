# Vendored: NodeFlow Panel + Node Agent (install-kit source)

This directory is the **upstream NodeFlow source** (from `NodeFlow-Panel-1.0.4-Agent-1.0.4-install-kit`),
vendored so node-installer can build and auto-deploy a **local** NodeFlow HAProxy panel on demand
(see `backend/app/services/nodeflow_server.py` and CLAUDE.md §12).

- **Everything except `Dockerfile.migrate` is upstream, unmodified.** `Dockerfile.migrate` is added by
  node-installer to bake the migrations into a one-shot image for DooD orchestration.
- **License:** the upstream install-kit ships **no LICENSE file** (all-rights-reserved). It is vendored
  here for the repo owner's own deployment, mirroring how `frontend/public/mihomo/` was vendored
  (documented caveat). Consider this before redistributing publicly.
- **How it's used:** the panel image is built via the main compose `nodeflow-build` profile
  (`docker compose --profile nodeflow-build build nodeflow-panel nodeflow-migrate`); the backend
  generates PKI/admin-token/signing-key (Python `cryptography`) and orchestrates
  postgres → migrate → panel over the host Docker socket, then proxies `/api/v1/*` for the «HAPROXY» UI.
- Upstream `compose.yaml` / `scripts/install-panel.sh` describe the *manual* install on a dedicated host;
  node-installer's orchestrator reproduces the equivalent setup locally (no root/openssl needed).
