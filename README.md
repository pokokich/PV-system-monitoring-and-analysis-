# PV Monitor: monitoring and analysis for Deye solar systems

[Български](README.bg.md) · [Setup with your AI agent](AGENT_SETUP.md) · License: [AGPL-3.0](LICENSE)

PV Monitor collects the data of your Deye hybrid inverter from the Deye Cloud Open API, keeps it on **your own computer**
for as long as you like, and turns it into a dashboard: production, battery, consumption and grid, money and payback,
goals, component health, data quality and (optionally) an analysis written by your own AI model.

**It only reads.** The Deye client in this project can call only monitoring endpoints; every control endpoint
(`/order/*`, `/strategy/*`, `/config/update`, …) is blocked in code before any request is made (see
[collector/deye_client.py](collector/deye_client.py) and its test). Nothing here can change a setting of your inverter
or battery.

> Not affiliated with, endorsed by or supported by Deye. "Deye" is a trademark of its owner. Use at your own risk.

## What you get

- **Overview**: energy flow now, today's numbers, goal status, health.
- **Production, Battery, Consumption and grid**: any period, per MPPT input, SOC heat map, day/night tariff split.
- **Economics**: cost with and without PV, benefit, payback in three scenarios (with replacements, price growth, degradation).
- **Goals**: your own targets (self-sufficiency, night coverage, payback, PVGIS ratio…) with forecast to the end of the period;
  benchmarks from PVGIS and Open-Meteo for your location.
- **Health**: battery temperatures and SOC bands, stress index, wear model, cycles; inverter temperature and grid quality.
- **Analysis**: findings and recommendations from your own LLM (Claude, any OpenAI-compatible API, a local Ollama, or a CLI
  such as `claude -p`). **Without a model this page stays empty**; nothing else needs AI.
- **Settings**: panels per MPPT input, battery, tariffs (with history), investment and costs, invoices; all dated, so an
  expansion of the system is handled correctly.
- English and Bulgarian interface, any currency (amounts are never converted), any time zone, single or day/night tariff.

## Before you start (prerequisites)

1. **A Deye inverter connected to Deye Cloud** (you can see it in the Deye Cloud app or on deyecloud.com).
2. **A Deye Cloud developer application** (free), which gives you an *App ID* and *App Secret*:
   1. Sign in at <https://developer.deyecloud.com> with the **same account** as your Deye Cloud app.
   2. *Application* → *Create application*.
   3. Grant **only the monitoring permissions** (station / device data). Do **not** grant control permissions: PV Monitor
      never needs them.
   4. Copy the **App ID** and **App Secret**. Note your **data centre** (Europe: `eu1`, Americas: `us1`; the developer portal
      shows the API address for your account).
   5. Check the Deye Open API terms in the portal yourself; you are the one who agrees to them.
3. **A computer that is on most of the time**, where the data will live:
   - **Desktop app**: macOS 12+ (Apple silicon or Intel) or Windows 10/11. The dashboard opens in your browser.
   - **Docker**: any Linux / macOS / Windows machine, NAS or Raspberry Pi 4/5 with Docker and Docker Compose v2.
   - About 1 GB of disk per year of data (usually much less), 200 MB of RAM.
4. **Optional, for the Analysis page**: an API key of an LLM provider (e.g. Anthropic Claude, OpenAI, OpenRouter, Mistral),
   or a local model (Ollama, LM Studio), or a command-line AI tool installed on the same computer.
5. **Optional**: your electricity tariff (prices per kWh, and the night hours if you have a two-rate tariff), and what the
   system cost (for payback).

### Why the computer should run most of the time

Deye keeps the detailed 5-minute history for **about 90 days** (daily totals for longer). PV Monitor catches up
automatically after it was off: when it starts again it downloads everything Deye still has since the last data it
stored. If it is off for more than ~90 days, the 5-minute detail of the oldest part is lost (daily totals remain).
Starting it at least once a month is enough to keep the full history; running it all the time is best.

## Install, option A: desktop app (Mac / Windows)

