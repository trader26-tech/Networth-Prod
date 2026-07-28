# networth.io

A personal **net-worth & wealth-management platform** for tracking every asset, income stream, and expense your household has — in one place, with live valuations, CAGR/XIRR, income calendars, and a liquidity-aware command center.

It started life as a Zerodha Kite Connect options-trading dashboard (covered calls, GTTs, scanners), and that toolkit is still here — but the project has grown into a complete picture of personal wealth: stocks, gold, real estate, bonds, FDs, ULIPs, cash, salary, and recurring income/expenses, all rolled up into a single dashboard.

Built as a **FastAPI** backend + **Angular 20** frontend, deployed as one service: FastAPI serves both the API at `/api/*` and the built Angular SPA at `/`.

---

## ✨ What it does

**Asset tracking** — each asset class is its own module with value, invested cost, CAGR, monthly income, and liquidity:
- **Stocks / Equity** — live multi-account holdings via Kite Connect, FIFO lot matching, realised P&L, and XIRR from your tradebook ([api/stocks/](api/stocks/))
- **Gold & Silver** — live-priced precious-metal pieces
- **Land · Apartments** — real estate with auto CAGR, rent income, and linked title documents
- **Bonds** — per-bond income, YTM, payout calendar & maturity ladder
- **Fixed Deposits · ULIPs** — value, payout interest, maturity, lock-in & XIRR
- **Cash** — bank + physical cash (the most liquid tier)

**Income & expenses** — salary (multi-currency → live ₹), other income (dividends, interest, rent, bonuses), monthly recurring expenses → surplus & savings rate, plus an income-receipt calendar.

**Home dashboard** — [api/dashboard/aggregate.py](api/dashboard/aggregate.py) unifies every module into one view: total net worth, allocation, per-person breakdown, liquidity tiers (how fast each asset converts to cash), and this month's expected income.

**Net-worth import** — guided Excel (`.xlsx`) upload → column mapping → asset records ([api/networth/](api/networth/)).

**Options trading toolkit** (original project) — paper-trading engine, covered calls, protected wheel, hedges, a covered-call simulator, scanners, and GTT/order management.

**Extras** — a secure document vault, an AI assistant (RAG via ChromaDB / Ollama), fundamentals sparklines (yfinance), and a standalone Rich-based terminal CLI for raw Kite Connect operations ([main.py](main.py) + [features/](features/)).

---

## 🏗 Architecture

```
api/                      FastAPI backend
  main.py                 Thin entry point — registers ~40 routers, serves the SPA
  <domain>/               Business logic per domain: engine.py / store.py / prices.py
  routes/<domain>.py      HTTP layer — one APIRouter per domain, prefix /api/<domain>
  dashboard/aggregate.py  Rolls every module up into the home dashboard
frontend/                 Angular 20 standalone-component workspace
  src/app/components/     ~41 components, mostly mirroring the backend domains
  src/app/services/       One service per domain
  src/app/app.routes.ts   Client-side routing
main.py                   Standalone terminal CLI (Rich) for raw Kite operations
features/                 CLI feature modules (orders, GTT, MF, market data, …)
```

**Persistence** — each domain typically has a JSON store *and* a `*_supabase_store.py`. When `SUPABASE_URL` + `SUPABASE_KEY` are set, data lives in Supabase Postgres (survives redeploys); otherwise it falls back to local JSON files (fine for dev). See [SUPABASE.md](SUPABASE.md).

**Market data** — live via Kite Connect when credentials are present; otherwise a built-in mock-market simulator keeps everything working (great for demos). The frontend also has a **demo mode** that serves canned data with no backend at all.

**Resilient boot** — routers register defensively in [api/main.py](api/main.py), so a single bad import won't crash the app.

---

## 🛠 Prerequisites

- **Python** ≥ 3.10 (3.11 in production)
- **Node.js** ≥ 20, **npm** ≥ 10
- *(optional)* a **Zerodha Kite Connect** developer account for live market data
- *(optional)* a **Supabase** project for persistent storage

---

## ⚙️ Setup & local dev

```bash
git clone <repo-url> networth.io
cd networth.io
cp .env.example .env        # fill in what you need (all optional for mock mode)
```

**Backend** (FastAPI):
```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
# API → http://localhost:8000     Swagger → http://localhost:8000/docs
```

**Frontend** (Angular), in a second terminal:
```bash
cd frontend
npm install
npm start                   # → http://localhost:4200
```

The frontend auto-detects localhost and points to `http://localhost:8000/api`; in production it uses the same origin (relative `/api`).

---

## 🔑 Environment variables

| Variable | Required? | What it does |
|---|---|---|
| `KITE_API_KEY` / `KITE_API_SECRET` / `KITE_ACCESS_TOKEN` | for live data | Zerodha Kite Connect. Without them the app runs in mock-data mode. |
| `SUPABASE_URL` / `SUPABASE_KEY` | for persistence | Enables Supabase Postgres storage. `SUPABASE_SERVICE_KEY` is preferred for backend access if set. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | optional | Local LLM for the AI assistant. |
| `PORT` | auto | Set by Railway; defaults to 8000. |

The committed `.env.example` has the full annotated list.

---

## 🚀 Deployment

Single-service deploy (FastAPI serves API + SPA from one URL). Railway is the primary target via [nixpacks.toml](nixpacks.toml), with [Dockerfile](Dockerfile) and a Heroku-style [Procfile](Procfile) also included. The production entry point is [start.py](start.py) (reads `PORT` from the environment and launches uvicorn). Health check: `GET /api/health`.

Full walkthrough in [DEPLOY.md](DEPLOY.md).

---

## 🧰 Tech

**Backend:** FastAPI · Uvicorn · Kite Connect · pandas · openpyxl · Supabase · yfinance · ChromaDB · Rich (CLI)
**Frontend:** Angular 20 · TypeScript · RxJS · Lightweight Charts
