# Deploying node-assistant on a server

The whole stack sits behind one nginx container (`proxy`) that terminates TLS and
forwards to the SPA and the API. `install.sh` sets it up and afterwards doubles as
the management CLI, reachable as `node-assistant`.

## Install with one command

```bash
curl -fsSL https://raw.githubusercontent.com/vitabled/node-assistant/main/install.sh \
  | sudo bash -s -- --domain panel.example.com --email you@example.com
```

Point an **A record** at the server first — the certificate is issued over
HTTP‑01, which only works if `panel.example.com` already resolves here and port 80
is reachable from the internet.

That single command installs Docker if missing, clones the repo to
`/opt/node-assistant`, generates the secrets in `.env`, builds and starts the
stack, obtains the certificate, installs the `node-assistant` shortcut and leaves
renewal running. When it finishes, open `https://panel.example.com` and create the
first account.

Prefer to look before you run? Clone first — it behaves identically:

```bash
git clone https://github.com/vitabled/node-assistant.git /opt/node-assistant
cd /opt/node-assistant
sudo ./install.sh --domain panel.example.com --email you@example.com
```

### Without a domain

```bash
curl -fsSL https://raw.githubusercontent.com/vitabled/node-assistant/main/install.sh | sudo bash -s -- --no-tls
```

Serves the panel over plain HTTP — fine for a LAN or a quick trial, **not** for
the internet (session tokens would travel in clear text). Add TLS later with
`sudo node-assistant set-domain panel.example.com`.

### While testing DNS or firewall rules

Add `--staging`: it uses Let's Encrypt's staging CA, so browsers will not trust
the certificate but there is **no rate limit** — a wrong DNS record costs
nothing. Re-run without `--staging` afterwards; the installer notices the staging
certificate and replaces it automatically.

## Managing an installed instance

After the first run the script is on `PATH` as `node-assistant` (a symlink into
the checkout, so it always runs the version you have installed):

```bash
sudo node-assistant status            # stack state, cert expiry, installed version
sudo node-assistant check-updates     # is a newer version available?
sudo node-assistant update            # pull, rebuild, restart
sudo node-assistant set-domain new.example.com
sudo node-assistant set-ports --http-port 8080 --https-port 8443
```

`check-updates` exits **10** when an update is available and 0 when up to date, so
it can drive a cron job or a monitoring check:

```bash
0 6 * * * /usr/local/bin/node-assistant check-updates >/dev/null || \
          /usr/local/bin/node-assistant update -y
```

`update` refuses to run if the checkout has local modifications — it will never
overwrite your own edits. It fast-forwards the tracked branch, rebuilds the
images, restarts the stack and reloads the proxy.

> The panel also has an in-app updater (Settings → «Обновления»), which does the
> same thing from a container. Either is fine; they operate on the same checkout.

### Options

| Flag | Meaning |
|---|---|
| `--domain <fqdn>` | Domain to serve on; enables HTTPS |
| `--email <mail>` | Contact address for expiry notices |
| `--no-tls` | Plain HTTP, no certificate |
| `--http-port <n>` / `--https-port <n>` | Web entry ports (default 80/443) |
| `--default-ports` | Keep 80/443 without being asked |
| `--staging` | Staging CA — untrusted certs, no rate limit |
| `--force-cert` | Re-issue even if a valid certificate exists |
| `--dir <path>` | Install directory (default `/opt/node-assistant`) |
| `--branch` / `--repo` | Which sources to deploy |
| `-y`, `--yes` | Non-interactive (also skips the ports prompt) |

Re-running `install` is safe: it keeps the existing `ENCRYPTION_KEY`, `AGG_TOKEN`
and certificates, remembers the domain and ports from `.env`, and repairs
whatever is missing.

## Ports

The installer asks which ports the panel should listen on and defaults to
**80/443** — just press Enter. Change them later with `set-ports`. These are the
ports published on the host; everything else in the stack talks over the internal
docker network and cannot collide with anything.