1. Download the latest `PV-Monitor-macOS-arm64.zip` (Apple silicon), `PV-Monitor-macOS-x64.zip` (Intel Mac) or
   `PV-Monitor-Windows-x64.zip` from [Releases](https://github.com/pokokich/PV-system-monitoring-and-analysis-/releases).
2. Unzip it.
   - **macOS**: move *PV Monitor.app* to *Applications*. The app is not signed by Apple, so the first time **right-click →
     Open → Open** (or System Settings → Privacy & Security → *Open Anyway*).
   - **Windows**: open the folder and run *PV Monitor.exe*. SmartScreen may warn about an unknown publisher: *More info →
     Run anyway*.
3. A sun icon appears in the menu bar / system tray and the browser opens `http://127.0.0.1:8088/setup` (or 8089, 8090… when another program already uses 8088; set `PVMON_PORT` to fix the port).
4. Follow the [first-run setup](#first-run-setup). In the tray menu, tick **Start at login**, so the data keeps coming.

Data lives in `~/Library/Application Support/PV Monitor` (macOS) or `%APPDATA%\PV Monitor` (Windows): the database
`data/pv.sqlite`, the access file `data/secrets.json`, daily backups in `backups/`, and the log `pvmon.log`.
The dashboard is reachable only from this computer. To open it from your phone at home, start the app with
`PVMON_HOST=0.0.0.0` (and set `WRITE_PASSWORD`, see [Security](#security)).

Run from source instead (any OS with Python 3.11+): `pip install -r requirements-desktop.txt && python -m desktop.app`.

## Install, option B: Docker (always-on computer, NAS, Raspberry Pi)

```bash
git clone https://github.com/pokokich/PV-system-monitoring-and-analysis-.git pvmon
cd pvmon
cp .env.example .env          # optional: everything can also be entered in the setup page
docker compose up -d --build
```

Open `http://<this-computer>:8088/setup` and follow the [first-run setup](#first-run-setup).
Instead of building, you can use the published image: set `PV_IMAGE=ghcr.io/pokokich/pvmon:latest` in `.env` and run
`docker compose pull && docker compose up -d --no-build`.

Services: `collector` (talks to Deye), `web` (the dashboard, port `WEB_PORT`, default 8088), `guard` (daily backup and
checks) and, optionally, `funnel` or `tunnel` for access from outside ([docs/public-access.md](docs/public-access.md)).
Data is in `./data`, backups in `BACKUP_DIR` (default `./backups`; point it to a synced folder or a NAS share).

Update: `git pull && docker compose up -d --build`. Logs: `docker compose logs -f collector`.

## First-run setup

The setup page (`/setup`, also linked from *Settings*) has four steps:

1. **Deye Cloud access**: e-mail and password of your Deye Cloud account, App ID, App Secret and data centre. The page
   signs in once and lists your stations to prove it works, then saves the access in `data/secrets.json` (readable by
   your user only). The collector starts within a minute and downloads the history Deye still has.
   With Docker you can put the same values in `.env` instead (`DEYE_EMAIL`, `DEYE_PASSWORD`, `DEYE_APP_ID`,
   `DEYE_APP_SECRET`, `DEYE_BASE_URL`); `.env` always wins.
2. **General**: name, currency (ISO code such as EUR, GBP, PLN, USD), time zone, language, location (filled from Deye
   when it is there; used for PVGIS and weather), and the **night tariff hours** (single rate, 22–06, 23–07, a
   seasonal preset, or your own periods).
3. **System and money** (in *Settings*): the panels on each MPPT input (count, Wp, tilt, azimuth), the battery (modules,
   kWh per module, minimum SOC), electricity prices (day/night, with VAT or not), feed-in price if you sell, and the
   investment. Every record has a *valid from* date, so add a new record when you expand the system instead of editing the
   old one.
4. **AI analysis (optional)**: see below.

Then set your **Goals** (page *Goals*): defaults are provided and all are editable.

## AI analysis

The *Analysis* page sends a **summary of the numbers** to the model you choose and shows its findings, each with the
numbers behind it, plus a list of levers ordered by yearly benefit. You start each analysis yourself (button *New
analysis*), optionally with a question; the last answer stays on the page.

| Provider (setup page → AI analysis) | What to enter | Notes |
|---|---|---|
| Claude API (Anthropic) | API key from console.anthropic.com; model, e.g. `claude-opus-5-5` | paid per use, a run costs cents |
| OpenAI-compatible | address (`https://api.openai.com/v1`, `https://openrouter.ai/api/v1`, …), key, model | OpenAI, OpenRouter, Mistral, Groq… |
| Local model | address `http://localhost:11434/v1` (Ollama) or LM Studio's, model e.g. `llama3.1`; no key | free, private; needs a capable computer |
| Command on this computer | `LLM_PROVIDER=cli` and `LLM_CLI_COMMAND="claude -p"` (or `ollama run llama3.1`) in `.env` (desktop app: the `.env` in its data folder) | the command gets the prompt on stdin; it can only be set in `.env`, never from the web page |

What is sent: your system description from Settings, KPIs for the last 30 days, last 365 days and since commissioning,
monthly totals (up to 24 months), the typical year, goals, health checks and payback scenarios. What is **never** sent:
Deye credentials, API keys, invoice numbers or personal data, the exact location (rounded to about 10 km).
The API key is stored in `data/secrets.json`, is never shown again in the browser, and is deleted automatically when you
change the provider or its address. Models can be wrong: check each finding against its numbers.

## Security

- The dashboard has **no user accounts**. At home that is usually fine. **Before making it reachable from outside, set
  `WRITE_PASSWORD`** (in `.env`, or as an environment variable for the desktop app): then every change, including the
  Deye access and the AI key, needs that password once per browser.
- Changes are accepted only from the dashboard's own pages (origin check), as JSON or PDF.
- Secrets live only in `.env` or `data/secrets.json`, never in the database or its backups. Do not commit `.env`.
- Access from outside: [docs/public-access.md](docs/public-access.md) (Tailscale Funnel without a domain, or Cloudflare
  Tunnel with one; no open router port).

## Privacy

No telemetry, no accounts, no data sent to the authors. PV Monitor talks only to: Deye Cloud (your data), PVGIS (EU JRC,
expected production for your location), Open-Meteo (weather and irradiance) and, if you configure one, your AI provider.

## Invoices

Electricity invoices can be entered by hand (period, kWh day/night, amount) in *Settings*; they let the dashboard use the
utility meter where Deye's grid measurement is incomplete. PDF upload currently recognises Electrohold (Bulgaria) invoices
and needs `pdftotext` (included in the Docker image); parsers for other utilities are welcome as contributions.

## Troubleshooting

- *The status line says "no Deye access yet"*: finish step 1 of the setup.
- *"Deye did not accept the data"*: check e-mail/password (as in the Deye app), App ID/Secret, and the data centre.
  HTTP 500 from Deye in the log is normal in bursts; the collector waits and retries.
- *Nothing new for hours*: the computer slept or was off; data is filled in on the next start. Check *Data quality*.
- *Wrong hours on the charts*: set the time zone in the setup.
- Logs: `docker compose logs -f collector`, or `pvmon.log` in the desktop app's folder.

## Development

```bash
pip install -r requirements-dev.txt
python -m unittest -v                  # tests, including the read-only guarantees
uvicorn web.app:app --reload --port 8088
python -m collector.main run           # collector
python -m collector.main check         # computed energy vs Deye's daily totals
```

Architecture: Python collector (standard library only) → SQLite (migrations in [migrations/](migrations/), applied
automatically) → FastAPI + Jinja2 + ECharts dashboard. The desktop app ([desktop/app.py](desktop/app.py)) runs the same
three parts in one process; releases are built by [GitHub Actions](.github/workflows/release.yml) with PyInstaller.
Contributions are welcome under the same license.

## License

[GNU AGPL-3.0](LICENSE). You may use, modify and share it; if you offer a modified version to others over a network, you
must offer them its source as well.
