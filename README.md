# Lifodial - AI Voice Receptionist for Clinics

Lifodial is a production-grade AI voice receptionist platform designed for healthcare, clinics, and hospitals. It features ultra-low latency voice-to-voice capabilities, a robust Master Agent architecture with barge-in interruption handling, AI-driven post-call evaluations, and a beautiful React frontend.

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
   uvicorn main:app --reload --port 8000
   ```
   The API will now be running on `http://localhost:8000`.

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
   * Local API requests are proxied directly to the backend on `localhost:8000`.

---

## 🔑 Default Dashboards & Logins

- **Clinic Dashboard:** `http://localhost:5173/login` (Use any credentials in demo mode)
- **SuperAdmin Dashboard:** `http://localhost:5173/superadmin/login` (credentials come from the `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` env vars — see `.env.example`)

## 🚢 Deployment Updates
*(Note: A comprehensive VPS container deployment guide is available in `scripts/setup_vps.sh` for when the application moves to production).*
