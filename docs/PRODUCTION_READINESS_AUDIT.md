# Lifodial — Production Readiness Audit & Optimization Report

**Audit date:** 2026-07-05
**Product:** Lifodial — AI Voice Receptionist SaaS for clinics (India & Middle East)
**Stack:** FastAPI (Python 3.11) + Pipecat/LiveKit voice agent · React 19 + Vite + TS frontend · Supabase Postgres · Render + Vercel
**Verdict:** 🔴 **NOT production-ready.** Overall readiness ≈ **20%.** The product is functionally rich and demos well, but it has **no authentication or tenant isolation**, stores secrets in plaintext, cannot scale beyond one process, has broken migrations, and its core low-latency voice path is bypassed by a blocking sequential loop. Multiple **Critical** issues are exploitable by anyone with the URL today.

> Scope note: this report is an audit against the **existing** product vision. It does not propose rebuilding features — only what is required to make what exists safe, correct, fast, and operable at scale.

---

## 1. Executive Summary

Lifodial is a multi-tenant SaaS that answers clinic phone/web calls with an AI receptionist: it transcribes the caller (Sarvam STT), reasons over a clinic-specific prompt + knowledge base (Gemini/OpenAI/Groq/Anthropic), speaks back (Sarvam/ElevenLabs TTS), and books/reschedules appointments, logging everything and metering credits per clinic.

The engineering is ambitious and the feature surface is broad (dynamic model selection, voice browser, embeddable widget, superadmin console, credits/billing). **But it is built as a demo, not a production system.** The single most important fact:

> **There is no authentication anywhere.** Login does not check the password (`agents.py:323` — *"Not verified yet — just looked up"*), the frontend gate is a forgeable `localStorage` boolean, admin/superadmin/tenant APIs have zero auth guards, and the client passes `tenant_id` itself. Any visitor can read, modify, or delete **every clinic's** data, patient PII, transcripts, and credits from a browser console. This alone is an absolute go-live blocker.

Beyond auth, four systemic problems block production:

1. **Secrets in the clear.** Passwords are plaintext; provider API keys are base64 (not encrypted); live production keys (Render, Sarvam, Gemini, Groq, ElevenLabs, **LiveKit secret**) sit in `push_render_env.ps1` on disk — all must be rotated.
2. **Cannot scale.** Session/call state is a process-local Python dict (`redis_client.py:5`); the server is pinned to `--workers 1`; Redis is provisioned but never used. More than one instance breaks calls.
3. **Broken data layer.** Alembic migrations are abandoned (baseline is `pass`); schema is created by `create_all` + ~10 raw `UPDATE` scripts run on **every** startup; FK/cascade rules contradict each other; `create_tenant` crashes on every call; credit deduction races and can't stop a zero-balance clinic.
4. **The real-time path isn't real-time.** The web-call turn is fully sequential and non-streaming, blocks the event loop on `subprocess.run(ffmpeg)`, and leaks conversation history across concurrent callers (a PII breach). The streaming STT/TTS infrastructure exists but is wired only to debug pages.

**Recommended stance:** Do not onboard real clinics or real patient data until Phase 1 (below) is complete. Treat all currently-deployed secrets as compromised and rotate immediately.

---

## 2. Project Overview

| Aspect | Detail |
|---|---|
| **What it does** | AI voice receptionist that answers clinic calls, books/reschedules/cancels appointments, answers FAQs from a per-clinic knowledge base, logs calls, and meters usage as credits. |
| **Target users** | Clinics/hospitals in India & the Middle East; multi-language (Hindi, English, Malayalam, Kannada, Arabic, + all Sarvam-supported Indic languages). Two personas: **clinic admin** (dashboard, agent config, appointments) and **Lifodial superadmin** (all clinics, billing, credits, onboarding). |
| **Core value** | 24/7 multilingual reception without human staff; low-latency (<3s target) natural conversation; embeddable on any clinic website via a one-line `<script>` widget; phone (LiveKit/SIP) + web calls. |
| **Channels** | Inbound telephony (LiveKit + SIP), browser web-calls (WebSocket + LiveKit), embeddable chat/voice widget. |
| **Monetization** | Prepaid credits per clinic, deducted per call minute (`credit_service.py`). |

### User journey (as built)
1. Clinic is onboarded (superadmin creates tenant → auto-generates plaintext password + fake AI number).
2. Clinic admin "logs in" (email lookup, **no password check**) → localStorage flag set.
3. Admin configures agent (voice, language, LLM model, prompt, knowledge base).
4. Patient calls the AI number / clicks the web widget → STT→LLM→TTS loop → booking.
5. Call is logged; credits deducted (after the call, non-atomically); optional webhook/Google-Sheets/Telegram notification.

---

## 3. Current Architecture

