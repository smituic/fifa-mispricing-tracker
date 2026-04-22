# Kalshi Mispricing Tracker — Backend

Real-time pricing analysis backend that surfaces mispriced outcomes in Kalshi's FIFA markets by comparing them against sportsbook-implied fair probabilities.

**Live demo:** [mispricing.smitp.dev](https://mispricing.smitp.dev)

**Frontend repo:** [fifa-mispricing-frontend](https://github.com/smituic/fifa-mispricing-frontend)

**Stack:** Python 3.12 · FastAPI · SQLite · systemd · Oracle Cloud · Cloudflare Tunnel

---

## What it does

Kalshi lists binary prediction contracts on FIFA match outcomes. Major sportsbooks publish odds on the same outcomes. When the two disagree after accounting for bookmaker vig, there's an edge worth surfacing.

This backend:

- Polls Kalshi and [The Odds API](https://the-odds-api.com/) on an interval
- Devigs sportsbook odds into fair probabilities and averages across books
- Fuzzy-matches Kalshi markets to sportsbook events (team-name normalization)
- Computes expected value (EV) per outcome vs. Kalshi's mid-price
- Scores confidence and liquidity on a 0–10 scale with human-readable labels
- Persists snapshots to SQLite so the frontend can plot EV movement over time
- Exposes a JSON API consumed by the Next.js frontend

## Architecture

```
                   ┌──────────────────────────────┐
                   │  https://mispricing.smitp.dev │
                   │     Next.js on Vercel         │
                   └──────────────┬───────────────┘
                                  │ HTTPS
                                  │ fetch(`${NEXT_PUBLIC_API_URL}/kalshi/...`)
                                  ▼
                   ┌──────────────────────────────┐
                   │   https://api.smitp.dev       │
                   │     Cloudflare Tunnel         │  TLS terminated here
                   └──────────────┬───────────────┘
                                  │ encrypted tunnel
                                  ▼
   ┌───────────────────────────────────────────────────────────┐
   │         Oracle Cloud VM  (Ubuntu 24.04, Ashburn VA)         │
   │                                                             │
   │   cloudflared  ──>  uvicorn 127.0.0.1:8000  ──>  FastAPI    │
   │                              │                              │
   │                              ▼                              │
   │                    snapshot_service (async)                 │
   │                              │                              │
   │                 ┌────────────┼────────────┐                 │
   │                 ▼            ▼            ▼                 │
   │            Kalshi API   Odds API     SQLite history         │
   │                                                             │
   │   systemd supervises:  fifa-backend.service,  cloudflared   │
   └───────────────────────────────────────────────────────────┘
```

Nothing on the VM is exposed directly to the internet. The tunnel makes an outbound connection to Cloudflare's edge; requests to `api.smitp.dev` are routed back through that tunnel to uvicorn on localhost.

## Methodology

### Devigging (removing bookmaker margin)

Bookmakers publish odds whose implied probabilities sum to more than 100%; the excess is their margin ("vig"). To recover a fair per-outcome probability:

```
p_implied(i)  = 1 / decimal_odds(i)

p_fair(i)     = p_implied(i) / Σ_j p_implied(j)
```

Each book's three-way market (home / draw / away) is normalized independently.

### Multi-book consensus

Once every book's fair probabilities are computed, the consensus is the arithmetic mean across all books quoting the match:

```
p_consensus(i) = (1 / N) · Σ_k p_fair(i, book_k)
```

Averaging dampens single-book noise and reduces sensitivity to an individual book's model.

### Edge (expected value)

The edge for each outcome is the signed difference between the consensus fair probability and Kalshi's mid-price:

```
edge(i) = p_consensus(i) − p_kalshi_mid(i)
```

- `edge > 0` → Kalshi is underpricing the outcome (**undervalued**, BUY signal)
- `edge < 0` → Kalshi is overpricing the outcome (**overvalued**, AVOID signal)

Classification uses a threshold (`MIN_EV_SIGNAL`, default 1%) to suppress sub-noise flips:

```
signal = "Undervalued"   if edge >  threshold
       = "Overvalued"    if edge < -threshold
       = "Fair"          otherwise
```

### Liquidity score

Per outcome, on a 0–10 scale, combining Kalshi volume, open interest, and bid-ask spread:

```
volume_score         = min(volume / 200, 1)
open_interest_score  = min(open_interest / 200, 1)
spread_score         = 1 − min(spread_pct / 1, 1)

liquidity = (0.4 · volume_score
           + 0.4 · open_interest_score
           + 0.2 · spread_score) · 10
```

The score is mapped to a label for the UI: `Very Thin` / `Thin` / `Tradable` / `Liquid` / `Deep`.

### Confidence score

A 0–10 score reflecting trust in the fair probability estimate, driven primarily by book count and cross-book agreement (lower variance across books → higher confidence). Mapped to `Very Low` / `Low` / `Moderate` / `High` / `Very High` for the UI.

### Opportunity ranking

Opportunities aren't ranked by raw EV alone — a huge edge on an illiquid, single-book market is less actionable than a moderate edge on a deep, well-covered market. The ranking score blends all three:

```
rank_score = EV            · w_ev
           + confidence/10  · 0.3
           + liquidity/10   · 0.1
```

This surfaces opportunities that are *both* mispriced *and* tradable with reasonable certainty.

## API

Base URL in production: `https://api.smitp.dev`

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness probe |
| `GET /kalshi/fifa/matches` | All current FIFA matches with top EV per match |
| `GET /kalshi/fifa/opportunities` | Ranked list of best current edges |
| `GET /kalshi/fifa/ev-movers?hours=6` | Biggest EV changes over a window |
| `GET /kalshi/fifa/match/{match_id}` | Full analysis for a single match |
| `GET /kalshi/fifa/match/{match_id}/history?hours=6` | Historical EV and probability snapshots |

Interactive docs at `{BASE_URL}/docs` (Swagger UI, auto-generated by FastAPI).

## Tech stack

- **Framework:** FastAPI 0.129 with async startup for the snapshot loop
- **Runtime:** Python 3.12, uvicorn with uvloop + httptools
- **Data:** SQLite (`sqlite3` stdlib) for snapshot history; lightweight and adequate for this workload
- **HTTP clients:** `httpx` (async) and `requests`
- **Config:** `pydantic-settings` reading from `.env`
- **Deployment:** Oracle Cloud Always Free VM · systemd · Cloudflare Tunnel

## Running locally

### Prerequisites

- Python 3.12+
- An API key from [The Odds API](https://the-odds-api.com/) (free tier is sufficient)

### Setup

```bash
git clone https://github.com/smituic/fifa-mispricing-tracker.git
cd fifa-mispricing-tracker

python3.12 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set ODDS_API_KEY

uvicorn app.main:app --reload
```

API at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Environment variables

See [`.env.example`](./.env.example). Required:

- `ODDS_API_KEY` — Odds API key
- `DEV_MODE` — must be `false` in production (defaults to `false`)

## Production deployment

The backend runs on an Oracle Cloud Always Free VM (Ubuntu 24.04, Ashburn) with two systemd services:

- [`fifa-backend.service`](./deploy/fifa-backend.service) — wraps `uvicorn app.main:app` as a supervised process. Single worker (the snapshot loop runs in-process and must not be parallelized). Auto-restarts on failure. Binds to `127.0.0.1:8000` only — never directly exposed.
- `cloudflared.service` — maintains four redundant outbound connections to Cloudflare's edge, routing `https://api.smitp.dev` → `http://127.0.0.1:8000` inside the VM.

Deployment workflow:

```bash
# On the VM
cd ~/fifa-mispricing-tracker
git pull
sudo systemctl restart fifa-backend
```

Logs: `journalctl -u fifa-backend -f`

### Why this setup

- **No public ports for the API.** Cloudflare Tunnel uses an outbound connection from the VM, so the FastAPI server is unreachable from the public internet except via the tunnel. No firewall rules to maintain, no certificates to renew, no IP to hide.
- **Free, always-on.** Oracle's Always Free tier includes an ARM VM (or AMD micro shape as used here) that never expires. Combined with Cloudflare Tunnel (free) and Vercel's Hobby tier for the frontend, total cost is $0/month.
- **Crash-safe.** systemd restarts the backend within 5 seconds if it exits unexpectedly, and starts it automatically on VM boot.
- **Single worker intentionally.** The snapshot loop (`asyncio.create_task(start_snapshot_loop())` at startup) runs inside the uvicorn process. Multiple workers would mean multiple loops, duplicate API calls, and racing DB writes.

## Project structure

```
fifa-mispricing-tracker/
├── app/
│   ├── main.py                          # FastAPI entrypoint, CORS, startup tasks
│   ├── core/
│   │   ├── config.py                    # Pydantic settings
│   │   └── dependencies.py              # Shared FastAPI dependencies
│   ├── api/
│   │   └── routes/
│   │       └── kalshi.py                # /kalshi/fifa/* endpoints
│   └── services/
│       ├── kalshi_client.py             # Kalshi API wrapper
│       ├── odds_client.py               # Odds API wrapper (15-min cache)
│       ├── sportsbook_fair_model.py     # Devig + multi-book consensus
│       ├── mispricing.py                # Edge / EV / signal classification
│       ├── match_analysis_service.py    # Scoring, labels, ranking
│       └── snapshot_service.py          # Background loop + SQLite persistence
├── deploy/
│   └── fifa-backend.service             # systemd unit (symlinked into /etc/systemd/system)
├── .env.example
├── requirements.txt
└── README.md
```

## Non-goals and honest caveats

- **This is a portfolio project, not a trading tool.** The numbers are for analysis and demonstration, not for placing real bets.
- **No authentication.** The API is effectively read-only and exposes no user data, so there's nothing to protect.
- **No horizontal scaling.** Single VM, single worker, single SQLite file. Appropriate for the workload; would not scale to thousands of concurrent users.
- **Odds coverage is partial.** Not every Kalshi market has a clean sportsbook counterpart — fuzzy matching on team names has gaps. Markets without a match are shown but carry no EV signal.

## License

Portfolio / demo project. Data shown is for illustration only.
