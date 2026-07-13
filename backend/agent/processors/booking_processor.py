"""
backend/agent/processors/booking_processor.py

Booking state machine as a Pipecat FrameProcessor.

Intercepts TranscriptionFrame events from the STT service and:
  1. Detects doctor/specialization mentions → sets pending booking
  2. Detects slot time mentions → updates pending slot
  3. Detects patient name → stores for appointment record
  4. Detects confirmation keywords → fires _commit_booking() as background task
  5. Detects cancellation keywords → resets booking state

This processor is transparent — it passes every frame downstream unchanged.
It only reads TranscriptionFrames and triggers side-effects.

No added latency to the voice pipeline (all DB writes are fire-and-forget tasks).
"""

import asyncio
import logging
import re
from typing import Optional

from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

# ── Keyword sets (lowercase, stripped) ────────────────────────────────────────
_CONFIRM_WORDS: frozenset[str] = frozenset({
    "yes", "haan", "ha", "okay", "ok", "theek", "theek hai", "book it",
    "confirm", "book karo", "book kar do", "book karein", "done", "sahi hai",
    "bilkul", "zaroor", "schedule it", "go ahead",
})

_CANCEL_WORDS: frozenset[str] = frozenset({
    "cancel", "nahi", "no", "nope", "mat karo", "band karo",
})

_EMERGENCY_WORDS: frozenset[str] = frozenset({
    "emergency", "heart attack", "accident", "unconscious", "bleeding",
    "bahut dard", "chest pain", "can't breathe", "can not breathe",
    "stroke", "ambulance", "108",
})

# Matches times like "11 AM", "3:30 pm", "11 baje", "gyarah baje"
_SLOT_PATTERN = re.compile(
    r'\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|bajey)?)\b',
    re.IGNORECASE,
)

# Triggers for extracting patient name from transcription
_NAME_TRIGGERS: tuple[str, ...] = (
    "my name is", "i am", "main hoon", "naam hai", "mera naam", "naam",
)