```
                          ┌───────────────────────────────────────────┐
                          │  Clients                                     │
   Patient phone ──SIP──▶ │  • Telephony caller                          │
   Clinic website ──────▶ │  • Embed widget (widget.js)                  │
   Admin browser ───────▶ │  • React SPA (Vercel)                        │
                          └───────────────┬─────────────────────────────┘
                                          │ HTTPS / WSS (NO AUTH)
                          ┌───────────────▼─────────────────────────────┐
                          │  FastAPI backend (Render, --workers 1)        │
                          │  ┌─────────────┐  ┌───────────────────────┐  │
                          │  │ REST routers│  │ WS voice loop          │  │
                          │  │ (21 routers)│  │ agent_test.py          │  │
                          │  └─────┬───────┘  │  STT→LLM→TTS (serial)  │  │
                          │        │          └──────────┬────────────┘  │
                          │  in-memory dict "sessions" ◀─┘ (redis_client) │
                          └───┬─────────────┬──────────────┬─────────────┘
                              │             │              │
             ┌────────────────▼──┐   ┌──────▼──────┐   ┌───▼───────────────┐
             │ Supabase Postgres │   │ AI providers│   │ LiveKit Cloud     │
             │ (NullPool)        │   │ Sarvam/Gemini│  │ (agent dispatch?) │
             └───────────────────┘   │ OpenAI/Groq  │  └───────────────────┘
                                     │ ElevenLabs   │
                                     └──────────────┘

   Separate (undeployed) Pipecat worker: backend/agent/pipeline.py  ── telephony
```

**Three parallel "agent" implementations exist and disagree:**
| Path | File | Status |
|---|---|---|
| Pipecat + LiveKit (streaming, correct) | `backend/agent/pipeline.py` | Real, but **no declared deployment** |
| Custom WS turn loop (sequential, non-streaming) | `backend/routers/agent_test.py` | Real — **serves web/embed calls** |
| Legacy LiveKit `Agent` | `backend/agent.py` + `agent/gemini.py` + `agent/sarvam.py` | **Mock/broken** — returns empty audio & fake transcripts |

---

## 4. Production Readiness Scorecard

| Category | Readiness | Grade | Notes |
|---|---:|:--:|---|
| **Authentication & Authorization** | **5%** | F | No auth anywhere; login doesn't verify password; forgeable client-side gate. |
| **Secrets & Data protection** | 10% | F | Plaintext passwords, base64 "encryption", live keys on disk. |
| **Multi-tenant isolation** | 5% | F | Client-supplied `tenant_id`, no ownership checks (universal IDOR). |
| **Database & migrations** | 25% | D | Alembic abandoned; `create_all`+startup UPDATEs; FK contradictions; crash bug. |
| **Real-time voice pipeline** | 35% | D | Works but sequential/blocking; cross-session bleed; streaming infra bypassed. |
| **Reliability & failure recovery** | 20% | D- | LLM errors → silence; fire-and-forget bookings; no circuit breakers. |
| **Scalability** | 15% | F | In-memory sessions; `--workers 1`; can't run >1 instance. |
| **Frontend** | 30% | D | Fake auth, no code splitting (819KB), no error boundary, mock-data store. |
| **DevOps / CI-CD** | 20% | D- | No CI/tests gate; destructive env script; ephemeral uploads. |
| **Observability** | 10% | F | Sentry declared but not installed/wired; no metrics/alerts/log shipping. |
| **API design** | 40% | C- | Reasonable shapes; no versioning, inconsistent errors, no rate limiting. |
| **Backups / DR** | 15% | F | No documented backup/restore; uploads & sessions unrecoverable. |
| **OVERALL** | **≈20%** | **F** | Multiple exploitable Critical blockers. |

**Findings by severity:** Critical **14** · High **21** · Medium **19** · Low **10**.

---

## 5. Complete Code Audit

### Structure & organization
- Reasonable router-per-domain layout (`backend/routers/*`, 21 routers) and model-per-file (`backend/models/*`). Frontend is page-per-route with shared components.
- **Two conflicting `get_db` dependencies** — `db.py:97` auto-commits; `admin.py:20` does neither (relies on manual commit/rollback per handler). Consolidate on one. *(Medium)*
- **Dead/duplicate code:** `backend/agent.py`, `agent/gemini.py`, `agent/sarvam.py` (mocks that emit empty audio); the entire `agent/providers/` package is imported nowhere and `sarvam_provider.py:6` imports a non-existent `_get_http_client` (ImportError if used). Duplicate `WebCallInterface.tsx` vs `WebCallModal.tsx`; two divergent `widget.js` files; per-model `_now()` redefinitions; `MockGemini` in `post_call_analysis.py` returns fabricated call summaries. *(High — delete or quarantine)*
- **Config contract violated:** `config.py` docstring says "never access os.environ directly," yet `db.py:19` reads `os.getenv("DATABASE_URL")` directly, bypassing pydantic. `redis_url` defined but unused. *(High)*
- **Error handling is bimodal and both modes are wrong:** some handlers swallow all exceptions and return empty 200s (`tenants.py:63`, `admin.py:159`) hiding failures from monitoring; others leak raw internals via `HTTPException(500, detail=str(e))` throughout `admin.py`/`platform.py`. *(Medium)*
- **Deprecated `datetime.utcnow()`** (tz-naive) mixed with tz-aware `DateTime(timezone=True)` columns → comparison/serialization bugs. *(Medium)*
- **Startup does data migration:** `main.py:41–301` runs ~10 raw `UPDATE`/full-scan statements on every boot, each wrapped in `except: warning`. *(Critical — see §8)*

