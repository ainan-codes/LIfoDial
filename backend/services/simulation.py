"""
backend/services/simulation.py — Agent Simulation Testing
Runs predefined patient conversation scenarios through the agent's system prompt
using Gemini as the simulated patient, then scores conversation quality 0-100.
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime

from google import genai as google_genai

from backend.config import settings

logger = logging.getLogger(__name__)


# ── Predefined Scenarios ──────────────────────────────────────────────────────

SIMULATION_SCENARIOS: dict[str, dict] = {
    "hindi_booking": {
        "name": "Hindi Booking",
        "description": "Patient requests appointment in Hindi",
        "language": "hi-IN",
        "messages": [
            "Namaste",
            "Mujhe cardiologist se appointment chahiye",
            "Kal subah 11 baje chahiye",
            "Haan, confirm karo",
            "Theek hai, shukriya",
        ],
        "expected_outcome": "booked",
        "pass_criteria": {"goal_completion": 70, "overall_score": 70},
    },
    "english_booking": {
        "name": "English Booking",
        "description": "Patient requests appointment in English",
        "language": "en-IN",
        "messages": [
            "Hello",
            "I need to book with a dermatologist",
            "Tomorrow afternoon if possible",
            "Yes that works",
            "Thank you, goodbye",
        ],
        "expected_outcome": "booked",
        "pass_criteria": {"goal_completion": 70, "overall_score": 70},
    },
    "emergency_detection": {
        "name": "Emergency Detection",
        "description": "Verifies agent correctly escalates emergencies",
        "language": "en-IN",
        "messages": [
            "Hello",
            "My father is having chest pain and emergency",
            "Please help, he cannot breathe",
        ],
        "expected_outcome": "transferred",
        "pass_criteria": {"goal_completion": 80, "overall_score": 70},
    },
    "hindi_general_query": {
        "name": "General Query (Hindi)",
        "description": "Patient asks about clinic hours",
        "language": "hi-IN",
        "messages": [
            "Kya clinic Saturday ko khuli hai?",
            "Timing kya hai?",
            "Dhanyavaad",
        ],
        "expected_outcome": "resolved",
        "pass_criteria": {"goal_completion": 70, "overall_score": 65},
    },
    "unclear_speech": {
        "name": "Unclear Speech Handling",
        "description": "Tests graceful fallback when patient is unclear",
        "language": "en-IN",
        "messages": [
            "Um...",
            "I don't know what",
            "Never mind, wrong number",
        ],
        "expected_outcome": "transferred",
        "pass_criteria": {"goal_completion": 60, "overall_score": 60},
    },
    "cancellation": {
        "name": "Appointment Cancellation",
        "description": "Patient wants to cancel an existing appointment",
        "language": "en-IN",
        "messages": [
            "Hello, I want to cancel my appointment",
            "My appointment ID is APT-2847",
            "Yes cancel it",
            "Thank you",
        ],
        "expected_outcome": "resolved",
        "pass_criteria": {"goal_completion": 65, "overall_score": 65},
    },
    "arabic_booking": {
        "name": "Arabic Booking",
        "description": "Patient books appointment in Arabic",
        "language": "ar-SA",
        "messages": [
            "مرحباً",
            "أريد حجز موعد مع طبيب عام",
            "غداً في الصباح",
            "نعم، تأكيد الحجز",
        ],
        "expected_outcome": "booked",
        "pass_criteria": {"goal_completion": 70, "overall_score": 68},
    },
    "insurance_query": {
        "name": "Insurance Query",
        "description": "Patient asks about insurance acceptance",
        "language": "en-IN",
        "messages": [
            "Do you accept Star Health insurance?",
            "What about New India Assurance?",
            "Ok thank you",
        ],
        "expected_outcome": "resolved",
        "pass_criteria": {"goal_completion": 65, "overall_score": 65},
    },
}


# ── Core simulation runner ────────────────────────────────────────────────────

async def run_simulation(agent_id: str, scenario_name: str, db) -> dict:
    """
    Simulate a complete patient conversation against the agent's system prompt.
    Uses Gemini to generate agent responses, another call to score quality.
    Returns a scored result dict.
    """
    from backend.models.agent_config import AgentConfig
    from backend.models.tenant import Tenant
    from sqlalchemy import select

    scenario = SIMULATION_SCENARIOS.get(scenario_name)
    if not scenario:
        return {"error": f"Scenario '{scenario_name}' not found"}

    # Load agent from DB
    res = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = res.scalar_one_or_none()
    if not agent:
        return {"error": "Agent not found"}

    # Load tenant for context
    tenant_res = await db.execute(select(Tenant).where(Tenant.id == agent.tenant_id))
    tenant = tenant_res.scalar_one_or_none()
    clinic_name = tenant.clinic_name if tenant else "Clinic"

    system_prompt = agent.system_prompt or (
        f"You are {agent.agent_name}, AI receptionist for {clinic_name}. "
        "Answer patient queries concisely in 2 sentences max."
    )

    google_genai_client = google_genai.Client(api_key=settings.gemini_api_key)
    model_id = agent.llm_model or "gemini-2.0-flash"

    # We use the synchronous generate_content via run_in_executor to avoid
    # blocking the event loop for each patient turn.
    import functools

    async def _generate(prompt_or_contents) -> str:
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                functools.partial(
                    google_genai_client.models.generate_content,
                    model=model_id,
                    contents=prompt_or_contents,
                )
            )
            return resp.text.strip() if resp.text else ""
        except Exception as e:
            logger.warning(f"Gemini generate error in simulation: {e}")
            return "I apologize, could you please repeat that?"
    conversation: list[dict] = []
    booking_made = False
    emergency_detected = False
    start_time = time.time()

    # Warm up the chat with the first_message as the AI greeting
    first_msg = agent.first_message or f"Namaste! {clinic_name} mein aapka swagat hai."
    conversation.append({"role": "ai", "text": first_msg})

    # Build system-augmented prompt for each turn
    for patient_msg in scenario["messages"]:
        conversation.append({"role": "patient", "text": patient_msg})
        # Build simple turn prompt (include system prompt for context)
        turn_prompt = (
            f"System: {system_prompt}\n\n"
            + "\n".join(
                f"{m['role'].upper()}: {m['text']}" for m in conversation
            )
            + "\nAI:"
        )
        ai_text = await _generate(turn_prompt)
        if not ai_text:
            ai_text = "I apologize, could you please repeat that?"
        conversation.append({"role": "ai", "text": ai_text})

        # Detect booking/emergency keywords
        lower_ai = ai_text.lower()
        if any(kw in lower_ai for kw in ["apt-", "appointment", "confirmed", "booked", "booking"]):
            booking_made = True
        if any(kw in lower_ai for kw in ["emergency", "transfer", "connect", "112", "immediately"]):
            emergency_detected = True

        await asyncio.sleep(0.1)  # Rate limit

    elapsed_ms = int((time.time() - start_time) * 1000)

    # ── Score the conversation via a separate Gemini call ──
    transcript_text = "\n".join(
        f"{m['role'].upper()}: {m['text']}" for m in conversation
    )
    score_prompt = f"""
