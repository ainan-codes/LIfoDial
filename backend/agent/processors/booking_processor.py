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

from pipecat.frames.frames import Frame, LLMContextFrame, TranscriptionFrame, TTSSpeakFrame
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

# Intent phrases that start an EXISTING-appointment cancel/reschedule flow —
# distinct from _CANCEL_WORDS above, which only aborts a NEW booking that
# hasn't been confirmed yet.
_CANCEL_APPOINTMENT_PHRASES: frozenset[str] = frozenset({
    "cancel my appointment", "cancel the appointment", "cancel my booking",
    "cancel appointment", "i want to cancel", "i need to cancel",
    "i'd like to cancel", "appointment cancel karo", "booking cancel karo",
    "mera appointment cancel karna hai",
})

_RESCHEDULE_APPOINTMENT_PHRASES: frozenset[str] = frozenset({
    "reschedule my appointment", "reschedule the appointment", "reschedule my booking",
    "reschedule appointment", "change my appointment", "move my appointment",
    "postpone my appointment", "i want to reschedule", "i need to reschedule",
    "appointment reschedule karo", "appointment change karna hai",
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
            # ── Cancel/reschedule state (existing appointment, not a new one) ──
            "mode":                  None,   # None | "cancel" | "reschedule"
            "action_awaiting_confirm": False,  # True once the details needed are known
            "action_confirmed":      False,  # True once the action committed to DB
            "new_slot_day":          None,   # Reschedule only — caller-given new day
            "new_slot_time":         None,   # Reschedule only — caller-given new time
        }

        # Set when a confirm keyword is heard; consumed on the next
        # LLMContextFrame, where the DB write is AWAITED and its real result is
        # injected into the LLM context BEFORE generation (audit FIX 4 — the
        # agent must never say "booked" unless the row actually exists).
        self._commit_pending: bool = False

        # Same contract as _commit_pending above, but for an EXISTING
        # appointment's cancel/reschedule commit (separate flag so a NEW
        # booking in progress can never be confused with one).
        self._action_commit_pending: bool = False

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

        if isinstance(frame, LLMContextFrame) and self._action_commit_pending:
            await self._commit_and_inject_action_result(frame)

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

        # 0.5. Cancel/reschedule of an EXISTING appointment — an independent flow
        #      from the NEW-booking one below (a caller may cancel one appointment
        #      in the same call a NEW-booking attempt failed or succeeded in, so
        #      this must not be gated on booking_state["confirmed"]).
        if self._agent_config.get("can_cancel_appointments", True):
            await self._handle_cancel_reschedule(text, text_lower)
            if self.booking_state["mode"] is not None:
                # An existing-appointment flow is active — don't let the
                # NEW-booking keyword matching below (doctor/slot/confirm)
                # interpret the same utterance a second time.
                return

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

        NOTE — scope: this pushes a TTSSpeakFrame straight to TTS (the same
        mechanism the first-message greeting uses via task.queue_frames, so it
        is known to flow through the LLM stage untouched). A bare TextFrame does
        NOT work here — TTSService only synthesizes it as part of an LLM response
        turn, so the emergency message was silently dropped. It does NOT perform
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
        await self.push_frame(TTSSpeakFrame(message), FrameDirection.DOWNSTREAM)
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
        """Scan utterance for doctor name or specialization keywords.

        An on-leave doctor is never armed for booking here: the system prompt
        (backend/agent/pipeline.py::_doctor_availability_block) already tells
        the LLM who's unavailable, so the spoken "sorry, on leave" response
        comes from there — this just has to not silently start booking a
        doctor who isn't seeing patients. A specialization match still tries
        to fall through to another available doctor with the same
        specialization before giving up, matching the prompt's own
        instruction to offer an alternative.
        """
        doctors: list[dict] = self._tenant.get("doctors", [])
        spec_match_unavailable = False
        for doc in doctors:
            spec = (doc.get("specialization") or "").lower()
            name = (doc.get("name") or "").lower()
            available = doc.get("is_available", True)

            # Match by specialization words or doctor name words
            spec_words = [w for w in spec.split() if len(w) > 2]
            name_words = [w for w in name.split() if len(w) > 2]

            if spec and (spec in text_lower or any(w in text_lower for w in spec_words)):
                if available:
                    self._set_pending_doctor(doc)
                    return
                spec_match_unavailable = True
                continue
            if name and any(w in text_lower for w in name_words):
                if available:
                    self._set_pending_doctor(doc)
                return  # matched by name (available or not) — stop scanning either way

        if spec_match_unavailable:
            logger.info("Booking: specialization matched only on-leave doctor(s) — not arming a pending doctor.")

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
            patient_name=self.booking_state.get("patient_name"),
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

    # ── Cancel / reschedule of an EXISTING appointment ────────────────────────

    async def _handle_cancel_reschedule(self, text: str, text_lower: str) -> None:
        """Drive the cancel/reschedule state machine for an appointment that
        (unlike the NEW-booking flow above) already exists in the database.

        The lookup his.sync_appointment_to_db performs is by tenant + phone +
        name, so this only has to collect a name (the phone is already known
        from caller ID, via booking_state["patient_phone"]) — and, for a
        reschedule, the new day/time the caller wants.
        """
        state = self.booking_state

        # Already committed this call — nothing further to do.
        if state["action_confirmed"]:
            return

        # Arm a flow only when nothing is active yet — a bare "cancel" heard
        # while a NEW booking is still being collected already means "abort
        # that", handled entirely by the code above this call site.
        if state["mode"] is None:
            if any(p in text_lower for p in _RESCHEDULE_APPOINTMENT_PHRASES):
                state["mode"] = "reschedule"
                logger.info("Reschedule-existing-appointment intent detected.")
            elif any(p in text_lower for p in _CANCEL_APPOINTMENT_PHRASES):
                state["mode"] = "cancel"
                logger.info("Cancel-existing-appointment intent detected.")
            else:
                return

        # Collect the patient's name (phone is already known from caller ID).
        self._try_extract_name(text, text_lower)

        if state["mode"] == "reschedule":
            day, time_part = self._extract_day_and_time(text)
            if time_part:
                state["new_slot_time"] = time_part
                if day:
                    state["new_slot_day"] = day
            ready = bool(state["patient_name"]) and bool(state["new_slot_time"])
        else:
            ready = bool(state["patient_name"])

        if ready and not state["action_awaiting_confirm"]:
            state["action_awaiting_confirm"] = True
            logger.info(
                "Action '%s' ready for confirmation (name=%s, new_slot=%s %s).",
                state["mode"], state["patient_name"],
                state["new_slot_day"], state["new_slot_time"],
            )

        if not state["action_awaiting_confirm"]:
            return

        # An explicit affirmative always wins, checked BEFORE the abort words —
        # "yes, cancel it" contains the literal word "cancel" (an abort word
        # below), and abort-first would wipe the flow instead of committing it.
        if any(w in text_lower for w in _CONFIRM_WORDS):
            self._action_commit_pending = True
            logger.info("%s confirm keyword heard — commit will be awaited before LLM reply.", state["mode"])
            return

        # Caller backs out before confirming — drop the whole flow so the
        # existing appointment is left untouched and normal conversation
        # (including a fresh NEW booking) can resume. When the action itself
        # IS "cancel", the bare word "cancel" is excluded from the abort set —
        # it almost always means "yes, cancel it", not "abort the cancel".
        abort_words = _CANCEL_WORDS - {"cancel"} if state["mode"] == "cancel" else _CANCEL_WORDS
        if any(w in text_lower for w in abort_words):
            logger.info("Patient backed out of pending %s. Resetting action state.", state["mode"])
            state["mode"] = None
            state["action_awaiting_confirm"] = False
            state["new_slot_day"] = None
            state["new_slot_time"] = None
            return

    def _extract_day_and_time(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """Same recognizers as _try_extract_slot, but returned separately so a
        reschedule's new slot can be passed to his.execute_booking_action as
        distinct date_str/time_str args instead of one combined string."""
        match = _SLOT_PATTERN.search(text)
        if not match:
            return None, None
        time_part = match.group(0).strip()
        day_match = _DAY_PATTERN.search(text)
        day_part = day_match.group(0).strip().capitalize() if day_match else None
        return day_part, time_part

    async def _commit_and_inject_action_result(self, frame: LLMContextFrame) -> None:
        """AWAIT the cancel/reschedule DB write, then inject the REAL outcome
        into the LLM context — the cancel/reschedule analogue of
        _commit_and_inject_result above, kept as a separate method (and a
        separate _action_commit_pending flag) so a NEW booking in progress
        can never be confused with an existing-appointment action.
        """
        self._action_commit_pending = False
        state = self.booking_state

        tenant_id = self._tenant.get("id")
        mode = state.get("mode")
        patient_name = state.get("patient_name")
        patient_phone = state.get("patient_phone", "unknown")

        if not tenant_id or not patient_name or mode not in ("cancel", "reschedule"):
            logger.warning(
                "Confirm heard but %s incomplete (tenant=%s name=%s) — not committing.",
                mode, tenant_id, patient_name,
            )
            return

        action = "CANCEL" if mode == "cancel" else "RESCHEDULE"
        ok, result = await _commit_action_to_db(
            action=action,
            tenant_id=str(tenant_id),
            patient_name=patient_name,
            patient_phone=patient_phone,
            new_slot_day=state.get("new_slot_day"),
            new_slot_time=state.get("new_slot_time"),
            call_record_id=self._call_meta.get("call_record_id"),
        )

        context = getattr(frame, "context", None)
        if ok:
            state["action_confirmed"] = True
            state["action_awaiting_confirm"] = False
            verb = "cancelled" if mode == "cancel" else "rescheduled"
            extra = (
                f" to {state.get('new_slot_day') or ''} {state.get('new_slot_time') or ''}".strip()
                if mode == "reschedule" else ""
            )
            msg = (
                f"[BOOKING_RESULT success=true] The appointment IS {verb} in the system"
                f"{(' ' + extra) if extra else ''} (appointment id {result.get('appointment_id')}). "
                "Confirm this to the caller in one short sentence."
            )
        else:
            # Re-arm so another "yes" retries.
            state["action_confirmed"] = False
            state["action_awaiting_confirm"] = True
            if (result or {}).get("reason") == "not_found":
                msg = (
                    f"[BOOKING_RESULT success=false] No appointment was found for that name and phone "
                    f"number, so nothing was changed. Do NOT say the {mode} is done. Ask the caller to "
                    "double-check the name the appointment was booked under, or offer to connect them "
                    "to the clinic's staff."
                )
            else:
                msg = (
                    f"[BOOKING_RESULT success=false] The {mode} could NOT be saved due to a system error. "
                    "Do NOT say it is done. Apologize briefly and ask if they'd like you to try again."
                )

        if context is not None:
            try:
                context.add_message({"role": "system", "content": msg})
            except Exception as exc:
                logger.error("Failed to inject %s result into LLM context: %s", mode, exc)
        logger.info("%s commit result injected: ok=%s", mode, ok)


# ── Standalone DB commit function ─────────────────────────────────────────────

async def _commit_booking_to_db(
    tenant_id: str,
    doctor_id: str,
    slot_time: str,
    patient_phone: str,
    patient_name: Optional[str] = None,
    call_record_id: Optional[str] = None,
) -> tuple[bool, dict]:
    """
    Write appointment to PostgreSQL and return (ok, result).

    AWAITED by BookingProcessor before the LLM is allowed to speak a
    confirmation (audit FIX 4: "booked" must never be spoken on a failed or
    unconfirmed write). Idempotency lives in his.create_appointment — a
    repeated commit for the same call_id returns the existing row instead of
    creating a duplicate.

    ``patient_name`` is passed through (when the caller gave one during the
    call) so a LATER cancel/reschedule call can find this row again — the
    lookup in his.sync_appointment_to_db matches on name AND phone, and a
    NULL name can never match.
    """
    try:
        from backend.services.his import create_appointment  # Lazy import — avoids circular deps

        result = await create_appointment(
            tenant_id=tenant_id,
            doctor_id=doctor_id,
            slot_time=slot_time,
            patient_phone=patient_phone,
            patient_name=patient_name,
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


async def _commit_action_to_db(
    action: str,
    tenant_id: str,
    patient_name: str,
    patient_phone: str,
    new_slot_day: Optional[str] = None,
    new_slot_time: Optional[str] = None,
    call_record_id: Optional[str] = None,
) -> tuple[bool, dict]:
    """
    Cancel or reschedule an EXISTING appointment in PostgreSQL/Supabase and
    return (ok, result). AWAITED by BookingProcessor before the LLM is allowed
    to speak a confirmation (same audit FIX 4 contract as _commit_booking_to_db).

    Routes through his.execute_booking_action — the SAME function the
    chat/embed path uses — so voice and chat share one real, doctor/DB-backed
    implementation instead of drifting into two.
    """
    try:
        from backend.services.his import execute_booking_action  # Lazy import — avoids circular deps

        result = await execute_booking_action(
            action=action,
            tenant_id=tenant_id,
            name=patient_name,
            phone=patient_phone,
            date_str=new_slot_day or "",
            time_str=new_slot_time or "",
            doctor_name="",
            call_id=call_record_id,
        )
        if not result.get("success"):
            logger.warning("[BookingProcessor] %s failed: %s", action, result)
            return False, result
        logger.info(
            "[BookingProcessor] %s committed: appointment_id=%s",
            action, result.get("appointment_id"),
        )
        await _mark_call_action(call_record_id, action)
        return True, result
    except Exception as exc:
        logger.error(
            "[BookingProcessor] Failed to %s appointment: %s",
            action, exc, exc_info=True,
        )
        return False, {}


async def _mark_call_action(call_record_id: Optional[str], action: str) -> None:
    """Flag the call with the real outcome of a cancel/reschedule, mirroring
    _mark_call_booked's audit-P3 fix so cancelled/rescheduled calls are also
    reflected in the dashboard's call outcome column instead of reading as
    unresolved. Best-effort: a failure here never affects the caller's
    already-successful cancel/reschedule."""
    if not call_record_id:
        return
    outcome = "cancelled" if action == "CANCEL" else "rescheduled"
    try:
        from sqlalchemy import update

        from backend.db import AsyncSessionLocal
        from backend.models.call_record import CallRecord

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(CallRecord)
                .where(CallRecord.id == call_record_id)
                .values(outcome=outcome, booking_successful=True)
            )
            await db.commit()
        logger.info("[BookingProcessor] Marked call %s outcome=%s", call_record_id, outcome)
    except Exception as exc:
        logger.error("[BookingProcessor] Failed to mark call %s %s: %s", call_record_id, outcome, exc)