---

## 6. Performance & Latency Audit

Target is **<3s** conversational latency. The web-call path (the one actually serving calls) cannot meet it under load.

| # | Issue | File | Impact | Fix |
|---|---|---|---|---|
| P1 🔴 | **Fully sequential, non-streaming turn:** STT (full upload) → LLM (`:generateContent`, non-streaming) → TTS (full clip) added end-to-end. | `agent_test.py:1273–1583` | p50 realistically 2–4s; misses target under any load. | Stream LLM tokens → sentence-chunked TTS; first audio after first sentence. Streaming infra already exists (§ below). |
| P2 🔴 | **`subprocess.run(ffmpeg)` blocks the event loop** on every WebM/Opus turn (default browser format). | `agent_test.py:1854–1913` | Stalls **all** concurrent calls/pings/DB tasks per transcode; serializes the server. | `asyncio.create_subprocess_exec` / `run_in_executor`. |
| P3 🟠 | **New `httpx.AsyncClient` per request** — fresh TLS handshake (~100–300ms) 2–3× per turn. | `agent_test.py:1816,2524,2574,2607,1925` | Adds hundreds of ms to hot path. | One shared module-level keep-alive client. |
| P4 🟡 | **KB + API-key DB lookups every turn** with `NullPool` (new PG connection each). | `agent_test.py:2084,2131` | Extra connect + query latency per utterance. | Cache KB + resolved key per session; cache static prompt. |
| P5 🟡 | **Hardcoded LLM params** (`maxOutputTokens:150, temp:0.7`) ignore config; larger-than-needed tokens raise latency. | `agent_test.py:2536` | Wasted tokens & latency; config silently ignored. | Read `agent.llm_temperature`/`max_response_tokens`. |
| P6 🟡 | **Smart-retry re-transcribes entire audio** on language mismatch. | `agent_test.py:1332–1366` | Doubles STT latency when it fires. | Bound/telemeter; use partial STT. |
| P7 🟢 | **`prewarm` is a no-op**; Silero VAD + first model load on first real call. | `pipeline.py:595` | Cold-start hundreds of ms on first call. | Actually load Silero in prewarm. |
| P8 🟢 | **`NullPool` discards the warm connection** immediately, so `_warmup` doesn't help the first real query. | `db.py:72`, `main.py:304` | Cold DB connect on first request. | Use a bounded pool against Supabase session pooler. |

**Streaming infra exists but is bypassed (High):** `services/sarvam_streaming.py` (streaming STT) and `agent_test.py:manage_sarvam_streaming_tts`/`relay_sarvam_audio` (streaming TTS) are wired only to standalone/debug endpoints (`/ws/streaming-stt`, `/ws/agent/{id}/tts-stream` → a debug HTML page), **not** to the production turn. Routing the live turn through them is the single biggest latency win.

**Recommended latency stack:** shared HTTP client · streaming STT (partials) · streaming LLM (token) · sentence-chunked streaming TTS · per-session KB/key cache · bounded connection pool · real Redis session store · move off Render free tier (cold starts).

---

## 7. Security Audit

> Root cause: **no authentication or authorization layer exists.** Every finding below compounds it.

### Critical
- **C1 — No auth on admin/superadmin/tenant APIs.** `admin.py` (entire), `platform.py`, `tenants.py`, `credits.py`. One `curl` can delete every clinic (`admin.py:180`), read cross-tenant patient PII (`admin.py:336`), or grant unlimited credits (`credits.py:58,81`).
- **C2 — Login doesn't verify the password.** `agents.py:326–347`; field comment `:323`. Email-only lookup returns `tenant_id`. `GET /agents/mine?email=` also returns full agent config unauthenticated.
- **C3 — Destructive endpoints unprotected / weak-secret.** `/admin/seed` & `/admin/sync-tenants-from-agents` have **no** protection; `/admin/reset-db` (drops all tables) & `/debug/audit` gated only by `X-Admin-Secret == secret_key`, which defaults to `"change_me"` (`config.py:21`) and ships as a known value in `.env.example`.
- **C4 — Provider API keys base64-obfuscated, not encrypted.** `api_key_config.py:13–22`. Any DB read yields all billable keys via `base64 -d`.
- **C5 — Frontend auth is a forgeable localStorage flag.** `RequireAuth.tsx:11`, `RequireSuperAdmin.tsx:5`. `localStorage.setItem('lifodial-superadmin','true')` opens the whole superadmin console.
- **C6 — Hardcoded superadmin creds in the client bundle.** `Login.tsx:32`, `SuperAdminLogin.tsx:11` (`admin@lifodial.com`/`lifodial2026`), with an "auto-fill" button. Compiled into public JS.
- **C7 — Login fails OPEN.** `Login.tsx:65–74` — API error/timeout still sets authed + navigates to dashboard ("demo fallback").
- **C8 — Live secrets on disk.** `push_render_env.ps1:11–40` contains real Render/Sarvam/Gemini/Groq/ElevenLabs keys and the **LiveKit secret** (not in git history, but must be rotated).

