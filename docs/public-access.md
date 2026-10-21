# Access from outside your home network

By default the dashboard is reachable only at home (Docker: `http://<computer-ip>:8088`; desktop app:
`http://127.0.0.1:8088` on that computer only). Two free ways give it a stable HTTPS address from anywhere.
Both open an **outgoing** connection, so no port is opened on your router and your home IP stays hidden.
`COMPOSE_PROFILES` in `.env` picks which one Docker Compose starts.

| | Tailscale Funnel (`funnel`) | Cloudflare Tunnel (`tunnel`) |
|---|---|---|
| Address | `https://<TS_HOSTNAME>.<tailnet>.ts.net` | `https://pv.<your-domain>` |
| Needs a domain | no | yes, with DNS on Cloudflare |
| Login before the pages | none: anyone with the link can read | Cloudflare Access (e-mail code) |
| Changes (settings, goals, invoices, keys) | only with `WRITE_PASSWORD` | only with `WRITE_PASSWORD` |

**Set `WRITE_PASSWORD` before you publish the dashboard.** Without it anyone with the link could change your
settings or replace the AI key. Reading stays open with Funnel, so share the link only with people you trust.

## Tailscale Funnel

Once, in the browser:

1. Free account at **login.tailscale.com** (Personal plan).
2. Admin console → **DNS**: turn on **MagicDNS** and **HTTPS Certificates**.
3. **Access controls**: `nodeAttrs` must contain `{"target": ["autogroup:member"], "attr": ["funnel"]}` (default in new accounts).
4. Settings → **Keys** → **Generate auth key** (not reusable, not ephemeral). Copy `tskey-auth-…`; it is needed only at the first start.
5. After the first start: Machines → your device → **Disable key expiry**, otherwise it leaves the network after 180 days.

In `.env`:

```
COMPOSE_PROFILES=funnel
TS_AUTHKEY=tskey-auth-...
TS_HOSTNAME=pvmon
TUNNEL_PUBLIC_URL=https://pvmon.<tailnet>.ts.net
TUNNEL_READY_URL=http://funnel:9002/healthz
WRITE_PASSWORD=<a long password>
```

Then `docker compose up -d --build`. The Tailscale login is kept in `tailscale/state/` (not in git).
The forwarding is in [tailscale/serve.json](../tailscale/serve.json): HTTPS 443 → `http://web:8088`.

## Cloudflare Tunnel (with your own domain on Cloudflare)

1. dash.cloudflare.com → **Networking → Tunnels → Create tunnel** → Cloudflared. Copy the string after `--token`: that is `TUNNEL_TOKEN`.
2. The tunnel → **Routes → Add route → Published application**: subdomain `pv`, your domain, service `http://web:8088`.
3. Optional but recommended: **Cloudflare Access** (Zero Trust → Access → Applications → Self-hosted) for `pv.<domain>`, policy Allow → your e-mail, login One-time PIN.
4. In `.env`: `COMPOSE_PROFILES=tunnel`, `TUNNEL_TOKEN=…`, `TUNNEL_PUBLIC_URL=https://pv.<domain>`, `TUNNEL_READY_URL` empty.

## Checks

- The `funnel` / `tunnel` container has a healthcheck (`docker compose ps` shows `healthy`).
- `guard` checks every 5 minutes that the connection is up and that the public address answers; a problem shows in the
  status line at the bottom left of every page and is sent to your alert channels.
- Updating stays `git pull && docker compose up -d --build`; the profile in `.env` brings the access up again.

## Stopping

- Temporarily: `docker compose stop funnel` (until the next `up`); for good: empty `COMPOSE_PROFILES` and `docker compose rm -sf funnel`.
- Switching: change `COMPOSE_PROFILES`, `TUNNEL_PUBLIC_URL`, `TUNNEL_READY_URL`, then `docker compose up -d` and remove the old container.
