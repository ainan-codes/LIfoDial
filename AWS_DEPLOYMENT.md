# Deploying Lifodial: AWS (backend + worker) + Vercel (frontend)

**Current state:** the backend (`lifodial-api`) and worker (`lifodial-agent`) are live on Render's free tier (see `render.yaml`), and the frontend is already set up to deploy on Vercel (`frontend/vercel.json`, `frontend/README_DEPLOY.md`). This guide moves the backend and worker to AWS (EC2 + RDS + ElastiCache) and finalizes the Vercel frontend deploy, so you're no longer dependent on Render's free-tier cold-start behavior.

Architecture after this migration:

```
Vercel (frontend, static React/Vite)
        │  HTTPS
        ▼
EC2 (nginx) ──▶ backend container (FastAPI, port 8001)
        │              │
        │              ├──▶ RDS PostgreSQL
        │              └──▶ ElastiCache Redis
        └──▶ livekit-agent container (Pipecat/LiveKit worker)
                       ├──▶ RDS PostgreSQL
                       └──▶ LiveKit Cloud (SFU)
```

Files added for this migration (all in the repo already):

| File | Purpose |
|---|---|
| `docker-compose.aws.yml` | Runs backend + worker + nginx only (no local Postgres/Redis containers) |
| `nginx.aws.conf` | Reverse proxy for the API domain only — no frontend upstream |
| `.env.aws.example` | Template `.env` with RDS/ElastiCache placeholders |
| `scripts/aws_ec2_setup.sh` | One-time EC2 bootstrap (Docker, firewall, clone repo) |
| `scripts/deploy_aws.sh` | `initial` / `update` / `ssl` / `logs` / `status` commands |
| `.github/workflows/deploy-aws.yml` | Auto-deploy to EC2 over SSH after CI passes on `main` |

---

## Part A — AWS backend + worker

### A1. Create a VPC (or use the default one)

Every account has a default VPC — it's fine to use it for a single-server setup. Note its VPC ID and at least one subnet; you'll need them for RDS, ElastiCache, and EC2.

### A2. RDS — PostgreSQL

1. RDS console → **Create database** → Standard create → PostgreSQL (version 16, matching `postgres:16-alpine` in the old compose file).
2. Templates: **Free tier** (db.t3.micro / db.t4g.micro) to start, upgrade later if needed.
3. Settings: DB instance identifier `lifodial-db`, master username, master password (save it).
4. Connectivity: same VPC as your future EC2 instance. Public access: **No**. Create a new security group `lifodial-db-sg`.
5. Additional configuration: initial database name `lifodial`.
6. Create, wait ~5–10 min, then copy the **endpoint** (e.g. `lifodial-db.xxxxxxxxxx.ap-south-1.rds.amazonaws.com`).

### A3. ElastiCache — Redis

1. ElastiCache console → **Create cluster** → Redis (or "Valkey", AWS's Redis-compatible fork — either works for this app's usage).
2. Cluster mode: disabled. Node type: `cache.t3.micro` (free-tier eligible).
3. Same VPC as EC2/RDS. Create/attach a security group `lifodial-cache-sg`.
4. Create, wait a few minutes, then copy the **primary endpoint**.

### A4. Security groups

- `lifodial-db-sg`: inbound rule allowing port **5432** from the EC2 instance's security group (not from `0.0.0.0/0`).
- `lifodial-cache-sg`: inbound rule allowing port **6379** from the EC2 instance's security group.
- EC2's security group (`lifodial-ec2-sg`, created in A5): inbound **22** (SSH, ideally restricted to your IP), **80**, **443** from `0.0.0.0/0`.

### A5. Launch the EC2 instance