### High
- **H1 — Plaintext passwords, returned in responses.** `tenant.py:28`; `admin.py:104,122–125`; `main.py:545` (`changeme123`).
- **H2 — SSRF via tenant webhook URLs.** Unauthenticated `PUT /tenants/{id}` sets `google_sheets_webhook_url`; `sheets.py:38` posts with `follow_redirects=True` → cloud metadata/internal-service theft.
- **H3 — Cross-tenant PII leak via WS & call-records (IDOR).** `ws.py:19,67`, `web_calls.py:212` return transcripts/PII for any `tenant_id`/`agent_id`; IDs are sequential (`agent-001…`).
- **H4 — `*` CORS on embed enables LLM cost abuse.** `main.py:390`, `embed.py:146`; the in-memory rate limiter is per-process and keyed on client-supplied `session_id` (bypassable) with a broken `known` check (`embed.py:48`).
- **H5 — Weak/known `SECRET_KEY`** pushed to prod (`push_render_env.ps1:23`) overrides Render's `generateValue`.

### Medium
- **M1 — Universal IDOR** (no object-ownership checks) across `tenants`, `voice_upload`, `web_calls`, `appointments`.
- **M2 — Outbound-call toll fraud** (`web_calls.py:150`) — unauthenticated, no rate limit/allowlist.
- **M3 — Unbounded file upload** (`voice_upload.py:38` reads full body into memory; spoofable MIME).
- **M4 — No rate limiting** on login (enumeration/brute force), `/stt/transcribe`, `/platform/tts/preview` (billable).
- **M5 — Raw `text()` f-string SQL** (`main.py:505`) — safe today (hardcoded names) but an unsafe pattern.

### Low
- **L1 — Config disclosure & key-in-URL logging** (`main.py:339` Gemini key in query string; `/platform/env-status`, `/health-status` public).
- **L2 — Verbose error disclosure** (`detail=str(e)`).
- **L3 — Catch-all WS accepts arbitrary connections** (`main.py:697–729`).

**Positives:** `.env` correctly git-ignored & not in history; docs disabled in prod; main-app CORS avoids `*`+credentials; migrations use bound params.

---

## 8. Database Audit

### Critical
- **DB1 — Alembic abandoned; `create_all` is the real authority.** Baseline `1a2b3c4d5e6f_initial_schema.py:22` is `pass`; `db.py:110` runs `create_all(checkfirst=True)` and swallows errors; `env.py` references a `_run_alembic_migrations()` that doesn't exist. `create_all` never `ALTER`s, so added columns/indexes/constraints silently never apply → schema drift.
- **DB2 — Startup data migrations on every boot.** `main.py:63–293` runs raw `UPDATE`/full-scans each start, per worker, silently on failure.
- **DB3 — Secrets in DB in plaintext** (passwords, `sip_password`, `sip_auth_token`, `livekit_api_secret`) / base64 (provider keys).
- **DB4 — `create_tenant` hard crash.** `tenants.py:72` passes `tenant_id=` (no such column) and a `UUID` into a `String(36)` `id` → `TypeError` on every call.

### High
- **DB5 — FK/cascade contradictions.** `appointment.py:27–31` `doctor_id` is `NOT NULL` yet `ondelete="SET NULL"`, while `Doctor.appointments` is `cascade="all, delete-orphan"`. Deleting a doctor errors on Postgres.
- **DB6 — Manual tenant delete misses child tables.** `tenants.py:134`, `admin.py:180` delete only some children; `call_records`, `clinic_credits`, `credit_transactions`, `phone_numbers`, `bulk_call`, `embed_events`, `knowledge_bases`, `appointments` FK-fail (Postgres) or orphan (SQLite).
- **DB7 — SQLite FKs not enforced** (`PRAGMA foreign_keys` never set) — masks DB5/DB6 in dev, surfaces in prod.
- **DB8 — Model↔migration divergence** — `alembic_upgrade.sql` is missing ~10 tables and dozens of columns; booleans stored as `INTEGER`.
- **DB9 — Non-atomic credit deduction; negative balances; no pre-call gate.** `credit_service.py:73–104` read-modify-write with no row lock; deducts *after* the call; zero-balance clinics run unlimited calls.
- **DB10 — No FK on analytics/transaction linkage** (`embed_analytics.py:13`, `credit_transactions.call_id`).

### Medium / Low
- **DB11 — Missing indexes:** `appointments.slot_time` (ordered by), `appointments.doctor_id`, `call_records.started_at`; no composite `(tenant_id, created_at)`/`(tenant_id, status)`.
- **DB12 — `NullPool` everywhere** — real connect cost per request; document/verify Supabase pooler is in front.
- **DB13 — Full-table loads** (`list_tenants` loads all agent `tenant_id`s into a set).
- **DB14 — Float money columns** (`clinic_credits.py:40`) → use `Numeric(10,2)`.
- **DB15 — Booking time always `utcnow()`** (`his.py:128,186,214`) — every appointment stored as "now," not the requested slot. *(Critical for correctness — see §12.)*
- **DB16 — Committed `.wav` test blobs** in git; stale local `lifodial.db` in working tree; no documented Supabase backup/PITR.

