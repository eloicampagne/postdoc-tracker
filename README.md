# Postdoc Tracker

A local web app to track postdoc and PhD job offers. Fetch jobs from academic and industry sources, scrape individual pages by URL, or add positions manually. Annotate with notes, rate by affinity, and mark applications.

Data is stored locally in `data/jobs.json` — nothing leaves your machine.

## Features

- **Fetch jobs** from INRIA, CNRS, LinkedIn, Welcome to the Jungle, or any RSS feed URL
- **Scrape any job page** by URL (extracts title, institution, deadline, description)
- **Add jobs manually**
- **Auto-tagging** by domain — keywords are fully configurable in `config.yaml`
- **Filter** by domain, position type (postdoc / PhD / other), and location
- **Sort** by deadline, affinity, or date added
- **Star rating** (1–5) and **notes** per job, saved automatically
- **Bulk delete** selected jobs
- **Theming** — colors, font, and accent fully configurable in `config.yaml`

## Requirements

Python 3.8+ and:

```bash
pip install flask requests beautifulsoup4
```

Or with the provided file:

```bash
pip install -r requirements.txt
```

## Start

```bash
./start.sh
```

Opens `http://localhost:3742` and starts the server. Stop with `Ctrl+C`.

Or start manually:

```bash
python3 server.py
```

## HTTPS (optional — needed for Safari on macOS)

Safari blocks some features on plain HTTP. To enable HTTPS, generate a self-signed certificate:

```bash
openssl req -x509 -newkey rsa:2048 -keyout cert.key -out cert.crt -days 3650 -nodes \
  -subj "/CN=localhost" -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
```

The server auto-detects `cert.crt` / `cert.key` in the project root and switches to HTTPS. On first visit, your browser will warn about the self-signed certificate — click through to proceed.

> `cert.key` and `cert.crt` are gitignored and never committed.

## Configuration

All behaviour is controlled by `config.yaml`:

| Section | What it controls |
|---|---|
| `app` | Port and window title |
| `search` | Default keywords/location pre-filled in the UI |
| `filter_out` | Keywords that cause a fetched job to be silently dropped (e.g. internship) |
| `domain_rules` | Domain tags and their trigger keywords — add/rename/remove freely |
| `style` | Font, accent color, background, etc. |

Restart the server after editing `config.yaml`.

### Adding a domain

```yaml
domain_rules:
  robotics:
    - robotics
    - robot learning
    - manipulation
    - autonomous systems
```

That's all — the sidebar filter button and the domain checkboxes in forms are generated automatically.

## Sources

| Source | Type | Notes |
|---|---|---|
| INRIA | HTML scraper | Filters by keyword and location post-scrape |
| CNRS | HTML scraper | Filters by keyword post-scrape |
| LinkedIn | HTML scraper | Requires no account |
| Welcome to the Jungle | Algolia API | Uses WTJ's own search index; location and keyword filtering |

To add or remove sources, edit `sources.py`.

## Stopping the server

Press `Ctrl+C` in the terminal. If you lost the terminal:

```bash
lsof -i :3742
kill <PID>
```
