# Deploying node-assistant on a server

The whole stack lives behind one nginx container (`proxy`) that terminates TLS and
forwards to the SPA and the API. `install.sh` sets it all up in one run.

## Quick start (fresh server)

```bash
git clone https://github.com/vitabled/node-assistant.git /opt/node-assistant
cd /opt/node-assistant
sudo ./install.sh --domain panel.example.com --email you@example.com
```

Point an **A record** at the server first — the certificate is issued over
HTTP‑01, which only works if `panel.example.com` already resolves here and port 80
is reachable from the internet.

The script installs Docker if it is missing, generates the secrets in `.env`,
builds the images, starts the stack, obtains the certificate and leaves renewal
running. When it finishes, open `https://panel.example.com` and create the first
account.

### Without a domain

```bash
sudo ./install.sh --no-tls
```

Serves the panel over plain HTTP on port 80 — fine for a LAN or a quick trial,
**not** for the internet (session tokens would travel in clear text). Add TLS
later by re-running with `--domain`; nothing else has to be redone.

### While testing DNS or firewall rules

```bash
sudo ./install.sh --domain panel.example.com --email you@example.com --staging
```

`--staging` uses Let's Encrypt's staging CA: browsers will not trust the
certificate, but there is **no rate limit**, so a misconfigured DNS record costs
nothing. Re-run without `--staging` once the run succeeds — add `--force-cert` to
replace the staging certificate with a real one.

### Options

| Flag | Meaning |
|---|---|
| `--domain <fqdn>` | Domain to serve on; enables HTTPS |
| `--email <mail>` | Contact address for expiry notices |
| `--no-tls` | Plain HTTP on port 80, no certificate |
| `--staging` | Staging CA — untrusted certs, no rate limit |
| `--force-cert` | Re-issue even if a valid certificate exists |
| `--dir <path>` | Install directory (default `/opt/node-assistant`) |
| `--branch` / `--repo` | Which sources to deploy |
| `-y`, `--yes` | Non-interactive |

Re-running the script is safe: it keeps the existing `ENCRYPTION_KEY`,
`AGG_TOKEN` and certificates, pulls new sources (when the tree is clean),
rebuilds and restarts.

## How the TLS layer works

```
:80  ┌───────────────────────────────┐
:443 │  proxy (nginx)                │
     │   /.well-known/acme-challenge │──► acme-webroot volume ◄── certbot
     │   /api/  ─────────────────────│──► backend:8000
     │   /ws/   ─────────────────────│──► backend:8000  (upgrade, 1h timeout)
     │   /      ─────────────────────│──► frontend:80   (SPA)
     │   /internal/ → 404            │
     └───────────────────────────────┘
```

* **No certificate yet?** The proxy renders an HTTP-only config. This is what
  makes the first issuance possible at all: an nginx with a `listen 443 ssl`
  block pointing at a missing certificate file refuses to start, so there would
  be no server on port 80 to answer the ACME challenge.
* **Certificate present?** It renders the TLS config: port 80 keeps serving the
  ACME path (renewals need it) and redirects everything else to HTTPS.
* A watcher inside the container re-checks every 6 hours and reloads nginx, so a
  renewed — or a first-ever — certificate goes live on its own.
* The `certbot` container runs `certbot renew` every 12 hours. Certbot itself
  no-ops until the certificate is within 30 days of expiry.

Certificates live in the `node-letsencrypt` volume. Keep it: losing it means
re-issuing, which counts against Let's Encrypt's rate limit.

## Operating

```bash
cd /opt/node-assistant
docker compose ps                 # what is running
docker compose logs -f proxy      # entry point / TLS
docker compose logs -f backend    # API
docker compose restart proxy      # reload after editing proxy/ files
docker compose down               # stop (volumes and certs survive)
```

Certificate state:

```bash
docker compose run --rm --entrypoint certbot certbot certificates
```

Force a renewal now:

```bash
docker compose run --rm --entrypoint certbot certbot renew --force-renewal \
  --webroot -w /var/www/certbot
docker compose restart proxy
```

## Changing the domain

```bash
sudo ./install.sh --domain new.example.com --email you@example.com
```

It rewrites `PROXY_DOMAIN`/`CORS_ORIGIN`, issues a certificate for the new name
and restarts the proxy. The old certificate stays in the volume, unused.

## Troubleshooting

**Certificate issuance failed.** The panel still works over HTTP, so nothing is
lost. In order of likelihood: the A record does not point at this server; port 80
is blocked by a *provider* firewall (security group), not just `ufw`; or a rate
limit was hit — use `--staging` while debugging. Fix and re-run.

**Port 80/443 already in use.** Usually a distro nginx or apache2 from the
provider image: `systemctl disable --now nginx apache2`, then re-run.

**`docker compose` not found.** The script installs Docker from get.docker.com,
which ships Compose v2. On a distro where that fails, install the
`docker-compose-plugin` package and re-run.

**Panel reachable but the browser warns about the certificate.** That is a
`--staging` certificate. Re-run without `--staging`, adding `--force-cert`.

## Notes

* `.env` holds the signing key for every session token and the credential vault
  key. It is written `chmod 600` — back it up, and never commit it.
* Everything after the first account is per-account; the **first** account
  created inherits any pre-existing root-level data, so create it before sharing
  the URL.
* `PROXY_DOMAIN` is read from `.env` by compose. You can edit it by hand and
  `docker compose up -d proxy`, but the certificate then has to be requested
  manually (see above) — `install.sh` does both.