---

## 9. API Audit

| Area | State | Recommendation |
|---|---|---|
| Auth/authz | None | Bearer JWT + role/tenant claims via a global dependency. |
| Versioning | None (`/tenants`, `/admin`, …) | Prefix `/api/v1`. |
| Request validation | Pydantic on bodies; path/query mostly unchecked | Validate + constrain; reject unknown fields. |
| Response consistency | Mixed (empty-200 on error vs raw 500 text) | Standard envelope `{data, error}`; generic 5xx. |
| Rate limiting | None (in-memory only on embed) | Redis-backed global limiter (slowapi) + per-tenant budgets. |
| Retry logic | LLM 429 only; others none | Bounded retries + circuit breaker per provider. |
| Idempotency | None on booking | Idempotency key + unique constraint. |
| Docs | `/docs` disabled in prod (good), otherwise none | Publish versioned OpenAPI + auth. |
| Third-party integrations | Sarvam/Gemini/OpenAI/Groq/Anthropic/ElevenLabs/LiveKit/Telegram/Sheets/Oxzygen HIS | Centralize clients, timeouts, breakers, key rotation. |

---

## 10. Scalability Review

**Current ceiling: one process, one instance.** Assumes thousands of concurrent callers — cannot serve dozens.

- **SC1 🔴 In-memory session store** (`redis_client.py:5`) — call/session state is a process dict; Redis provisioned (`docker-compose.yml`, `REDIS_URL`) but never used.
- **SC2 🔴 `--workers 1`** (`Dockerfile.backend:36`) — correct given SC1, but caps a box and forbids replicas; a second instance routes turns to the wrong process.
- **SC3 🔴 State lost on restart/redeploy** — mid-call state & history evaporate.
- **SC4 🟠 Event-loop blocking** (`subprocess.run` ffmpeg) serializes all calls (§6 P2).
- **SC5 🟠 `NullPool` connection fan-out** — each turn + each background task opens its own Supabase connection; N concurrent calls exhaust the pooler.
- **SC6 🟡 Unbounded global dicts** (`_conversation_history`, `_greeting_audio_cache`, …) — memory grows without eviction.

**Scaling roadmap:** (1) move sessions to Redis behind the existing interface → (2) make handlers stateless, drop `--workers 1`, enable replica autoscaling → (3) bounded async pool to session pooler + shared HTTP client → (4) offload transcode/analysis to a queue/worker → (5) object storage for uploads → (6) CDN for widget/static.

---

## 11. Infrastructure / DevOps Review

- **I1 🔴 Live secrets in `push_render_env.ps1`** — rotate all + delete script; use Render env groups.
- **I2 🔴 Weak `SECRET_KEY` pushed to prod** — generate 32+ byte random; fail startup on default in prod.
- **I3 🟠 No CI/CD gate** — only `keepalive.yml` (health cron); push-to-`main` ships with zero tests/lint/typecheck. Add GitHub Actions: `pytest` + `npm ci && tsc --noEmit && npm run build`, block deploy on failure.
- **I4 🟠 Destructive env push** — `push_render_env.ps1` replaces *all* env vars then redeploys (wipes dashboard-set vars).
- **I5 🟠 Containers run as root**; `chmod 777 uploads`; backend/agent images single-stage (ship gcc/toolchain); base images pinned to major tag only (non-reproducible).
- **I6 🟠 Ephemeral uploads & silent SQLite fallback** — `uploads/` on ephemeral disk (lost on redeploy); `db.py:21` falls back to SQLite (wiped each deploy) if `DATABASE_URL` unset. Make `DATABASE_URL` required; move uploads to S3/Supabase Storage.
- **I7 🟠 Voice agent has no declared deployment** (`Dockerfile.agent` "NOT deployed on Render"; VPS/worker undefined). LiveKit dispatch may never place the agent in web-call rooms (`__main__.py` sets `agent_name` but `web_calls.py` creates no dispatch) → silent calls.
- **I8 🟡 Render free-tier spin-down** — cold starts hurt a real-time product; `plan: starter` in `render.yaml` contradicts keepalive assumptions.
- **I9 🟡 Deps partially pinned, no lockfile** (`aiohttp>=`, `openai>=`, `pipecat-ai>=`…); no `pip-audit`/Dependabot/image scan.
- **I10 🟡 Config drift** — ports 8000/8001/10000 across compose/nginx/Render/README; two frontend deploy targets (Vercel + Render static).

---

## 12. Reliability & Resilience Review