> ⚠️ **Let's Encrypt validates http-01 over public port 80.** If you move HTTP off
> 80, certificates can no longer be issued or renewed automatically. Use a
> non-standard HTTP port only when something upstream forwards :80 to it, or run
> `--no-tls` and terminate TLS elsewhere. The installer warns and skips issuance
> rather than failing.

A non-standard HTTPS port is handled properly — the HTTP→HTTPS redirect names the
port explicitly, so `http://panel:8080/` lands on `https://panel:8443/`.

## How the TLS layer works

```
:80  ┌───────────────────────────────┐
:443 │  proxy (nginx)                │
     │   /.well-known/acme-challenge │──► acme-webroot volume ◄── certbot
     │   /healthz                    │──► answered locally (healthcheck)
     │   /api/  ─────────────────────│──► backend:8000
     │   /ws/   ─────────────────────│──► backend:8000  (upgrade, 1h timeout)
     │   /      ─────────────────────│──► frontend:80   (SPA)
     │   /internal/ → 404            │
     └───────────────────────────────┘
```

* **No certificate yet?** The proxy renders an HTTP-only config. This is what
  makes the first issuance possible: an nginx with a `listen 443 ssl` block
  pointing at a missing certificate file refuses to start, so there would be no
  server on port 80 to answer the ACME challenge.
* **Certificate present?** It renders the TLS config: port 80 keeps serving the
  ACME path and `/healthz`, and redirects everything else to HTTPS.
* A watcher in the container re-checks every 6 hours and reloads nginx, so a
  renewed — or first-ever — certificate goes live on its own.
* The `certbot` container runs `certbot renew` every 12 hours; certbot itself
  no-ops until the certificate is within 30 days of expiry.

Certificates live in the `node-letsencrypt` volume. Keep it — losing it means
re-issuing, which counts against Let's Encrypt's rate limit.

## Operating

```bash
cd /opt/node-assistant
docker compose ps                 # what is running
docker compose logs -f proxy      # entry point / TLS
docker compose logs -f backend    # API
docker compose down               # stop (volumes and certs survive)
```

Certificate state, and forcing a renewal:

```bash
docker compose run --rm --entrypoint certbot certbot certificates
docker compose run --rm --entrypoint certbot certbot renew --force-renewal \
  --webroot -w /var/www/certbot
docker compose restart proxy
```

## Troubleshooting

**Certificate issuance failed.** The panel still works over HTTP, so nothing is
lost. In order of likelihood: the A record does not point at this server; port 80
is blocked by a *provider* firewall (security group), not just `ufw`; or a rate
limit was hit — use `--staging` while debugging. Fix, then re-run
`sudo node-assistant set-domain <fqdn>`.

**Port 80/443 already in use.** Usually a distro nginx or apache2 from the
provider image: `systemctl disable --now nginx apache2`, then re-run. Or move the
panel with `set-ports` (mind the http-01 caveat above).

**`docker compose` not found.** The script installs Docker from get.docker.com,
which ships Compose v2. Where that fails, install the `docker-compose-plugin`
package and re-run.

**Browser warns about the certificate.** That is a `--staging` certificate. Run
`sudo node-assistant set-domain <fqdn>` — it detects and replaces it.

**`node-assistant: command not found`.** The shortcut is created on a successful
install at `/usr/local/bin/node-assistant`. Use `sudo /opt/node-assistant/install.sh`
directly, or re-run the installer to recreate it.

## Notes

* `.env` holds the signing key for every session token and the credential vault
  key. It is written `chmod 600` — back it up, and never commit it.
* The **first** account created inherits any pre-existing root-level data, so
  create it before sharing the URL.
* `.env` is the single source of truth for the domain and ports; both compose and
  the CLI read it. Editing it by hand works, but then apply with
  `docker compose up -d` (and request the certificate yourself) — the CLI
  subcommands do both for you.
