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

import logging
import re
from typing import Optional

from pipecat.frames.frames import Frame, LLMContextFrame, TextFrame, TranscriptionFrame
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

# Day words that qualify a requested time ("tomorrow 3 pm", "kal 11 baje")
_DAY_PATTERN = re.compile(
    r'\b(today|tomorrow|tonight|aaj|kal|parso|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
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
            "pending_slot":        None,   # Slot the CALLER asked for (never fabricated)
            "awaiting_confirm":    False,  # True once doctor + caller-given slot exist
            "patient_phone":       call_meta.get("caller_phone", "unknown"),
            "patient_name":        None,   # Extracted from conversation
            "confirmed":           False,  # True once booking committed to DB
            "emergency_detected":  False,  # True on emergency keyword
        }

        # Set when a confirm keyword is heard; consumed on the next
        # LLMContextFrame, where the DB write is AWAITED and its real result is
        # injected into the LLM context BEFORE generation (audit FIX 4 — the
        # agent must never say "booked" unless the row actually exists).
        self._commit_pending: bool = False

        logger.info(
            "BookingProcessor initialised | tenant=%s caller=%s",
            tenant.get("id"), self.booking_state["patient_phone"],
        )

    # ── FrameProcessor interface ──────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Pass every frame through; inspect TranscriptionFrames for state triggers.

        LLMContextFrame is the frame that triggers LLM generation downstream —
        when a booking commit is pending, we HOLD it here, await the DB write,
        and inject the real result into the context first. This is the
        mechanism that makes "booked" impossible to speak before the row
        exists. Only the confirmation turn pays this DB round-trip; every
        other frame passes straight through (no hot-path latency added).
        """
        # REQUIRED first: lets the base FrameProcessor handle system frames
        # (StartFrame/CancelFrame/…) and mark itself started. Without it,
        # pipecat 1.5 floods "Trying to process X but StartFrame not received"
        # and blocks CancelFrame from reaching the pipeline end at teardown.
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text:
            await self._handle_transcription(frame.text)

        if isinstance(frame, LLMContextFrame) and self._commit_pending:
            await self._commit_and_inject_result(frame)

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

        # 3. Extract the slot the CALLER asks for. Runs whenever a doctor is
        #    pending — before confirm (caller states a time) or during confirm
        #    (caller changes the time). There is NO fabricated default slot
        #    (audit FIX 4: the old code offered a hardcoded "11:00 AM").
        if self.booking_state["pending_doctor_id"]:
            self._try_extract_slot(text)
            # Doctor + a caller-given time = ready to ask for a yes/no.
            if (
                self.booking_state["pending_slot"]
                and not self.booking_state["awaiting_confirm"]
                and self.check_availability_allowed()
            ):
                self.booking_state["awaiting_confirm"] = True
                logger.info(
                    "Booking: doctor '%s' + caller-requested slot '%s' — awaiting confirm.",
                    self.booking_state["pending_doctor_name"],
                    self.booking_state["pending_slot"],
                )

        # 4. Detect cancellation
        if self.booking_state["awaiting_confirm"]:
            if any(w in text_lower for w in _CANCEL_WORDS):
                logger.info("Patient cancelled pending booking. Resetting state.")
                self.booking_state["awaiting_confirm"] = False
                self.booking_state["pending_doctor_id"] = None
                self.booking_state["pending_slot"] = None
                return

        # 5. Detect confirmation → mark commit pending. The actual DB write is
        #    awaited on the next LLMContextFrame (see process_frame) so its
        #    real result reaches the LLM before it can speak a confirmation.
        if self.booking_state["awaiting_confirm"] and self.booking_state["pending_slot"]:
            if any(w in text_lower for w in _CONFIRM_WORDS):
                self._commit_pending = True
                logger.info("Booking confirm keyword heard — commit will be awaited before LLM reply.")

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
        """Record a matched doctor. Confirmation is NOT armed here — it requires
        a slot the caller actually asked for (see _handle_transcription step 3).
        The old hardcoded '11:00 AM' default is gone (audit FIX 4)."""
        self.booking_state["pending_doctor_id"]   = doc.get("id")
        self.booking_state["pending_doctor_name"] = doc.get("name")
        logger.info(
            "Booking: matched doctor '%s' (id=%s) — waiting for the caller's requested time.",
            doc.get("name"), doc.get("id"),
        )

    def _try_extract_slot(self, text: str) -> None:
        """Extract the requested slot from the caller's own words. Captures an
        explicit clock time plus any nearby day word ("tomorrow 3 pm",
        "kal 11 baje") so the stored slot reflects what was actually asked."""
        match = _SLOT_PATTERN.search(text)
        if not match:
            return
        slot = match.group(0).strip()

        day_match = _DAY_PATTERN.search(text)
        if day_match:
            slot = f"{day_match.group(0).strip().capitalize()} {slot}"

        self.booking_state["pending_slot"] = slot
        logger.info("Slot captured from caller utterance: '%s'", slot)

    async def _commit_and_inject_result(self, frame: LLMContextFrame) -> None:
        """AWAIT the appointment DB write, then inject the REAL outcome into the
        LLM context carried by this frame — before the LLM generates.

        Success → the LLM is told the row exists (id + doctor + slot) and may
        confirm. Failure → the LLM is told to apologize and offer to retry, and
        booking state is re-armed so a fresh "yes" retries the commit (the
        idempotency key in his.create_appointment makes retries safe).
        """
        self._commit_pending = False

        tenant_id = self._tenant.get("id")
        doctor_id = self.booking_state.get("pending_doctor_id")
        slot_time = self.booking_state.get("pending_slot")
        patient_phone = self.booking_state.get("patient_phone", "unknown")

        if not tenant_id or not doctor_id or not slot_time:
            logger.warning(
                "Confirm heard but booking incomplete (tenant=%s doctor=%s slot=%s) — not committing.",
                tenant_id, doctor_id, slot_time,
            )
            return

        ok, result = await _commit_booking_to_db(
            tenant_id=str(tenant_id),
            doctor_id=str(doctor_id),
            slot_time=slot_time,
            patient_phone=patient_phone,
            call_record_id=self._call_meta.get("call_record_id"),
        )

        context = getattr(frame, "context", None)
        if ok:
            self.booking_state["confirmed"] = True
            self.booking_state["awaiting_confirm"] = False
            msg = (
                f"[BOOKING_RESULT success=true] The appointment IS saved in the system: "
                f"{result.get('doctor_name', self.booking_state['pending_doctor_name'])} at {slot_time} "
                f"(appointment id {result.get('appointment_id')}). "
                "Confirm this to the caller in one short sentence."
            )
        else:
            # Re-arm so another "yes" retries — idempotency key prevents dupes.
            self.booking_state["confirmed"] = False
            self.booking_state["awaiting_confirm"] = True
            msg = (
                "[BOOKING_RESULT success=false] The appointment could NOT be saved due to a system error. "
                "Do NOT say it is booked. Apologize briefly and ask if they'd like you to try again."
            )

        if context is not None:
            try:
                context.add_message({"role": "system", "content": msg})
            except Exception as exc:
                logger.error("Failed to inject booking result into LLM context: %s", exc)
        logger.info("Booking commit result injected: ok=%s slot=%s", ok, slot_time)

    def check_availability_allowed(self) -> bool:
        """Gate for the 'Check Availability' tool toggle — offering a slot to
        confirm is the live pipeline's only availability-check equivalent
        (see _set_pending_doctor; real per-doctor scheduling data is not
        wired here yet, matching the mocked his.get_slots())."""
        return self._agent_config.get("can_check_availability", True)


# ── Standalone DB commit function ─────────────────────────────────────────────

async def _commit_booking_to_db(
    tenant_id: str,
    doctor_id: str,
    slot_time: str,
    patient_phone: str,
    call_record_id: Optional[str] = None,
) -> tuple[bool, dict]:
    """
    Write appointment to PostgreSQL and return (ok, result).

    AWAITED by BookingProcessor before the LLM is allowed to speak a
    confirmation (audit FIX 4: "booked" must never be spoken on a failed or
    unconfirmed write). Idempotency lives in his.create_appointment — a
    repeated commit for the same call_id returns the existing row instead of
    creating a duplicate.
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
        if not result or not result.get("appointment_id"):
            logger.error("[BookingProcessor] create_appointment returned no appointment_id: %r", result)
            return False, {}
        logger.info(
            "[BookingProcessor] Appointment saved: id=%s doctor=%s slot=%s",
            result.get("appointment_id"),
            result.get("doctor_name"),
            slot_time,
        )
        # Record the booking on the call itself so platform analytics reflect
        # reality: Overview's resolution rate and the All Calls status read
        # call_records.outcome, which was never set on a successful booking → a
        # clinic with real bookings showed 0% resolution (audit P3).
        await _mark_call_booked(call_record_id)
        return True, result
    except Exception as exc:
        logger.error(
            "[BookingProcessor] Failed to save appointment: %s",
            exc,
            exc_info=True,
        )
        return False, {}


async def _mark_call_booked(call_record_id: Optional[str]) -> None:
    """Flag the call as having produced a booking (outcome='booked',
    booking_successful=True).

    Overview's resolution rate counts call_records whose outcome is
    booked/resolved, and the All Calls status column maps 'booked' → 'Booked'.
    Nothing wrote outcome on a successful booking before, so resolution always
    read 0% even when bookings existed (audit P3). Best-effort: a failure here
    never affects the caller's booking, which already succeeded.
    """
    if not call_record_id:
        return
    try:
        from sqlalchemy import update

        from backend.db import AsyncSessionLocal
        from backend.models.call_record import CallRecord

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(CallRecord)
                .where(CallRecord.id == call_record_id)
                .values(outcome="booked", booking_successful=True)
            )
            await db.commit()
        logger.info("[BookingProcessor] Marked call %s outcome=booked", call_record_id)
    except Exception as exc:
        logger.error("[BookingProcessor] Failed to mark call %s booked: %s", call_record_id, exc)