- **R1 🔴 Booking time always wrong** (`his.py:128,186,214`) — stores `utcnow()`, not the requested slot. Every appointment is mis-scheduled.
- **R2 🟠 Cross-session conversation bleed** (`agent_test.py:2075` — `session_key = session_id or agent.id`) — concurrent callers on one agent share/interleave history → PII breach + garbage context.
- **R3 🟠 Fire-and-forget bookings can be GC'd** — `asyncio.create_task` without a retained reference (`booking_processor.py:230`, `agent_test.py:2281`, `call_logger_processor.py:*`): the caller is told "booked" while the DB write may vanish silently.
- **R4 🟠 No booking idempotency** — repeated `[ACTION: BOOK]` tags create duplicate appointments; no unique constraint/upsert.
- **R5 🟠 LLM failure → silence** — non-429 LLM errors raise and send a *text* error the caller can't hear; violates the CLAUDE.md "speak a fallback and continue" rule. Pipecat path has no try/except at all → dropped call.
- **R6 🟠 One-directional TTS fallback** — falls back to Sarvam, but if Sarvam is primary and fails → silence.
- **R7 🟡 No circuit breakers** — a hard-down provider is retried per turn, amplifying latency/cost.
- **R8 🟡 Loose cancel/reschedule matching** (`his.py:170` `ilike("%name%")` + earliest) can act on the wrong appointment.
- **R9 🟡 No backpressure** — each audio frame cancels the in-flight turn; a chunk-streaming client never completes a turn.
- **R10 🟡 `streaming_stt_ws` task leak** (`ws.py:126`) on non-disconnect exceptions.

---

## 13. Monitoring & Observability

Effectively **absent** — the biggest operational risk after security.

- **O1 🟠 Sentry declared, not wired** — `SENTRY_DSN` in `render.yaml`, but `sentry-sdk` not in `requirements.txt` and no `sentry_sdk.init()` anywhere. Add `sentry-sdk[fastapi]`, init in `main.py` on `SENTRY_DSN`.
- **O2 🟠 No metrics / tracing / log shipping** — stdout `basicConfig` only; logs live in Render's ephemeral tail. Add OpenTelemetry/Prometheus (latency per stage: STT/LLM/TTS), ship logs off-box.
- **O3 🟠 No alerting** — keepalive cron doesn't alert on failure. Add uptime + error-rate + latency + credit-balance alerts.
- **O4 🟡 Failures swallowed as "non-fatal"** across startup/warmup — invisible without Sentry.
- **O5 🟢 PostHog dependency unused** — wire product analytics or remove.

**Define SLOs:** call answer rate ≥99%, p95 turn latency <3s, booking write success ≥99.9%, uptime ≥99.5%.

---

## 14. Technical Debt Report

| Debt | Long-term risk | Fix complexity | Priority |
|---|---|---|---|
| No auth/authz layer | Total data breach; blocks launch | High | P0 |
| Plaintext/base64 secrets | Credential theft, provider bill theft | Medium | P0 |
| In-memory session store | Cannot scale; data loss | Medium | P0 |
| Abandoned Alembic + startup UPDATEs | Schema drift; no rollback | Medium | P1 |
| Sequential non-streaming voice loop | Misses latency target; can't scale | High | P1 |
| Three agent implementations (2 dead) | Wrong/mock worker deployed by mistake | Low (delete) | P1 |
| Mock frontend store + fixtures-as-fallback | Admin actions don't persist; data masking | Medium | P1 |
| No CI/tests | Regressions ship silently | Low | P1 |
| FK/cascade contradictions | Delete failures, orphans | Medium | P2 |
| No observability | Blind in production | Medium | P2 |
| No booking idempotency / wrong times | Duplicate & mis-scheduled appointments | Medium | P1 |
| No code splitting (819KB bundle) | Slow first load | Low | P2 |
| TS `strict:false`, no typecheck gate | Latent type bugs | Medium | P2 |

---

## 15. Production Gap Analysis

| Area | Current State | Production Requirement | Gap | Recommended Fix | Priority | Effort |
|---|---|---|---|---|---|---|
| Auth | None; password not checked | JWT/session + RBAC, hashed pw | Total | Auth layer + argon2 + token-derived tenant | Critical | L (2–3wk) |
| Tenant isolation | Client-supplied `tenant_id` | Server-derived, ownership checks | Total | Scope all queries to token tenant | Critical | M |
| Secrets | Plaintext/base64/on-disk | KMS-encrypted, rotated, in secret mgr | Total | Fernet/KMS + rotate all | Critical | M |
| Sessions | In-memory dict | Redis, shared | Total | Implement Redis behind existing interface | Critical | M |
| Migrations | `create_all` + boot UPDATEs | Alembic `upgrade head` on deploy | Large | Real baseline; move data-migs to revisions | High | M |
| Voice latency | Sequential, blocking | Streaming STT/LLM/TTS, non-blocking | Large | Wire existing streaming infra; async ffmpeg | High | M–L |
| Booking | Wrong time, dup, fire-and-forget | Correct slot, idempotent, awaited | Large | Parse slot; unique key; retained tasks | High | M |
| Reliability | LLM error → silence | Spoken fallback + breakers | Medium | Fallback utterance + circuit breaker | High | M |
| CI/CD | Health cron only | Test/build gate + rollback | Medium | GitHub Actions + tagged releases | High | S |
| Observability | None wired | Sentry + metrics + alerts | Large | Init Sentry; OTel; uptime alerts | High | M |
| Uploads | Ephemeral disk | Object storage | Medium | S3/Supabase Storage | Medium | S |
| Frontend | Fake auth, monolith bundle | Real auth UX, code-split, error boundary | Medium | lazy() + boundary + real login | Medium | M |
| Rate limiting | Embed only, bypassable | Global Redis limiter + budgets | Medium | slowapi + per-tenant caps | Medium | S |