1. EC2 console → **Launch instance**.
2. AMI: **Ubuntu Server 22.04 LTS**.
3. Instance type: **t3.small** minimum (2 GB RAM — the backend, worker, and nginx all run on this one box; `t3.micro`'s 1 GB will swap under real call load).
4. Key pair: create or reuse one; download the `.pem`.
5. Network: same VPC as RDS/ElastiCache. Create security group `lifodial-ec2-sg` per A4.
6. Storage: 20 GB gp3 is enough.
7. Launch, then allocate and associate an **Elastic IP** to the instance (Elastic IPs → Allocate → Associate) so the address never changes on reboot.

### A6. DNS

Point an A record — e.g. `api.yourdomain.com` — at the Elastic IP, in whatever DNS provider you use (Route 53 if you want it all in AWS, or your existing registrar).

### A7. Bootstrap the instance

```bash
scp -i your-key.pem scripts/aws_ec2_setup.sh ubuntu@<ELASTIC_IP>:~/
ssh -i your-key.pem ubuntu@<ELASTIC_IP>
chmod +x aws_ec2_setup.sh && sudo ./aws_ec2_setup.sh
```

This installs Docker + Compose, sets up `ufw`, and clones the repo into `/opt/lifodial`.

### A8. Configure environment variables

```bash
cp .env.aws.example .env   # on your machine
# fill in DATABASE_URL (A2 endpoint), REDIS_URL (A3 endpoint),
# GEMINI_API_KEY, LIVEKIT_*, SARVAM_API_KEY, SECRET_KEY, CORS_ORIGIN, etc.
scp -i your-key.pem .env ubuntu@<ELASTIC_IP>:/opt/lifodial/.env
```

`SECRET_KEY` must be a real 32+ byte value in production — `backend/config.py` refuses to boot otherwise. Generate one with:

```bash
python -c "import secrets;print(secrets.token_hex(32))"
```

This same `.env` is shared by both the `backend` and `livekit-agent` containers (see `docker-compose.aws.yml`), which matters because `SECRET_KEY` must be identical between them — it's used for provider-key encryption at rest.

Also edit `nginx.aws.conf` on the server and replace `api.yourdomain.com` with your real domain (both `server_name` lines).

### A9. First deploy

```bash
ssh -i your-key.pem ubuntu@<ELASTIC_IP>
cd /opt/lifodial
chmod +x scripts/deploy_aws.sh
./scripts/deploy_aws.sh initial
```

This builds and starts `backend`, `livekit-agent`, and `nginx`, then runs `alembic upgrade head` against RDS. Check it's healthy:

```bash
curl http://localhost:8001/health
./scripts/deploy_aws.sh status
./scripts/deploy_aws.sh logs
```

### A10. SSL

```bash
./scripts/deploy_aws.sh ssl api.yourdomain.com you@example.com
```

This gets a Let's Encrypt cert via certbot and restarts nginx. Your API is now live at `https://api.yourdomain.com`.

### A11. CI/CD (optional but included)

`.github/workflows/deploy-aws.yml` SSHes into the instance and runs `./scripts/deploy_aws.sh update` after CI passes on `main`. Add these repo secrets (GitHub repo → Settings → Secrets and variables → Actions):

- `EC2_HOST` — the Elastic IP or `api.yourdomain.com`
- `EC2_USER` — `ubuntu`
- `EC2_SSH_KEY` — the private key contents (the `.pem` file), not the `.pub`

Without this, just re-run `./scripts/deploy_aws.sh update` over SSH manually after each push.

---

## Part B — Frontend on Vercel

`frontend/vercel.json` (SPA rewrites) is already in the repo, so this is close to zero-config.

1. `npm i -g vercel` (or connect the GitHub repo directly in the Vercel dashboard for auto-deploy-on-push instead of the CLI).
2. From `frontend/`: `vercel login` then `vercel --prod`. Root directory: `frontend`. Framework: Vite (auto-detected).
3. In the Vercel project's **Settings → Environment Variables**, set:
   - `VITE_API_URL` = `https://api.yourdomain.com`
4. Redeploy after setting env vars (`vercel --prod` again, or a new push if using Git integration) — Vite bakes env vars in at build time.
5. Optional: add a custom domain for the frontend under **Settings → Domains**.

### CORS

`backend/main.py` already whitelists `https://lifodial.vercel.app` by default. If you're using a different Vercel URL or a custom domain, set `CORS_ORIGIN` in the EC2 `.env` to that exact URL (already templated in `.env.aws.example`) and redeploy the backend (`./scripts/deploy_aws.sh update`).

---

## Part C — Cutover checklist

- [ ] RDS reachable from EC2, `alembic upgrade head` succeeded
- [ ] ElastiCache reachable from EC2 (`docker-compose -f docker-compose.aws.yml exec backend python -c "import redis; redis.from_url('$REDIS_URL').ping()"`)
- [ ] `https://api.yourdomain.com/health` returns `{"status": "ok", ...}`
- [ ] A real test call completes end-to-end (worker registers with LiveKit, dispatch succeeds — no cold-start issue on EC2 since the container never sleeps)
- [ ] Vercel frontend loads and successfully calls the new API (check browser network tab for CORS errors)
- [ ] `SUPERADMIN_PASSWORD` and all provider API keys copied over correctly (diff against Render's env vars before decommissioning Render)
- [ ] DNS TTL lowered in advance if you're repointing an existing domain, to minimize cutover downtime
- [ ] Only after the above is confirmed: pause or delete the Render services to stop paying/using free-tier hours there

## Part D — Rough cost (ap-south-1 / similar regions, monthly)

- EC2 t3.small: ~$15
- RDS db.t3.micro (Single-AZ): ~$13
- ElastiCache cache.t3.micro: ~$12
- Elastic IP: free while attached to a running instance
- Data transfer: usually a few dollars unless call volume is very high

Total: roughly $40–45/month, versus $0 on Render's free tier — but without the cold-start/spin-down problems documented in `backend/services/agent_worker.py`. If cost matters more than the cold-start fix, self-hosting Postgres/Redis as containers on the same EC2 box (like the original `docker-compose.yml`) instead of RDS/ElastiCache brings this down to just the ~$15 EC2 cost.

## Rollback

Render services aren't touched by any of this — they keep running until you pause them, so you can flip `VITE_API_URL` back to `https://lifodial.onrender.com` in Vercel at any point if something's wrong on AWS.