Score this AI clinic receptionist conversation on these dimensions (0-100 each).
Return ONLY valid JSON, no markdown fences.

{{
  "overall_score": 0-100,
  "goal_completion": 0-100,
  "naturalness": 0-100,
  "conciseness": 0-100,
  "language_accuracy": 0-100,
  "error_handling": 0-100,
  "issues": ["list of specific problems found"],
  "strengths": ["list of things done well"]
}}

Scoring guide:
- goal_completion: Did AI fulfill patient's core need?
- naturalness: Did it sound human, not robotic?
- conciseness: Max 2 sentences per response?
- language_accuracy: Responded in the patient's language?
- error_handling: Handled unclear inputs gracefully?

Expected outcome for this scenario: {scenario["expected_outcome"]}
Scenario: {scenario["name"]}

Conversation:
{transcript_text}
"""
    try:
        score_resp_text = await _generate(score_prompt)
        raw = score_resp_text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
        scores = json.loads(raw)
    except Exception as e:
        logger.warning(f"Scoring failed: {e}")
        scores = {
            "overall_score": 50,
            "goal_completion": 50,
            "naturalness": 50,
            "conciseness": 50,
            "language_accuracy": 50,
            "error_handling": 50,
            "issues": ["Scoring unavailable"],
            "strengths": [],
        }

    # Determine pass/fail against the scenario criteria
    criteria = scenario["pass_criteria"]
    passed = (
        scores.get("overall_score", 0) >= criteria["overall_score"]
        and scores.get("goal_completion", 0) >= criteria["goal_completion"]
    )

    return {
        "scenario": scenario_name,
        "scenario_name": scenario["name"],
        "scenario_description": scenario["description"],
        "agent_id": agent_id,
        "conversation": conversation,
        "booking_made": booking_made,
        "emergency_detected": emergency_detected,
        "turn_count": len(scenario["messages"]),
        "elapsed_ms": elapsed_ms,
        "scores": scores,
        "passed": passed,
        "expected_outcome": scenario["expected_outcome"],
        "ran_at": datetime.utcnow().isoformat(),
    }


async def run_all_simulations(agent_id: str, db) -> dict:
    """
    Run all predefined scenarios for an agent and return aggregate results.
    """
    results = []
    for scenario_name in SIMULATION_SCENARIOS:
        try:
            result = await run_simulation(agent_id, scenario_name, db)
            results.append(result)
        except Exception as e:
            logger.error(f"Simulation '{scenario_name}' failed: {e}")
            results.append({
                "scenario": scenario_name,
                "scenario_name": SIMULATION_SCENARIOS[scenario_name]["name"],
                "passed": False,
                "scores": {"overall_score": 0},
                "error": str(e),
            })
        await asyncio.sleep(0.5)  # Respect rate limits

    passed_count = sum(1 for r in results if r.get("passed", False))
    total = len(results)
    avg_score = (
        sum(r.get("scores", {}).get("overall_score", 0) for r in results) / total
        if total else 0
    )

    return {
        "agent_id": agent_id,
        "total_scenarios": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": f"{(passed_count / total * 100):.1f}%" if total else "0%",
        "avg_score": round(avg_score, 1),
        "results": results,
        "ready_for_production": passed_count >= int(total * 0.75),
        "ran_at": datetime.utcnow().isoformat(),
    }


def list_scenarios() -> list[dict]:
    """Return scenario metadata (no conversation data) for UI display."""
    return [
        {
            "key": k,
            "name": v["name"],
            "description": v["description"],
            "language": v["language"],
            "turn_count": len(v["messages"]),
            "expected_outcome": v["expected_outcome"],
        }
        for k, v in SIMULATION_SCENARIOS.items()
    ]
