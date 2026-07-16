# Lifodial — Deployment Guide

## Local Development

### Prerequisites
- Python 3.11+
- Node.js 20+
- Redis (optional — backend falls back to in-memory)

### Backend
```bash
# Create venv and install
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt

# Copy env
cp .env.example .env
# Edit .env — set GEMINI_API_KEY, SARVAM_API_KEY at minimum

# Run
uvicorn backend.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173

### Health Check
```
GET http://localhost:8000/health
```
Returns database type, connection status, and environment.

---

## Docker Compose (Production)

### 1. Environment
```bash
cp .env.example .env
```
Set these at minimum:
| Variable | Example |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@postgres/lifodial` |
| `POSTGRES_USER` | `lifodial` |
| `POSTGRES_PASSWORD` | `<strong-password>` |
| `GEMINI_API_KEY` | `AIza...` |
| `SARVAM_API_KEY` | `...` |
| `LIVEKIT_URL` | `wss://your-livekit.example.com` |
| `LIVEKIT_API_KEY` | `...` |
| `LIVEKIT_API_SECRET` | `...` |

### 2. Build & Start
```bash
docker compose up -d --build
```

Services started:
- **postgres** — PostgreSQL 16
- **redis** — Redis 7 with AOF persistence
- **backend** — FastAPI on :8000 (with healthcheck)
- **livekit-agent** — Voice pipeline worker
- **frontend** — React app served by nginx
- **nginx** — Reverse proxy on :80/:443

### 3. Verify
```bash
curl http://localhost/health
docker compose ps
docker compose logs -f backend
```

### 4. SSL (Let's Encrypt)
```bash
# Install certbot on host or use a certbot container
certbot certonly --webroot -w /var/www/certbot -d yourdomain.com
```
Update `nginx.conf` to reference certs from `/etc/letsencrypt/`.

---

## VPS Deployment (Ubuntu 22.04)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 2. Clone repo
git clone <your-repo-url> /opt/lifodial
cd /opt/lifodial

# 3. Configure
cp .env.example .env
nano .env   # Fill in API keys and passwords

# 4. Deploy
docker compose up -d --build

# 5. Verify
curl http://localhost/health
```

---

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────────┐
│  Nginx   │────▸│ Frontend │     │  LiveKit SFU  │
│  :80/443 │     │  (React) │     │  (external)   │
│          │────▸│          │     └──────┬─────────┘
│          │     └──────────┘            │
│          │────▸┌──────────┐     ┌──────▼─────────┐
│          │     │ Backend  │◀───▸│ LiveKit Agent   │
│          │     │ FastAPI  │     │ (voice pipeline)│
│          │     └────┬─────┘     └────────────────┘
└──────────┘          │
                ┌─────▼─────┐   ┌───────────┐
                │ PostgreSQL │   │   Redis    │
                │   :5432    │   │   :6379    │
                └────────────┘   └───────────┘
```

## Voice Worker Keep-Warm (Render free tier)

The production voice worker (`lifodial-agent` on Render, free plan) spins down
after ~15 min without **inbound HTTP** — its LiveKit WebSocket does NOT count
as activity. A spun-down worker is deregistered from LiveKit, so inbound calls
dispatched during that window fail or wait through a cold start.

**Ping URL:** `https://lifodial-agent.onrender.com/`
- Returns `200 OK` only when the worker process is healthy **and** its LiveKit
  connection is up; `503` otherwise. The ping therefore doubles as a LiveKit
  registration check.
- `https://lifodial-agent.onrender.com/worker` returns JSON status
  (`agent_name`, `worker_load`, `active_jobs`, `sdk_version`).
- Both are served by the livekit-agents built-in health server (bound to
  Render's `$PORT` in `backend/agent/__main__.py`); handlers are trivial
  in-process reads and do not touch the call path.

**Pinger:** `.github/workflows/keepalive.yml` pings the backend `/health` and
the worker `/` every 5 minutes (GitHub cron is best-effort and can lag several
minutes, so 5 min leaves margin under the 15-min window; recommended interval
if using an external pinger instead: ≤10 min).

**Honest caveat — the pinger reduces but does NOT eliminate cold-start risk:**
- If a ping fails or GitHub delays the cron, the worker can still spin down;
  a call arriving then hits a ~60–120 s cold start (measured: 52.8 s) or fails.
- Free instances also share 750 instance-hours/month across services.

**For a real clinic going live:** flip the worker to always-on in
`render.yaml` — service `lifodial-agent` (~line 125): change `plan: free` to a
paid tier and `type: web` to `type: worker` (the comment above that block says
the same). Then the keep-warm ping for the worker becomes unnecessary.

## Key Endpoints
| Endpoint | Description |
|---|---|
| `GET /health` | System health + DB status |
| `GET /agents` | List agents |
| `POST /agents/{id}/web-call-token` | Get LiveKit token for browser call |
| `POST /agents/{id}/outbound-call` | Initiate SIP outbound call |
| `GET /agents/{id}/call-records` | Call history for agent |
| `GET /phone-numbers` | List virtual numbers |
| `WS /ws/calls/{tenant_id}` | Realtime call events |