---

## 16. Implementation Roadmap

### Phase 1 — Critical Blockers *(must complete before any real clinic/patient data)*
1. **Auth & tenant isolation.** *Objective:* nobody accesses data without a valid token; tenant derived server-side. *Steps:* hash passwords (argon2id) + real `clinic_login` verification → issue JWT (or httpOnly session) → global FastAPI auth dependency + role guard on `/admin/*` → resolve `tenant_id` from token, scope every query → make frontend guards UX-only, remove hardcoded creds & fail-open. *Success:* every non-embed/health route returns 401 without a valid token; cross-tenant access returns 404; pen-test of IDOR passes. *Effort:* 2–3 wk. *Risk:* touches 82 raw `fetch()` sites + all routers.
2. **Rotate & encrypt all secrets.** Rotate every key in `push_render_env.ps1` + LiveKit secret; delete the script; generate real `SECRET_KEY`; encrypt provider keys with Fernet/KMS; stop returning/logging cleartext; remove/guard `/admin/reset-db|seed|sync`, `/debug/audit`. *Success:* no plaintext secret in DB/repo/logs; app refuses to boot on default secret in prod.
3. **Redis session store.** Swap `redis_client.py` dict for `redis.asyncio` behind the same interface. *Success:* sessions survive restart; >1 instance serves one caller correctly.
4. **Fix hard bugs:** `create_tenant` crash; booking stores requested slot (not `utcnow()`); non-atomic credit deduction → `UPDATE … balance = balance - :cost` + pre-call balance gate.
5. **Remove dead/dangerous agents** (`agent.py`, `agent/gemini.py`, `agent/sarvam.py`, `agent/providers/`).

### Phase 2 — Stability & Reliability
- Real Alembic baseline; move startup UPDATEs into versioned revisions; `alembic upgrade head` on deploy; remove `create_all` (or gate to SQLite dev).
- Fix FK/cascade contradictions; enable SQLite FK pragma or drop SQLite in prod; add FKs to analytics.
- Booking idempotency (unique key + upsert) + retained/awaited tasks + await-before-confirm.
- Spoken LLM/TTS fallback on every provider error; circuit breakers; multi-provider fallback both directions.
- Frontend error boundary; route superadmin mutations through the backend; retire mock store + fixture fallbacks.
- CI pipeline (tests + typecheck + build gate); tagged releases + rollback runbook.

### Phase 3 — Performance / Low-Latency
- Wire the live turn through existing streaming STT + streaming TTS; stream LLM tokens → sentence-chunked TTS.
- `asyncio.create_subprocess_exec` for ffmpeg (unblock loop); shared keep-alive HTTP client; per-session KB/key/prompt cache.
- Bounded async connection pool to Supabase session pooler; real `prewarm`.
- Frontend: `lazy()` all routes + LiveKit split; `manualChunks`; TS `strict` + typecheck; fix STT WS origin (`WS_URL`, not `window.location.host`) and callback churn.

### Phase 4 — Scalability
- Make handlers stateless; drop `--workers 1`; enable replica autoscaling.
- Offload transcode/post-call analysis to a queue/worker; object storage for uploads; CDN for widget/static.
- Global Redis rate limiting + per-tenant daily budgets; per-agent embed budgets; outbound-call allowlist + spend caps.

### Phase 5 — Monitoring & Operations
- Init Sentry; OpenTelemetry traces (per-stage STT/LLM/TTS latency); ship logs off-box; metrics dashboards.
- Uptime/error/latency/credit alerts; define & track SLOs.
- Scheduled `pg_dump` to object storage + verify Supabase PITR + restore runbook.
- Dependency lockfiles + `pip-audit`/`npm audit`/Dependabot + image scanning; non-root containers, multi-stage images, digest pinning.

---

## 17. Real-Time Optimization Strategy

**Goal: p95 < 3s, sub-second first audio, hundreds of concurrent calls per box.**

1. **First-audio-fast turn:** partial STT → stream LLM tokens → emit TTS per completed sentence. Target first audio <800ms after speech end.
2. **Never block the loop:** async subprocess/executor for ffmpeg; offload CPU work; shared HTTP client with keep-alive.
3. **Stateless + Redis:** move all call state to Redis so any worker/replica can handle any turn; enables horizontal scale.
4. **Bounded pooling:** async QueuePool to the Supabase session pooler; cache per-session KB/key/prompt to cut per-turn DB round trips.
5. **Barge-in done right:** stream mic through VAD instead of hard-suppressing while speaking; honor interrupts within ~200ms.
6. **Resilience without latency cost:** circuit breakers skip known-down providers instantly; pre-synthesized fallback utterances so failures never produce silence.
7. **Edge/CDN:** serve `widget.js`/static from CDN; keep the backend off free-tier to eliminate cold starts.

---

## 18. Final Go-Live Checklist

