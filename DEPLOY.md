# Deploying to Railway

This is a single-service deployment: FastAPI serves both the **API** at `/api/*` and the **built Angular SPA** at `/`. One URL, one health check, one bill.

## TL;DR

1. Push this repo to GitHub.
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → pick this repo.
3. Once the service spins up, open it → **Variables** tab → add the env vars from `.env.example` you need (at minimum `KITE_API_KEY` and `KITE_API_SECRET` if you want live data).
4. Click **Deploy**. Wait ~5-7 minutes for the first build (Node + Python + Angular).
5. Open the generated `*.up.railway.app` URL — you'll see the dashboard.

## What gets built (auto)

Railway uses [`nixpacks.toml`](nixpacks.toml) which:

1. Installs Python 3.11 + Node 20
2. Creates a venv at `/opt/venv`
3. Runs `pip install -r requirements.txt`
4. Runs `npm ci` in `frontend/`
5. Runs `npx ng build --configuration=production` → outputs to `frontend/dist/frontend/browser/`
6. Starts uvicorn:
   ```
   uvicorn api.main:app --host 0.0.0.0 --port $PORT --workers 2 --proxy-headers
   ```

The FastAPI app auto-detects the built Angular folder and serves it with SPA fallback (so `/portfolio`, `/covered-call`, etc. all work on direct URL load).

## Required environment variables

| Variable | Required? | What it does |
|---|---|---|
| `KITE_API_KEY` | for live data | Kite Connect API key |
| `KITE_API_SECRET` | for live data | Kite Connect API secret |
| `KITE_ACCESS_TOKEN` | for live data | Daily-rotating access token (set after login) |
| `OLLAMA_BASE_URL` | optional | If using local LLM for AI assistant |
| `OLLAMA_MODEL` | optional | Default `llama3.2` |
| `PORT` | auto | Railway sets this automatically |

Without Kite credentials the app runs in **mock-data mode** — perfect for sharing demos.

## Health check

Railway pings `/api/health` every 30s; if it fails 3× the service restarts. The endpoint returns `{ "status": "ok", "kite_connected": true/false }`.

## Verifying the deployment

After deploy, hit:
- `https://your-app.up.railway.app/api/health` → JSON status
- `https://your-app.up.railway.app/` → the Angular dashboard

## Local dev (still works)

```bash
# Backend
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm start  # → http://localhost:4200
```

The frontend automatically detects it's running on localhost and points to `http://localhost:8000/api`. In production it uses the same origin (relative `/api`).

## Alternate deploys

- **Docker**: `docker build -t zerodha-pro . && docker run -p 8000:8000 -e KITE_API_KEY=... zerodha-pro`
- **Heroku-style**: `Procfile` is included; `heroku create` will work the same way as Railway.
- **Custom domain**: in Railway, add your domain under **Settings → Networking**. The app needs zero changes — same-origin API detection handles any hostname.

## Troubleshooting

**Build fails on `ng build`?**
Check Railway build logs. Most common: peer-dependency mismatch. Bump `frontend/package.json` and re-deploy.

**`npm ci` complains about lockfile mismatch?**
Run `npm install` locally to refresh `frontend/package-lock.json`, commit, re-deploy.

**WebSocket not connecting in production?**
Already handled — `ticker.service.ts` swaps `ws://` ↔ `wss://` based on `window.location.protocol`. If your reverse proxy (e.g. Cloudflare) needs special config for WS, enable WebSocket support in its dashboard.

**`api/cc_positions.json` resets on every deploy?**
Railway containers are ephemeral. The cleanest fix is to enable Supabase — see [SUPABASE.md](SUPABASE.md) for a 5-minute walkthrough. Set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in Railway → Variables and your covered-call positions will persist across deploys forever (free tier handles it).

## File reference

| File | Purpose |
|---|---|
| `nixpacks.toml` | Railway build instructions |
| `railway.json` | Service-level config (health check, restart policy) |
| `Procfile` | Heroku-style start command (also picked up by Railway) |
| `runtime.txt` | Python version pin |
| `Dockerfile` | Optional Docker build |
| `.env.example` | Required env vars |
| `.dockerignore` | Files excluded from Docker context |
