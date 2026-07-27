# Lifodial - AI Voice Receptionist for Clinics

Lifodial is a production-grade, multi-tenant AI voice receptionist platform built specifically for healthcare — clinics, hospitals, and multi-location practices. It answers patient calls (and web/embed calls) in real time, understands intent, books and manages appointments directly against the clinic's own scheduling system, and hands off gracefully whenever a human is genuinely needed.

## 💡 Why Lifodial

Most voice-AI agent platforms are generic — a chatbot with a phone number bolted on, built for any industry and none in particular. Lifodial is purpose-built for healthcare from the ground up:

- **Native multilingual care, not translated English** — first-class support for Hindi and 20+ Indian languages (plus English/Arabic for Middle East clinics), with automatic language detection and seamless mid-call switching — not a bolt-on translation layer.
- **Real scheduling, not a demo booking flow** — appointments are created directly against the clinic's actual doctor/slot data and show up instantly in the clinic's own dashboard, so front-desk staff see exactly what the AI booked, in real time.
- **Bring your own AI stack** — Lifodial isn't locked into one speech or language model vendor. Clinics (or their technical team) can plug in their own API keys for the speech-to-text, text-to-speech, or LLM provider of their choice, with automatic fallback if a provider has an outage — no rebuild, no redeploy, no vendor lock-in.
- **Sub-second, natural conversation** — a real-time WebRTC voice pipeline (not turn-based request/response) with barge-in support, so patients can interrupt the AI naturally mid-sentence the way they would a human receptionist.
- **Deploy anywhere the clinic already is** — phone number forwarding, or a single embeddable widget snippet for the clinic's own website — live in minutes, not weeks.
- **Built for operators, not just developers** — a full super-admin + per-clinic dashboard for call logs, transcripts, appointment history, credit/usage billing, and live health monitoring — the operational tooling a real healthcare business needs, included by default.
- **Production-hardened from day one** — structured error monitoring, automatic provider failover, encrypted credential storage, and tenant-isolated data by design (never demo-quality software wearing a production label).

## 🚀 Tech Stack

- **Frontend:** React, TypeScript, Vite, TailwindCSS (Vanilla CSS for custom components), Lucide Icons
- **Backend:** FastAPI (Python), SQLAlchemy, asyncpg
- **Database:** PostgreSQL (via Supabase), Redis (for session management)
- **Voice / Telephony:** LiveKit (WebRTC), Exotel/Twilio (SIP integration)
- **AI Models:** 
  - **LLM:** Google Gemini 2.0 Flash
  - **Speech-to-Text (STT) & Text-to-Speech (TTS):** Sarvam AI (High-accuracy native Indian languages & English), Deepgram (Fallback)

---

## 🛠️ Local Development Setup

To run Lifodial locally for development, you will need to start both the Python Backend server and the React Frontend server.

### 1. Backend Setup

The backend handles the API, database connectivity, and the core AI agent logic.

1. **Navigate to the backend directory:**
   ```bash
   cd backend
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables:**
   Ensure you have a `.env` file in the `backend` folder containing your `DATABASE_URL` (SQLite will be used if left blank), `GEMINI_API_KEY`, `SARVAM_API_KEY`, and `LIVEKIT` keys.

5. **Start the FastAPI server:**
   ```bash
   uvicorn main:app --reload --port 8001
   ```
   The API will now be running on `http://localhost:8001`.

### 2. Frontend Setup

The frontend provides the Administration dashboards (Clinic view and SuperAdmin view).

1. **Navigate to the frontend directory:**
   ```bash
   cd frontend
   ```

2. **Install Node.js dependencies:**
   ```bash
   npm install
   ```

3. **Start the Vite development server:**
   ```bash
   npm run dev
   ```
   The UI will now be running on `http://localhost:5173`. 
   * Local API requests are proxied directly to the backend on `localhost:8001`.

---

## 🔑 Default Dashboards & Logins

- **Clinic Dashboard:** `http://localhost:5173/login` (Use any credentials in demo mode)
- **SuperAdmin Dashboard:** `http://localhost:5173/superadmin/login` (credentials come from the `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` env vars — see `.env.example`)

## 🧪 Full Local Test Flow

1. **Super Admin Access**:
   - URL: `http://localhost:5173/superadmin/login`
   - Log in with your `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` from `.env`
2. **Create Test Clinic**:
   - Go to **"All Clinics"**.
   - Click **"Add Clinic"** → Fill form → Submit.
   - **COPY THE GENERATED PASSWORD** (only shown once).
3. **Login as Clinic**:
   - Logout from Super Admin.
   - Login at `http://localhost:5173/login` using the generated email and password.
4. **Voice Test (Needs LiveKit Keys)**:
   - Add `LIVEKIT_*` keys to `.env`.
   - Run: `python backend/tests/create_test_room.py`

## 🚢 Deployment

- A comprehensive VPS container deployment guide is available in `scripts/setup_vps.sh` and `docs/DEPLOYMENT.md`.
- **Vercel (frontend only):**
  ```bash
  cd frontend
  npm run build
  vercel --prod
  ```

## 📚 More Docs

- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — full deployment guide (local, Docker Compose, VPS)
- [`docs/PRODUCTION_READINESS_AUDIT.md`](docs/PRODUCTION_READINESS_AUDIT.md) — production readiness audit findings
- [`docs/STREAMING_STT_README.md`](docs/STREAMING_STT_README.md) — streaming STT architecture
- [`docs/STREAMING_STT_TEST.md`](docs/STREAMING_STT_TEST.md) — streaming STT manual test guide
