# Set up PV Monitor with your AI agent

If you do not want to install PV Monitor yourself, give this file to an AI agent that can run commands on your computer
(for example Claude Code, Codex, Cursor or a similar tool) and say: **"Set up PV Monitor for me following AGENT_SETUP.md."**
The agent does the technical part; you only provide what it cannot know or create: the items in *What you provide*.

---

## Instructions for the agent

You are installing **PV Monitor** (https://github.com/pokokich/PV-system-monitoring-and-analysis-), a read-only
monitoring and analysis dashboard for Deye solar inverters. Follow these steps in order. Ask the owner only for the items
under *What you provide*; do everything else yourself.

### Check first that it fits

PV Monitor fits when the owner has a **Deye** inverter that reports to **Deye Cloud** and wants monitoring and analysis
on their own computer. It does not fit if they want to control the inverter (it is read-only by design), read it locally
over Modbus/RS485, or use another brand's cloud. Tell the owner this before installing.

### Hard rules

1. **Read-only towards the inverter.** Never call Deye control endpoints, never write inverter or battery settings, never
   install or enable any tool that can (for example the Deye Open MCP server, which exposes control commands).
2. **Secrets**: never print the Deye password, App Secret or AI key back in the chat, never write them into a file that is
   committed to git, a log, a shell history line visible to others, or a cloud folder. Enter them in the setup page, or
   write them only into `.env` (Docker) on the owner's computer.
3. **Do not change system or network settings** (firewall, router, sleep, DNS) without asking the owner first.
4. **No paid services** unless the owner asks for them. An AI provider for the Analysis page is optional.
5. If something fails twice, stop and tell the owner what failed and what you tried.

### What you provide (ask the owner for these, nothing else)

| Item | Where the owner finds it |
|---|---|
| Deye Cloud e-mail and password | the login of the Deye Cloud app |
| App ID and App Secret | developer.deyecloud.com → Application. If there is none, guide the owner through creating one (below); you cannot create it for them |
| Data centre | developer portal; Europe = `eu1`, Americas = `us1` |
| Which computer runs it | this computer, or a NAS/server that is on all the time |
| Electricity prices per kWh, VAT, night hours (if two-rate) | the electricity bill or contract |
| What the system cost (optional) | the installer's invoice |
| AI provider and key (optional) | e.g. console.anthropic.com, platform.openai.com, or a local Ollama |

Creating the Deye developer application (the owner does this in the browser, you explain): sign in at
https://developer.deyecloud.com with the same account as the Deye Cloud app → *Application* → *Create* → grant **only
monitoring permissions** (station and device data, no control) → copy App ID and App Secret.

### Step 1: choose the way to run it

- The owner wants an app on a Mac or Windows PC → **desktop app** (step 2A).
- There is an always-on Linux machine, NAS, Raspberry Pi, or the owner prefers Docker → **Docker** (step 2B).
  Check `docker compose version` works (Compose v2). If Docker is missing, ask before installing it.

### Step 2A: desktop app

1. Download the latest release asset for the OS and CPU from
   https://github.com/pokokich/PV-system-monitoring-and-analysis-/releases (`PV-Monitor-macOS-arm64.zip`,
   `PV-Monitor-macOS-x64.zip` or `PV-Monitor-Windows-x64.zip`; on macOS `uname -m` gives `arm64` or `x86_64`).
2. Unzip. macOS: move `PV Monitor.app` to `/Applications`, then remove the download quarantine with
   `xattr -dr com.apple.quarantine "/Applications/PV Monitor.app"` (the app is not signed) and open it.
   Windows: extract to `%LOCALAPPDATA%\Programs\PV Monitor` and run `PV Monitor.exe`.
3. If no release exists yet or the binary does not start, run from source instead: install Python 3.11+, clone the
   repository, `pip install -r requirements-desktop.txt`, then `python -m desktop.app`.
4. Confirm `http://127.0.0.1:<port>/healthz` answers (8088, or the next free port up to 8099 when 8088 is taken; the port is the first word of `instance` in the data folder). In the tray menu enable **Start at login** (or tell the owner to).

### Step 2B: Docker

```bash
git clone https://github.com/pokokich/PV-system-monitoring-and-analysis-.git ~/pvmon
cd ~/pvmon
cp .env.example .env
```

- If port 8088 is taken (`lsof -i :8088` / `ss -ltn`), set `WEB_PORT` in `.env` to a free port. Never stop another
  service to free a port.
- Optionally put the Deye access into `.env` (`DEYE_EMAIL`, `DEYE_PASSWORD`, `DEYE_APP_ID`, `DEYE_APP_SECRET`,
  `DEYE_BASE_URL=https://<dc>-developer.deyecloud.com/v1.0`) by editing the file locally; otherwise use the setup page.
- Set `TZ` to the owner's IANA time zone (e.g. `Europe/Berlin`).
- Suggest `BACKUP_DIR` pointing to a folder that is backed up off this machine (NAS, synced folder). Ask before
  pointing it to a cloud-synced folder, because the database contains the owner's consumption data.
- `docker compose up -d --build`, then check `docker compose ps` (collector, web, guard running) and
  `curl -fsS http://localhost:${WEB_PORT:-8088}/healthz`.

### Step 3: first-run setup

Open `http://<host>:<port>/setup` (the owner can do the typing of secrets there, which keeps them out of the chat):

1. **Deye Cloud access** → *Check and save*. Success lists the station(s). Then within a minute
   `docker compose logs collector` (or `pvmon.log`) shows the backfill starting.
2. **General**: name, currency, time zone, language, night tariff hours. Location comes from Deye if it is set there.
3. **Settings** page: add the PV arrays per MPPT input (panels, Wp, tilt, azimuth), the battery (modules, kWh per
   module, minimum SOC), the electricity prices (with their start date and whether VAT is included), the feed-in price if
   any, and the investment (CAPEX). Use dated records; when the system was expanded, add one record per period.
4. **AI analysis** (optional): provider, model, address, key. For a CLI tool, set `LLM_PROVIDER=cli` and
   `LLM_CLI_COMMAND` in `.env` (the web page cannot set commands) and restart.
   For `claude -p`, make sure Claude Code is signed in (`claude`, then `/login`). If the Analysis page still reports
   *Not logged in*, have the owner run `claude setup-token` and add `CLAUDE_CODE_OAUTH_TOKEN=<token>` to the same
   `.env` themselves (it is a secret: do not ask for it in the chat), then restart.
5. Press *Done*.

### Step 4: verify and hand over

- Wait until the status line at the bottom left of the dashboard shows fresh data (first backfill can take 10–30 minutes
  because Deye limits request speed).
- Open *Data quality*: coverage should be close to 100% for recent days.
- If the dashboard should be reachable from outside, follow `docs/public-access.md` and **set `WRITE_PASSWORD` first**.
- Tell the owner: the address of the dashboard, where the data and backups are, how to update
  (`git pull && docker compose up -d --build`, or download the new release), and that the computer should be on at least
  once a month (Deye keeps 5-minute data ~90 days).

### Troubleshooting for the agent

- `Deye did not accept the data`: wrong password (it is the Deye Cloud app password), wrong App ID/Secret, or wrong
  data centre. Ask the owner to re-check; do not guess.
- HTTP 500 from Deye in bursts is normal; the collector backs off.
- Wrong hours on charts: set the time zone in the setup page (or `TZ`).
- Run the tests if you changed anything: `pip install -r requirements-dev.txt && python -m unittest`.