class BookingProcessor(FrameProcessor):
    """
    Transparent FrameProcessor that drives the appointment booking state machine.

    Constructor args:
        tenant (dict): Tenant record with 'id', 'clinic_name', 'doctors' list.
        agent_config (dict): Agent config with language, voice settings etc.
        call_meta (dict): Call metadata — caller_phone, call_record_id, etc.
    """

    def __init__(
        self,
        tenant: dict,
        agent_config: dict,
        call_meta: dict,
    ) -> None:
        super().__init__()

        self._tenant = tenant
        self._agent_config = agent_config
        self._call_meta = call_meta

        # ── Booking state ─────────────────────────────────────────────────────
        self.booking_state: dict = {
            "pending_doctor_id":   None,   # UUID string of matched doctor
            "pending_doctor_name": None,   # Human-readable name
            "pending_slot":        None,   # Offered slot time string
            "awaiting_confirm":    False,  # True after slot offered, awaiting YES/NO
            "patient_phone":       call_meta.get("caller_phone", "unknown"),
            "patient_name":        None,   # Extracted from conversation
            "confirmed":           False,  # True once booking committed to DB
            "emergency_detected":  False,  # True on emergency keyword
        }

        logger.info(
            "BookingProcessor initialised | tenant=%s caller=%s",
            tenant.get("id"), self.booking_state["patient_phone"],
        )

    # ── FrameProcessor interface ──────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Pass every frame through; inspect TranscriptionFrames for state triggers."""
        if isinstance(frame, TranscriptionFrame) and frame.text:
            await self._handle_transcription(frame.text)

        # Always push the frame downstream — never block the voice pipeline
        await self.push_frame(frame, direction)

    # ── Internal state machine ────────────────────────────────────────────────

    async def _handle_transcription(self, text: str) -> None:
        """Apply all booking state machine rules to a completed user utterance."""
        text_lower = text.lower().strip()

        # 0. Emergency detection — highest priority
        if any(w in text_lower for w in _EMERGENCY_WORDS):
            if not self.booking_state["emergency_detected"]:
                self.booking_state["emergency_detected"] = True
                logger.warning(
                    "EMERGENCY keyword detected in utterance: '%s'", text[:80]
                )
                await self._handle_emergency()
            return  # Don't process booking after emergency

        # Already confirmed — nothing more to do
        if self.booking_state["confirmed"]:
            return

        # Appointment booking (including the doctor-match step that starts the
        # flow) is gated on can_book_appointments — if the clinic admin turned
        # this tool off, the agent must not start collecting booking details.
        if not self._agent_config.get("can_book_appointments", True):
            return

        # 1. Extract patient name from utterance
        self._try_extract_name(text, text_lower)

        # 2. Detect doctor / specialization mention (only when not yet awaiting confirm)
        if not self.booking_state["awaiting_confirm"]:
            self._try_match_doctor(text_lower)

        # 3. Detect explicit slot time from utterance
        if self.booking_state["awaiting_confirm"]:
            self._try_extract_slot(text)

        # 4. Detect cancellation
        if self.booking_state["awaiting_confirm"]:
            if any(w in text_lower for w in _CANCEL_WORDS):
                logger.info("Patient cancelled pending booking. Resetting state.")
                self.booking_state["awaiting_confirm"] = False
                self.booking_state["pending_doctor_id"] = None
                self.booking_state["pending_slot"] = None
                return

        # 5. Detect confirmation → commit appointment
        if self.booking_state["awaiting_confirm"]:
            if any(w in text_lower for w in _CONFIRM_WORDS):
                await self._try_commit_booking()

    async def _handle_emergency(self) -> None:
        """
        Speak an emergency message as soon as an emergency keyword is detected,
        gated on can_transfer_emergency.

        NOTE — scope: this pushes a TextFrame straight to TTS (the same
        mechanism the first-message greeting uses via task.queue_frames, so it
        is known to flow through the LLM stage untouched). It does NOT perform
        an actual SIP/telephony call transfer — no such capability exists
        anywhere in this codebase yet (no LiveKit SIP transfer call, no
        Exotel/Twilio integration). A real transfer would need that telephony
        integration built first; this only ensures the caller is told to call
        emergency services / the clinic's emergency number without waiting for
        the LLM to finish its current turn.
        """
        if not self._agent_config.get("can_transfer_emergency", True):
            logger.info("Emergency keyword detected but can_transfer_emergency is off — no action taken.")
            return

        number = self._agent_config.get("emergency_transfer_number")
        if number:
            message = (
                f"This sounds like a medical emergency. Please call {number} "
                "or go to your nearest emergency room right away."
            )
        else:
            message = (
                "This sounds like a medical emergency. Please call your local "
                "emergency number or go to your nearest emergency room right away."
            )
        await self.push_frame(TextFrame(message), FrameDirection.DOWNSTREAM)
        logger.warning("Emergency message queued for TTS: '%s'", message)

    def _try_extract_name(self, text: str, text_lower: str) -> None:
        """Extract patient name when name-trigger phrases are detected."""
        if self.booking_state["patient_name"]:
            return  # Already captured

        for trigger in _NAME_TRIGGERS:
            if trigger in text_lower:
                idx = text_lower.find(trigger) + len(trigger)
                remainder = text[idx:].strip()
                if remainder:
                    raw_name = remainder.split()[0]
                    # Strip punctuation from name
                    clean_name = re.sub(r"[^\w]", "", raw_name).capitalize()
                    if clean_name:
                        self.booking_state["patient_name"] = clean_name
                        logger.info("Patient name captured: '%s'", clean_name)
                break

    def _try_match_doctor(self, text_lower: str) -> None:
        """Scan utterance for doctor name or specialization keywords."""
        doctors: list[dict] = self._tenant.get("doctors", [])
        for doc in doctors:
            spec = (doc.get("specialization") or "").lower()
            name = (doc.get("name") or "").lower()

            # Match by specialization words or doctor name words
            spec_words = [w for w in spec.split() if len(w) > 2]
            name_words = [w for w in name.split() if len(w) > 2]

            if spec and (spec in text_lower or any(w in text_lower for w in spec_words)):
                self._set_pending_doctor(doc)
                break
            if name and any(w in text_lower for w in name_words):
                self._set_pending_doctor(doc)
                break

    def _set_pending_doctor(self, doc: dict) -> None:
        """Record a matched doctor and mark booking as awaiting confirmation."""
        self.booking_state["pending_doctor_id"]   = doc.get("id")
        self.booking_state["pending_doctor_name"] = doc.get("name")

        if not self.check_availability_allowed():
            # Doctor identified, but the clinic disabled availability lookups —
            # don't offer a slot or move to confirmation.
            logger.info(
                "Booking: matched doctor '%s' but can_check_availability is off — not offering a slot.",
                doc.get("name"),
            )
            return

        # Default slot — real implementation would call his.get_slots()
        self.booking_state["pending_slot"]        = "11:00 AM"
        self.booking_state["awaiting_confirm"]    = True
        logger.info(
            "Booking: matched doctor '%s' (id=%s) — awaiting patient confirm.",
            doc.get("name"), doc.get("id"),
        )

    def _try_extract_slot(self, text: str) -> None:
        """Extract a time slot from the utterance and update pending_slot."""
        match = _SLOT_PATTERN.search(text)
        if match:
            slot = match.group(0).strip()
            self.booking_state["pending_slot"] = slot
            logger.info("Slot updated from utterance: '%s'", slot)

    async def _try_commit_booking(self) -> None:
        """Fire appointment DB write as a background task (never blocks voice)."""
        tenant_id = self._tenant.get("id")
        doctor_id = self.booking_state.get("pending_doctor_id")
        slot_time = self.booking_state.get("pending_slot", "TBD")
        patient_phone = self.booking_state.get("patient_phone", "unknown")

        if not tenant_id or not doctor_id:
            logger.warning(
                "Confirmation detected but missing tenant_id=%s or doctor_id=%s — skipping.",
                tenant_id, doctor_id,
            )
            return

        logger.info(
            "Booking confirmed! tenant=%s doctor_id=%s slot=%s phone=%s",
            tenant_id, doctor_id, slot_time, patient_phone,
        )

        # Mark state BEFORE firing task — prevents double-fire on repeated "yes"
        self.booking_state["confirmed"]       = True
        self.booking_state["awaiting_confirm"] = False

        asyncio.create_task(
            _commit_booking_to_db(
                tenant_id=str(tenant_id),
                doctor_id=str(doctor_id),
                slot_time=slot_time,
                patient_phone=patient_phone,
                call_record_id=self._call_meta.get("call_record_id"),
            )
        )

    def check_availability_allowed(self) -> bool:
        """Gate for the 'Check Availability' tool toggle — offering a slot to
        confirm is the live pipeline's only availability-check equivalent
        (see _set_pending_doctor; real per-doctor scheduling data is not
        wired here yet, matching the mocked his.get_slots())."""
        return self._agent_config.get("can_check_availability", True)


# ── Standalone DB commit function (called as background task) ─────────────────

async def _commit_booking_to_db(
    tenant_id: str,
    doctor_id: str,
    slot_time: str,
    patient_phone: str,
    call_record_id: Optional[str] = None,
) -> None:
    """
    Write appointment to PostgreSQL and fire Google Sheets webhook.

    Runs as asyncio.create_task() — never blocks the voice call.
    All errors are logged and swallowed; the call continues regardless.
    """
    try:
        from backend.services.his import create_appointment  # Lazy import — avoids circular deps

        result = await create_appointment(
            tenant_id=tenant_id,
            doctor_id=doctor_id,
            slot_time=slot_time,
            patient_phone=patient_phone,
            call_id=call_record_id,
        )
        logger.info(
            "[BookingProcessor] Appointment saved: id=%s doctor=%s slot=%s",
            result.get("appointment_id"),
            result.get("doctor_name"),
            slot_time,
        )
    except Exception as exc:
        logger.error(
            "[BookingProcessor] Failed to save appointment: %s",
            exc,
            exc_info=True,
        )