**Blockers (all must be ✅):**
- [ ] Real authentication on every non-public route; tenant derived from token
- [ ] Passwords hashed (argon2/bcrypt); no cleartext stored or returned
- [ ] All deployed secrets rotated; provider keys encrypted at rest; `push_render_env.ps1` deleted
- [ ] Strong mandatory `SECRET_KEY`; boot fails on default in prod
- [ ] `/admin/reset-db|seed|sync`, `/debug/audit` removed or admin-gated
- [ ] Redis session store live; server runs ≥2 instances correctly
- [ ] `create_tenant` fixed; booking stores requested slot; credit deduction atomic + pre-call gate
- [ ] `DATABASE_URL` required (no silent SQLite fallback); Postgres FKs enforced
- [ ] Frontend guards are UX-only; hardcoded creds & fail-open removed
- [ ] Dead agent code removed; deployed voice agent verified to join rooms & speak

**Strongly recommended before scale:**
- [ ] Alembic is the schema authority; `upgrade head` on deploy
- [ ] Streaming STT/LLM/TTS wired; ffmpeg non-blocking; shared HTTP client
- [ ] Booking idempotency; spoken fallbacks; circuit breakers
- [ ] CI test/build gate; tagged releases + rollback runbook
- [ ] Sentry + metrics + alerts + SLOs
- [ ] DB backups + restore runbook; uploads on object storage
- [ ] Rate limiting + per-tenant budgets; SSRF allowlist on webhooks
- [ ] Code splitting + error boundary; TS strict; dep lockfiles + audit

---

## 19. Risk Register

| ID | Risk | Likelihood | Impact | Exposure | Mitigation |
|---|---|:--:|:--:|:--:|---|
| RR1 | Unauthenticated data breach (all clinics/PII) | Certain | Catastrophic | 🔴 | Phase 1.1 auth |
| RR2 | Compromised keys → provider bill theft / call fraud | High | High | 🔴 | Rotate + encrypt; budgets/allowlist |
| RR3 | DB wipe via `/admin/reset-db` + default secret | Medium | Catastrophic | 🔴 | Remove endpoint; strong secret |
| RR4 | Data loss on redeploy (SQLite fallback, uploads, sessions) | Medium | High | 🟠 | Require Postgres; object storage; Redis |
| RR5 | Missed latency target → poor UX / churn | High | Medium | 🟠 | Phase 3 streaming |
| RR6 | Duplicate/mis-scheduled appointments | High | High | 🟠 | Idempotency + correct slot |
| RR7 | Silent call drops on provider failure | Medium | High | 🟠 | Fallbacks + breakers |
| RR8 | Cannot scale past one box | Certain (at load) | High | 🟠 | Redis + stateless + replicas |
| RR9 | Blind to production incidents | Certain | Medium | 🟠 | Sentry + alerts |
| RR10 | Regression shipped without tests | High | Medium | 🟡 | CI gate |
| RR11 | SSRF → cloud metadata/credential theft | Medium | High | 🟡 | Webhook allowlist, no redirects |

---

## 20. Recommended Production Architecture

```
                    ┌──────────── CDN (widget.js, SPA static) ─────────────┐
                    ▼                                                        ▼
   Patient phone ─SIP─▶ LiveKit Cloud ─▶ Voice Agent workers (autoscaled)   Admin/clinic browser
   Clinic site  ─────▶ Embed widget ─┐        │  (Pipecat, streaming)        │ React SPA (JWT in httpOnly cookie)
                                     ▼        ▼                              ▼
                        ┌────────────────────────────────────────────────────────┐
                        │  API Gateway / LB  (TLS, WAF, global rate limit)          │
                        └───────────────┬────────────────────────────────┬─────────┘
                                        │  JWT auth + tenant claim         │
                        ┌───────────────▼───────────────┐   ┌─────────────▼───────────┐
                        │ FastAPI (stateless, N replicas)│   │ Async workers / queue    │
                        │  auth · tenant-scoped queries  │   │ transcode · post-call    │
                        └───┬───────────┬───────────┬────┘   │ analysis · webhooks      │
                            │           │           │        └──────────┬───────────────┘
              ┌─────────────▼┐   ┌──────▼──────┐  ┌─▼──────────────┐    │
              │ Redis        │   │ Postgres    │  │ Object storage │    │
              │ sessions +   │   │ (pooled,    │  │ (uploads,      │    │
              │ rate limits  │   │  Alembic,   │  │  recordings)   │    │
              │ + breakers   │   │  encrypted) │  └────────────────┘    │
              └──────────────┘   └─────────────┘                        │
                            ┌───────────────────────────────────────────▼─┐
                            │ Observability: Sentry · OTel traces · metrics │
                            │ dashboards · alerts · SLOs · secrets manager  │
                            └───────────────────────────────────────────────┘
```

**Principles:** stateless API behind a gateway that enforces auth + rate limits; Redis for all shared state; Postgres as the only durable store (Alembic-managed, encrypted secrets); object storage for blobs; a dedicated autoscaled voice-agent tier with proper LiveKit dispatch; async workers for anything slow; full observability + a secrets manager.

---

*Prepared as a static audit against the codebase at commit `f4a0789`. File/line references are to that revision. This document does not change product scope — it enumerates the work to make the existing product production-grade.*
