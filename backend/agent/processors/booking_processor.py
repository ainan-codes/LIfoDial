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

import datetime as dt
import logging
import re
import unicodedata
from typing import Callable, Optional

from pipecat.frames.frames import Frame, LLMContextFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from backend.services.booking_trace import (
    ARMED,
    CONFIRMED,
    DROPPED,
    EXECUTED,
    EXECUTING,
    INTENT,
    REPLIED,
    VOICE,
    new_trace_id,
    trace,
)

logger = logging.getLogger(__name__)


def _said(text: str, phrases) -> bool:
    """Did the caller say any of ``phrases``, whatever script they said it in?

    Replaces the ``any(w in text_lower for w in WORDS)`` tests this file used
    everywhere. Those tests were the reason a Hindi caller's "हाँ" never
    confirmed a booking: the phrase lists are romanised, the transcript is not.
    services/indic_text.contains_any does a plain substring test first (exact
    for same-script text) and falls back to a consonant-skeleton comparison.
    """
    from backend.services.indic_text import contains_any

    return contains_any(text, phrases)


# ── Keyword sets ──────────────────────────────────────────────────────────────
#
# These are matched with indic_text.contains_any, NOT a plain `in`, because STT
# returns an Indic call's words in the caller's own script: a Hindi caller says
# "हाँ", and no amount of romanised spelling in this set will ever contain it.
# The romanised entries are still useful (callers do code-switch), and the
# native-script entries below are the ones that made a Hindi/Malayalam/Kannada
# booking completable at all. Skeleton matching covers spelling variants, so
# only genuinely different WORDS need listing.
_CONFIRM_WORDS: frozenset[str] = frozenset({
    # Romanised / English
    "yes", "haan", "ha", "okay", "ok", "theek", "theek hai", "book it",
    "confirm", "book karo", "book kar do", "book karein", "done", "sahi hai",
    "bilkul", "zaroor", "schedule it", "go ahead", "correct", "right",
    # Hindi / Marathi
    "हाँ", "हां", "ठीक है", "सही है", "बिल्कुल", "जरूर", "हो", "बरोबर",
    # Bengali, Gujarati, Punjabi, Odia
    "হ্যাঁ", "ঠিক আছে", "હા", "બરાબર", "ਹਾਂ", "ਠੀਕ ਹੈ", "ହଁ", "ଠିକ୍ ଅଛି",
    # Tamil, Telugu, Kannada, Malayalam
    "ஆம்", "சரி", "అవును", "సరే", "ಹೌದು", "ಸರಿ", "അതെ", "ശരി", "ഉവ്വ്",
    # English loanwords as STT actually returns them — in native script, not
    # romanised. Callers code-switch constantly ("ഓക്കേ", "ആ യെസ്"), and no
    # amount of romanised spelling in this set matches those bytes: the literal
    # test needs the native form, and the skeleton test cannot bridge scripts
    # for a needle this short.
    "ഓക്കേ", "യെസ്", "ഒകെ", "ओके", "यस", "ওকে", "ઓકે", "ਓਕੇ", "ஓகே", "ఓకే", "ಓಕೆ",
})

_CANCEL_WORDS: frozenset[str] = frozenset({
    "cancel", "nahi", "no", "nope", "mat karo", "band karo",
    "नहीं", "नको", "मत करो", "ना करो", "नहीं चाहिए",
    "না", "ના", "ਨਹੀਂ", "ନାହିଁ",
    "இல்லை", "వద్దు", "కాదు", "ಬೇಡ", "ಇಲ್ಲ", "വേണ്ട", "ഇല്ല",
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

# Day words that qualify a requested time ("tomorrow 3 pm", "kal 11 baje",
# "कल ग्यारह बजे"). The native-script alternatives carry no \b because word
# boundaries are meaningless against Indic scripts in Python's re.
_DAY_PATTERN = re.compile(
    r'\b(today|tomorrow|tonight|aaj|kal|parso|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b'
    r'|(आज|कल|परसों|আজ|আগামীকাল|આજે|કાલે|ਅੱਜ|ਕੱਲ੍ਹ|ଆଜି|କାଲି'
    r'|இன்று|நாளை|ఈరోజు|రేపు|ಇಂದು|ನಾಳೆ|ഇന്ന്|നാളെ)',
    re.IGNORECASE,
)

# Native day words -> the English word his.parse_slot_datetime understands.
#
# Kept as a thin view over services/dayref.RELATIVE_DAYS, which is now the ONE
# place every language's calendar vocabulary lives. This mapping existed because
# the parser only understood English day words; it understands all of them now,
# so this is belt-and-braces (and keeps the FSM's stored pending_slot readable in
# logs). "Parso" used to be emitted here for परसों and the parser did NOT
# understand it — a "day after tomorrow" booking silently landed on today.
def _day_word_to_english(word: str) -> str:
    from backend.services.dayref import to_english_day_word

    return to_english_day_word(word) or (word or "").capitalize()

# Triggers for extracting patient name from transcription. Native-script forms
# for the same reason as the confirm words above — "मेरा नाम" is what a Hindi
# transcript actually contains.
_NAME_TRIGGERS: tuple[str, ...] = (
    "my name is", "i am", "main hoon", "naam hai", "mera naam", "naam",
    "मेरा नाम", "नाम है", "माझे नाव", "আমার নাম", "મારું નામ", "ਮੇਰਾ ਨਾਮ",
    "ମୋ ନାମ", "என் பெயர்", "నా పేరు", "ನನ್ನ ಹೆಸರು", "എന്റെ പേര്",
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
            "pending_slot_day_str":  None,  # Day component, before combining into pending_slot
            "pending_slot_time_str": None,  # Time component, before combining into pending_slot
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

        # A one-shot system message queued for the NEXT LLMContextFrame — used
        # for the [AVAILABILITY_NOTE] the arm-check (_handle_transcription
        # step 3) queues when a caller-requested slot isn't actually open, so
        # the LLM learns the real alternatives instead of inventing one.
        # Parallels _commit_pending's injection mechanism but for an
        # informational note rather than a booking outcome.
        self._info_message: Optional[str] = None

        # A refreshed copy of the shared REAL DOCTOR AVAILABILITY block, queued
        # for the next LLMContextFrame. The block in the system prompt was built
        # at call setup and covers today + tomorrow; when the caller asks about
        # some other day ("Friday", "15/08/2025") the agent would otherwise have
        # to answer from nothing. Kept in its own slot rather than sharing
        # _info_message so a slot-unavailable note and a roster refresh in the
        # same turn cannot overwrite each other.
        self._availability_refresh: Optional[str] = None

        # Days the prompt already carries real slots for — so a caller saying
        # "tomorrow" does not trigger a pointless DB read on the audio path.
        self._days_in_prompt: set = set()

        # The caller's REAL existing appointments, queued for the next
        # LLMContextFrame. Own slot (not shared with _info_message) so a
        # slot-unavailable note and this cannot overwrite each other.
        self._appointments_note: Optional[str] = None
        # What the last lookup was keyed on, so an unchanged (name, phone) does
        # not re-read the DB on every turn of the audio path.
        self._appointments_key: Optional[tuple] = None

        # Correlates every stage of this call's booking attempt in the logs —
        # see services/booking_trace.py. Per-call, not per-attempt: a caller may
        # abandon one booking and start another, and both belong to this call.
        self._trace_id: str = new_trace_id()

        logger.info(
            "BookingProcessor initialised | tenant=%s caller=%s trace_id=%s",
            tenant.get("id"), self.booking_state["patient_phone"], self._trace_id,
        )

    # ── FrameProcessor interface ──────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Pass every frame through, acting on LLMContextFrames.

        LLMContextFrame is the frame that triggers LLM generation downstream —
        when a booking commit is pending, we HOLD it here, await the DB write,
        and inject the real result into the context first. This is the
        mechanism that makes "booked" impossible to speak before the row
        exists. Only the confirmation turn pays this DB round-trip; every
        other frame passes straight through (no hot-path latency added).

        ⚠️ This processor does NOT see TranscriptionFrames and must not try to.
        It sits downstream of context_aggregator.user(), which CONSUMES
        TranscriptionFrame without pushing it (pipecat 1.5.0,
        llm_response_universal.py:794 — the branch calls _handle_transcription()
        and returns, with no push_frame). It has to sit there, because
        LLMContextFrame does not exist upstream of the aggregator. Utterances
        therefore arrive sideways, from BookingTranscriptTap — which sits where
        the transcriptions still exist and calls on_user_utterance(). See that
        class's docstring; it is the SAME split UserTranscriptTap already uses
        to feed the call logger.
        """
        # REQUIRED first: lets the base FrameProcessor handle system frames
        # (StartFrame/CancelFrame/…) and mark itself started. Without it,
        # pipecat 1.5 floods "Trying to process X but StartFrame not received"
        # and blocks CancelFrame from reaching the pipeline end at teardown.
        await super().process_frame(frame, direction)

        # Nothing in here may propagate. pipecat catches an exception raised by
        # process_frame and turns it into push_error() — which routes the
        # ErrorFrame UPSTREAM (frame_processor.py:722) and, critically, SKIPS
        # the push_frame below. The in-flight LLMContextFrame is then never
        # delivered to the LLM: no generation, no TTS, no ErrorFrame anywhere
        # downstream, and the never-silence guard cannot fire because it sits
        # downstream too. The caller just hears nothing, forever. That is the
        # exact shape of the 2026-08-11 Hindi call that ended on "हेलो" into
        # dead air, so this guard is load-bearing, not defensive habit.
        try:
            if isinstance(frame, LLMContextFrame):
                await self._apply_pending_context_work(frame)
        except Exception as exc:
            logger.error(
                "BookingProcessor: turn work failed, passing the frame on anyway "
                "so the caller still gets a reply: %s", exc, exc_info=True,
            )
            trace(self._trace_id, VOICE, DROPPED, error=type(exc).__name__)

        # Always push the frame downstream — never block the voice pipeline
        await self.push_frame(frame, direction)

    async def on_user_utterance(self, text: str) -> None:
        """Feed one finalised caller utterance to the state machine.

        The public entry point, called by BookingTranscriptTap from upstream of
        context_aggregator.user(). Kept separate from _handle_transcription so
        the guard lives in exactly one place: an exception here must never
        reach the tap, because the tap would then drop the TranscriptionFrame
        and the caller's words would never reach the LLM at all.
        """
        if not text or not text.strip():
            return
        self._note_what_the_caller_said(text)
        try:
            await self._handle_transcription(text)
        except Exception as exc:
            logger.error(
                "BookingProcessor: utterance handling failed for %r: %s",
                text[:80], exc, exc_info=True,
            )
            trace(self._trace_id, VOICE, DROPPED, error=type(exc).__name__)

    def _note_what_the_caller_said(self, text: str) -> None:
        """Record the caller's OWN day words and phone number into ``call_meta``.

        ``call_meta`` is the same dict object VoiceActionProcessor holds (both are
        constructed with it in pipeline.py), and this is the channel by which "what
        the caller actually said" reaches the thing that performs the write. It has
        to be a channel: the executor sits downstream of context_aggregator.user(),
        which consumes every TranscriptionFrame, so it never sees a caller's words.

        Both facts exist to stop the MODEL being the authority on them:

        * days — measured live 2026-08-12: the caller said "कल" with today's date in
          the prompt and the model wrote 15/08/2026, three days out. A real
          appointment was created on a day nobody asked for. Dates are arithmetic;
          see services/dayref.py.
        * phone — the number a caller reads out is the key their existing
          appointments are found by, and it survives STT far better than a name.
        """
        try:
            from backend.services.dayref import note_dates_said
            from backend.services.indic_text import normalise_spoken_numbers
            from backend.services.timeutil import ist_now

            said = self._call_meta.setdefault("said_dates", [])
            note_dates_said(said, text, ist_now().date())

            # Native digit shapes and spoken number words become ASCII first, so a
            # number said as "नौ एक चार आठ..." is still a number here.
            #
            # Matched as a RUN of digits (spaces and dashes allowed inside it, since
            # a spoken number arrives as separate words) rather than by stripping
            # every non-digit from the whole sentence: "मेरा नंबर 9148768120 है और
            # 3 बजे" concatenates to 11 digits, and taking the last 10 of that
            # yields 1487681203 — a number the caller does not have, which would
            # then fail to find any of their appointments.
            from backend.services.his import normalize_phone

            for run in re.findall(r"\d[\d\s-]{8,}\d", normalise_spoken_numbers(text)):
                candidate = normalize_phone(run)
                if len(candidate) == 10:
                    self._call_meta["stated_phone"] = candidate
                    break
        except Exception as exc:
            # Advisory only — never let it cost the caller their turn.
            logger.warning("Could not record what the caller said (non-fatal): %s", exc)

    async def _apply_pending_context_work(self, frame: LLMContextFrame) -> None:
        """Commit whatever this turn armed, and inject what the LLM must know."""
        if self._commit_pending:
            await self._commit_and_inject_result(frame)

        if self._action_commit_pending:
            await self._commit_and_inject_action_result(frame)

        if self._info_message or self._availability_refresh or self._appointments_note:
            context = getattr(frame, "context", None)
            for content in (self._appointments_note, self._availability_refresh, self._info_message):
                if not content or context is None:
                    continue
                try:
                    context.add_message({"role": "system", "content": content})
                except Exception as exc:
                    logger.error("Failed to inject availability note into LLM context: %s", exc)
            self._info_message = None
            self._availability_refresh = None
            self._appointments_note = None

    # ── Internal state machine ────────────────────────────────────────────────

    async def _handle_transcription(self, text: str) -> None:
        """Apply all booking state machine rules to a completed user utterance."""
        text_lower = text.lower().strip()

        # 0.0. Real availability for a day the prompt does not already cover.
        #       Runs before every early return below (emergency, cancel flow,
        #       already-confirmed, can_book off) because "what times are free on
        #       Friday?" is a question the agent must be able to answer whatever
        #       state the booking FSM happens to be in.
        await self._maybe_refresh_availability(text)

        # 0. Emergency detection — highest priority
        if _said(text, _EMERGENCY_WORDS):
            if not self.booking_state["emergency_detected"]:
                self.booking_state["emergency_detected"] = True
                logger.warning(
                    "EMERGENCY keyword detected in utterance: '%s'", text[:80]
                )
                await self._handle_emergency()
            return  # Don't process booking after emergency

        # 0.2. This caller's REAL appointments, once we know who they are. Runs
        #      before the cancel/reschedule flow below so the agent has the actual
        #      rows in hand for the very turn it starts talking about them.
        await self._maybe_refresh_caller_appointments()

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
            # Raw text, not text_lower: the matcher works on the caller's own
            # script and lowercases per-word itself.
            self._try_match_doctor(text)

        # 3. Extract the slot the CALLER asks for. Runs whenever a doctor is
        #    pending — before confirm (caller states a time) or during confirm
        #    (caller changes the time). There is NO fabricated default slot
        #    (audit FIX 4: the old code offered a hardcoded "11:00 AM").
        if self.booking_state["pending_doctor_id"]:
            self._try_extract_slot(text)
            # Doctor + a caller-given time = ready to ask for a yes/no — but
            # only once verified against the doctor's REAL schedule + existing
            # bookings (availability.is_doctor_open_at), not just because the
            # caller said a time. check_availability_allowed() gates this
            # PROACTIVE check only — the final pre-commit check in
            # _commit_and_inject_result and the DB unique index always run
            # regardless of this toggle, since those are data-integrity, not
            # a feature switch.
            if (
                self.booking_state["pending_slot"]
                and not self.booking_state["awaiting_confirm"]
                and self.check_availability_allowed()
            ):
                from backend.services.availability import is_doctor_open_at

                slot_utc = self._parse_pending_slot_utc()
                is_open, reason = await is_doctor_open_at(
                    self._tenant.get("id"), self.booking_state["pending_doctor_id"], slot_utc,
                )
                if is_open:
                    self.booking_state["awaiting_confirm"] = True
                    logger.info(
                        "Booking: doctor '%s' + caller-requested slot '%s' — awaiting confirm.",
                        self.booking_state["pending_doctor_name"],
                        self.booking_state["pending_slot"],
                    )
                    trace(
                        self._trace_id, VOICE, ARMED, action="BOOK",
                        doctor=self.booking_state["pending_doctor_name"],
                        slot=self.booking_state["pending_slot"],
                    )
                else:
                    logger.info(
                        "Booking: requested slot '%s' not open (reason=%s) — not arming confirmation.",
                        self.booking_state["pending_slot"], reason,
                    )
                    self.booking_state["pending_slot"] = None
                    self.booking_state["pending_slot_day_str"] = None
                    self.booking_state["pending_slot_time_str"] = None
                    self._info_message = await _build_availability_note(
                        tenant_id=self._tenant.get("id"),
                        doctor_id=self.booking_state["pending_doctor_id"],
                        doctor_name=self.booking_state["pending_doctor_name"],
                        slot_utc=slot_utc,
                        reason=reason,
                    )

        # 4. Detect cancellation
        if self.booking_state["awaiting_confirm"]:
            if _said(text, _CANCEL_WORDS):
                logger.info("Patient cancelled pending booking. Resetting state.")
                self.booking_state["awaiting_confirm"] = False
                self.booking_state["pending_doctor_id"] = None
                self.booking_state["pending_slot"] = None
                return

        # 5. Detect confirmation → mark commit pending. The actual DB write is
        #    awaited on the next LLMContextFrame (see process_frame) so its
        #    real result reaches the LLM before it can speak a confirmation.
        if self.booking_state["awaiting_confirm"] and self.booking_state["pending_slot"]:
            if _said(text, _CONFIRM_WORDS):
                self._commit_pending = True
                logger.info("Booking confirm keyword heard — commit will be awaited before LLM reply.")
                trace(
                    self._trace_id, VOICE, CONFIRMED, action="BOOK",
                    doctor=self.booking_state["pending_doctor_name"],
                    slot=self.booking_state["pending_slot"],
                )

    async def _maybe_refresh_caller_appointments(self) -> None:
        """Queue the caller's real appointments for the LLM, once identifiable.

        The number the caller is calling from is enough at call setup; a number or
        name they say during the call is enough after that. Re-read only when that
        identity actually changes, so this costs one DB read per new fact rather
        than one per turn of the audio path.

        This is what turns a cancel from an interrogation into a confirmation. On
        the 2026-08-12 call the agent asked which doctor, which date, which time,
        and then for the caller's name four times — every one of which was already
        a row in the database.
        """
        tenant_id = self._tenant.get("id")
        if not tenant_id:
            return

        phone = (
            self._call_meta.get("stated_phone")
            or self.booking_state.get("patient_phone")
            or ""
        )
        name = self.booking_state.get("patient_name") or ""
        if not str(phone).strip() and not name.strip():
            return

        key = (str(phone), name)
        if key == self._appointments_key:
            return
        self._appointments_key = key

        try:
            from backend.services.availability_prompt import caller_appointments_block

            block = await caller_appointments_block(str(tenant_id), str(phone), name)
            if block:
                self._appointments_note = block
                logger.info(
                    "Caller's existing appointments injected for phone=%s name=%r",
                    phone, name,
                )
        except Exception as exc:
            logger.warning("Caller-appointments lookup failed (non-fatal): %s", exc)

    async def _maybe_refresh_availability(self, text: str) -> None:
        """Queue a refreshed shared availability block when the caller brings up
        a day the prompt's copy does not cover.

        Same builder the chat channel calls
        (services/availability_prompt.real_availability_block) — the point of
        this method is only WHEN, not WHAT. Cheap by construction: the days
        already in the prompt are skipped outright, and the builder's own 30s
        digest cache absorbs repeats within a conversation.
        """
        try:
            from backend.services.availability_prompt import (
                dates_mentioned,
                real_availability_block,
            )
            from backend.services.timeutil import ist_now

            tenant_id = self._tenant.get("id")
            if not tenant_id:
                return

            if not self._days_in_prompt:
                today = ist_now().date()
                self._days_in_prompt = {today, today + dt.timedelta(days=1)}

            wanted = [d for d in dates_mentioned(text) if d not in self._days_in_prompt]
            if not wanted:
                return

            block = await real_availability_block(str(tenant_id), text)
            if block:
                self._availability_refresh = (
                    "[AVAILABILITY_REFRESH] The real schedule for the day the caller just "
                    "mentioned. This REPLACES the availability section in your instructions "
                    "for that day — answer from it and nothing else.\n" + block
                )
                self._days_in_prompt.update(wanted)
                logger.info(
                    "Availability refreshed mid-call for %s",
                    ", ".join(d.isoformat() for d in wanted),
                )
        except Exception as exc:
            # Never let this break a turn — the agent simply asks the caller to
            # confirm the day, which the shared block's own rules instruct.
            logger.warning("Mid-call availability refresh failed (non-fatal): %s", exc)

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
                    # Strip punctuation, KEEPING combining marks. re.sub(r"[^\w]")
                    # looks Unicode-safe and is not: Python's \w excludes the
                    # nonspacing-mark category (Mn), which is where every Indic
                    # vowel sign lives. It silently turned "विनोद" into "वनद" —
                    # a name the patient does not have, stored on their
                    # appointment and read back to them.
                    clean_name = "".join(
                        ch for ch in raw_name
                        if ch.isalnum() or unicodedata.category(ch).startswith("M")
                    )
                    # .capitalize() is a no-op for scripts without case, and
                    # lowercases the rest of a romanised name, so only apply it
                    # where it means something.
                    if clean_name.isascii():
                        clean_name = clean_name.capitalize()
                    if clean_name:
                        self.booking_state["patient_name"] = clean_name
                        logger.info("Patient name captured: '%s'", clean_name)
                break

    def _try_match_doctor(self, text: str) -> None:
        """Scan the utterance for a doctor name or specialization.

        Delegates to services/doctor_match.match_doctor — the SAME matcher
        his.find_doctor_for_booking uses to resolve the doctor named in a chat
        booking tag. This used to be a private lowercase-ASCII loop, which
        meant a Hindi caller saying "सलमान" matched nothing and no
        Indian-language voice call could ever start a booking.

        An on-leave doctor is never armed for booking here: the system prompt
        (backend/agent/pipeline.py::_doctor_availability_block) already tells
        the LLM who's unavailable, so the spoken "sorry, on leave" response
        comes from there — this just has to not silently start booking a
        doctor who isn't seeing patients.
        """
        from backend.services.doctor_match import match_doctor

        doc, how = match_doctor(text, self._tenant.get("doctors", []))
        if doc is None:
            return
        if how in ("name", "specialization"):
            self._set_pending_doctor(doc)
            return
        logger.info(
            "Booking: %r matched only an unavailable doctor (%s, %s) — not arming a "
            "pending doctor; the prompt tells the caller they are on leave.",
            text[:60], doc.get("name"), how,
        )

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
        trace(
            self._trace_id, VOICE, INTENT, action="BOOK",
            doctor=doc.get("name"), doctor_id=doc.get("id"),
        )

    def _try_extract_slot(self, text: str) -> None:
        """Extract the requested slot from the caller's own words. Captures an
        explicit clock time plus any nearby day word ("tomorrow 3 pm",
        "kal 11 baje", "कल ग्यारह बजे") so the stored slot reflects what was
        actually asked.

        The utterance is normalised first: spoken number words and native digit
        shapes become ASCII digits, and the local "o'clock" word becomes "baje".
        Without that, _SLOT_PATTERN — which needs \\d — found no time at all in
        "ग्यारह बजे", so an entire Hindi call could name a time and the FSM
        would never arm a confirmation.
        """
        from backend.services.indic_text import normalise_spoken_numbers

        normalised = normalise_spoken_numbers(text)
        match = _SLOT_PATTERN.search(normalised)
        if not match:
            return
        time_part = match.group(0).strip()

        day_match = _DAY_PATTERN.search(text)
        day_part = None
        if day_match:
            raw_day = day_match.group(0).strip()
            # Native-script day words must be translated, not just capitalised:
            # his.parse_slot_datetime only knows the English/romanised forms and
            # silently treats anything else as today.
            day_part = _day_word_to_english(raw_day)
        slot = f"{day_part} {time_part}" if day_part else time_part

        self.booking_state["pending_slot"] = slot
        # Kept separately (not just parsed back out of `slot`) so
        # _parse_pending_slot_utc can hand them straight to
        # his.parse_slot_datetime(day, time) without re-splitting the
        # display string — and so they always reflect the caller's most
        # recently stated time, including a change made after arming.
        self.booking_state["pending_slot_time_str"] = time_part
        self.booking_state["pending_slot_day_str"] = day_part
        logger.info("Slot captured from caller utterance: '%s'", slot)

    def _parse_pending_slot_utc(self):
        """Parse the CURRENT pending_slot_day_str/time_str into a UTC instant.

        Deliberately re-derived on every call rather than cached: a caller
        can change their requested time while awaiting_confirm is already
        True (_try_extract_slot updates these fields every turn a doctor is
        pending, per its own docstring), but the arm-check below only runs on
        the FIRST transition into awaiting_confirm. Re-parsing fresh here
        means the final pre-commit check always validates whatever time is
        actually active, not a stale value from when the flow first armed.
        """
        time_str = self.booking_state.get("pending_slot_time_str")
        if not time_str:
            return None
        from backend.services.his import parse_slot_datetime  # Lazy import — avoids circular deps
        return parse_slot_datetime(self.booking_state.get("pending_slot_day_str"), time_str)

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

        # Final pre-commit re-check — closes the race window as tightly as
        # possible given this file's async structure: the slot could have
        # gone stale (booked by someone else, or the doctor went on leave)
        # between arming and this confirm. Always runs, regardless of
        # check_availability_allowed() — this is data-integrity, not the
        # proactive-check feature toggle. The DB unique index +
        # IntegrityError catch in his.create_appointment is what closes the
        # window completely for a genuinely concurrent second caller; this
        # just avoids a doomed DB round-trip and gives a faster, cleaner
        # message in the common (non-race) case.
        from backend.services.availability import is_doctor_open_at

        recheck_slot_utc = self._parse_pending_slot_utc()
        is_open, recheck_reason = await is_doctor_open_at(str(tenant_id), str(doctor_id), recheck_slot_utc)
        if not is_open:
            logger.info(
                "Booking: pre-commit re-check found slot '%s' no longer open (reason=%s) — not committing.",
                slot_time, recheck_reason,
            )
            self.booking_state["confirmed"] = False
            self.booking_state["awaiting_confirm"] = False
            self.booking_state["pending_slot"] = None
            self.booking_state["pending_slot_day_str"] = None
            self.booking_state["pending_slot_time_str"] = None
            context = getattr(frame, "context", None)
            if context is not None:
                try:
                    context.add_message({
                        "role": "system",
                        "content": (
                            "[BOOKING_RESULT success=false] That time is no longer available "
                            f"(reason: {recheck_reason}). Do NOT say it is booked. Apologize briefly "
                            "and ask the caller for a different time."
                        ),
                    })
                except Exception as exc:
                    logger.error("Failed to inject pre-commit availability result into LLM context: %s", exc)
            return

        # The DAY and the TIME go through separately — never the combined
        # display string. This used to pass pending_slot ("Tomorrow 11 baje"),
        # with no date at all, into create_appointment's slot_time. Downstream,
        # parse_slot_datetime(None, "Tomorrow 11 baje") could parse neither
        # field, so it fell back to TODAY at NOW and wrote that: the caller was
        # told "tomorrow at eleven", the row said this afternoon. The
        # availability gate above validated the correct instant, which is why
        # nothing complained. Chat has always passed the two fields separately
        # (execute_booking_action takes date_str, time_str) — this is the voice
        # side of that same contract.
        trace(
            self._trace_id, VOICE, EXECUTING, action="BOOK",
            doctor_id=str(doctor_id), slot=slot_time,
        )
        ok, result = await _commit_booking_to_db(
            tenant_id=str(tenant_id),
            doctor_id=str(doctor_id),
            slot_time=self.booking_state.get("pending_slot_time_str") or slot_time,
            slot_date=self.booking_state.get("pending_slot_day_str"),
            patient_phone=patient_phone,
            patient_name=self.booking_state.get("patient_name"),
            call_record_id=self._call_meta.get("call_record_id"),
        )
        trace(
            self._trace_id, VOICE, EXECUTED, action="BOOK", ok=str(ok).lower(),
            appointment_id=(result or {}).get("appointment_id"),
            reason=(result or {}).get("reason"),
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
            # (Not appropriate for slot_taken: the SAME slot would just fail
            # again — clear the stale slot so the caller is asked for a new one.)
            self.booking_state["confirmed"] = False
            if result.get("reason") == "slot_taken":
                self.booking_state["awaiting_confirm"] = False
                self.booking_state["pending_slot"] = None
                self.booking_state["pending_slot_day_str"] = None
                self.booking_state["pending_slot_time_str"] = None
                msg = (
                    "[BOOKING_RESULT success=false] That exact time was just booked by someone else "
                    "before this could be confirmed. Do NOT say it is booked. Apologize briefly and ask "
                    "the caller for a different time."
                )
            else:
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
        trace(self._trace_id, VOICE, REPLIED, action="BOOK", ok=str(ok).lower())

    def check_availability_allowed(self) -> bool:
        """Gate for the 'Check Availability' tool toggle — it governs the
        PROACTIVE check only (the arm-check in _handle_transcription step 3).

        The pre-commit checks in _commit_and_inject_result, the reschedule
        pre-check in _reschedule_slot_is_open, the gate inside
        his.execute_booking_action and the DB unique index all run regardless:
        those are data integrity, not a feature switch, and a clinic turning a
        tool off must never turn into a fabricated or conflicting booking."""
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
            if _said(text, _RESCHEDULE_APPOINTMENT_PHRASES):
                state["mode"] = "reschedule"
                logger.info("Reschedule-existing-appointment intent detected.")
                trace(self._trace_id, VOICE, INTENT, action="RESCHEDULE")
            elif _said(text, _CANCEL_APPOINTMENT_PHRASES):
                state["mode"] = "cancel"
                logger.info("Cancel-existing-appointment intent detected.")
                trace(self._trace_id, VOICE, INTENT, action="CANCEL")
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
            # A reschedule is only armed once the NEW time is verified against
            # the doctor's real schedule — the same is_doctor_open_at check the
            # NEW-booking flow above does before arming. Without it the agent
            # asked the caller to confirm a time it had never checked, and
            # (before the pre-commit gate in his.execute_booking_action) would
            # then have written it regardless. A cancel needs no such check.
            if state["mode"] == "reschedule" and not await self._reschedule_slot_is_open():
                return
            state["action_awaiting_confirm"] = True
            logger.info(
                "Action '%s' ready for confirmation (name=%s, new_slot=%s %s).",
                state["mode"], state["patient_name"],
                state["new_slot_day"], state["new_slot_time"],
            )
            trace(
                self._trace_id, VOICE, ARMED, action=state["mode"].upper(),
                patient=state["patient_name"],
                slot=f"{state['new_slot_day'] or ''} {state['new_slot_time'] or ''}".strip() or None,
            )

        if not state["action_awaiting_confirm"]:
            return

        # An explicit affirmative always wins, checked BEFORE the abort words —
        # "yes, cancel it" contains the literal word "cancel" (an abort word
        # below), and abort-first would wipe the flow instead of committing it.
        if _said(text, _CONFIRM_WORDS):
            self._action_commit_pending = True
            logger.info("%s confirm keyword heard — commit will be awaited before LLM reply.", state["mode"])
            trace(self._trace_id, VOICE, CONFIRMED, action=state["mode"].upper())
            return

        # Caller backs out before confirming — drop the whole flow so the
        # existing appointment is left untouched and normal conversation
        # (including a fresh NEW booking) can resume. When the action itself
        # IS "cancel", the bare word "cancel" is excluded from the abort set —
        # it almost always means "yes, cancel it", not "abort the cancel".
        abort_words = _CANCEL_WORDS - {"cancel"} if state["mode"] == "cancel" else _CANCEL_WORDS
        if _said(text, abort_words):
            logger.info("Patient backed out of pending %s. Resetting action state.", state["mode"])
            state["mode"] = None
            state["action_awaiting_confirm"] = False
            state["new_slot_day"] = None
            state["new_slot_time"] = None
            return

    async def _reschedule_slot_is_open(self) -> bool:
        """True when the caller's requested NEW time is genuinely open on the
        real schedule of the doctor their existing appointment is with.

        Resolves the appointment first (his.find_active_appointment — the same
        lookup the write itself uses), because a reschedule rarely names a
        doctor: the calendar that matters is the one the appointment is already
        on. On a miss, queues an [AVAILABILITY_NOTE] with that doctor's real
        open times so the agent offers actual alternatives, and leaves the flow
        un-armed so no confirmation can be asked for.

        A lookup failure (no such appointment) does NOT block arming: the
        commit path already reports "not found" honestly, and blocking here
        would leave the caller with no explanation at all.
        """
        state = self.booking_state
        tenant_id = self._tenant.get("id")
        if not tenant_id or not state.get("new_slot_time"):
            return False

        try:
            from backend.services.availability import is_doctor_open_at
            from backend.services.his import find_active_appointment, parse_slot_datetime

            existing = await find_active_appointment(
                str(tenant_id), state.get("patient_name") or "",
                state.get("patient_phone") or "",
            )
            if not existing:
                logger.info(
                    "Reschedule: no existing appointment found for '%s' — arming so the "
                    "commit can report that honestly.", state.get("patient_name"),
                )
                return True

            slot_utc = parse_slot_datetime(state.get("new_slot_day"), state.get("new_slot_time"))
            is_open, reason = await is_doctor_open_at(
                str(tenant_id), existing["doctor_id"], slot_utc,
            )
            if is_open:
                return True

            logger.info(
                "Reschedule: requested new slot '%s %s' not open (reason=%s) — not arming confirmation.",
                state.get("new_slot_day"), state.get("new_slot_time"), reason,
            )
            state["new_slot_time"] = None
            state["new_slot_day"] = None
            self._info_message = await _build_availability_note(
                tenant_id=str(tenant_id),
                doctor_id=existing["doctor_id"],
                doctor_name=existing.get("doctor_name"),
                slot_utc=slot_utc,
                reason=reason,
            )
            return False
        except Exception as exc:
            # Never let a check failure strand the caller mid-flow: arm, and let
            # the awaited pre-commit gate be the authority on whether it happens.
            logger.error("Reschedule availability pre-check failed: %s", exc, exc_info=True)
            return True

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
        trace(self._trace_id, VOICE, EXECUTING, action=action, patient=patient_name)
        ok, result = await _commit_action_to_db(
            action=action,
            tenant_id=str(tenant_id),
            patient_name=patient_name,
            patient_phone=patient_phone,
            new_slot_day=state.get("new_slot_day"),
            new_slot_time=state.get("new_slot_time"),
            call_record_id=self._call_meta.get("call_record_id"),
        )
        trace(
            self._trace_id, VOICE, EXECUTED, action=action, ok=str(ok).lower(),
            appointment_id=(result or {}).get("appointment_id"),
            reason=(result or {}).get("reason"),
        )

        context = getattr(frame, "context", None)
        reason = (result or {}).get("reason") or ""
        if ok and reason == "already_at_that_time":
            # Nothing moved, and nothing is wrong — but "rescheduled" would be a
            # lie about a change that never happened.
            state["action_confirmed"] = True
            state["action_awaiting_confirm"] = False
            msg = (
                "[BOOKING_RESULT success=true] The caller's appointment was ALREADY at exactly that time "
                f"(appointment id {result.get('appointment_id')}), so nothing needed to change. Tell them "
                "in one short sentence that it is already at that time and still confirmed."
            )
        elif ok:
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
            if reason == "not_found":
                msg = (
                    f"[BOOKING_RESULT success=false] No appointment was found for that name and phone "
                    f"number, so nothing was changed. Do NOT say the {mode} is done. Ask the caller to "
                    "double-check the name the appointment was booked under, or offer to connect them "
                    "to the clinic's staff."
                )
            elif reason in ("slot_taken", "outside_hours", "slot_in_past", "slot_unavailable",
                            "no_schedule", "doctor_unavailable"):
                # The requested new time was refused by the availability engine.
                # Clearing it (and standing the flow down) is what stops a
                # repeated "yes" from retrying a time that can never succeed.
                state["action_awaiting_confirm"] = False
                state["new_slot_time"] = None
                state["new_slot_day"] = None
                alts = ", ".join((result or {}).get("alternatives") or [])
                offer = (f" That doctor IS free at: {alts}. Offer those exact times and nothing else."
                         if alts else " Ask the caller for a different day or time.")
                msg = (
                    f"[BOOKING_RESULT success=false] That time is NOT open on the doctor's real schedule, "
                    f"so NOTHING changed and the existing appointment still stands at its original time. "
                    f"Do NOT say the {mode} is done, and do NOT offer that same time again.{offer}"
                )
            elif reason == "invalid_time":
                state["new_slot_time"] = None
                state["new_slot_day"] = None
                msg = (
                    f"[BOOKING_RESULT success=false] No valid new TIME was given, so NOTHING changed and "
                    f"the existing appointment still stands. Do NOT say the {mode} is done. Ask the caller "
                    "to state the new time again, e.g. '3 PM' or '11:30 AM'."
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
        trace(self._trace_id, VOICE, REPLIED, action=action, ok=str(ok).lower())


class BookingTranscriptTap(FrameProcessor):
    """Feeds finalised caller utterances to a BookingProcessor.

    Exists purely because of frame placement — the same split UserTranscriptTap
    already uses for the call logger, and for the same underlying reason.

    BookingProcessor must sit AFTER context_aggregator.user(), because the
    LLMContextFrame it injects [BOOKING_RESULT ...] into does not exist before
    the aggregator builds it. But TranscriptionFrames never get that far: the
    aggregator CONSUMES them without pushing downstream (pipecat 1.5.0,
    llm_response_universal.py:794).

    The consequence, until this tap existed, was total: BookingProcessor
    received zero transcriptions for the entire life of the product, so the
    voice state machine never matched a doctor, never captured a slot, never
    heard a confirmation, and never wrote a row. Production bore that out —
    every appointment ever created had ``call_id IS NULL`` (chat), and not one
    came from a call. The FSM and its unit tests were correct throughout; the
    frames simply never arrived, because every test called
    ``_handle_transcription`` directly instead of driving the pipeline.

    This tap sits between `stt` and the aggregator, where the frames still
    exist, and hands the text sideways. Fully transparent: every frame is
    pushed on unchanged, and the handler is guarded, so a booking error can
    never swallow the caller's words on the way to the LLM.
    """

    def __init__(
        self,
        booking_processor: BookingProcessor,
        on_new_turn: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self._booking = booking_processor
        # Called once per finalised caller utterance, before the FSM sees it.
        # VoiceActionProcessor uses it to lift its per-turn caps — it cannot
        # detect a new turn itself for exactly the reason described above (the
        # aggregator between it and `stt` eats the TranscriptionFrames), and
        # giving it its own tap would cost a second passthrough hop for every
        # audio frame on a CPU-starved free-tier worker.
        self._on_new_turn = on_new_turn

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # REQUIRED first (pipecat 1.5): handle system frames + mark started.
        await super().process_frame(frame, direction)

        # Finalised transcriptions only. InterimTranscriptionFrame is a running
        # hypothesis that gets revised, so acting on one would arm a booking
        # from half a sentence — and could fire a confirm on a "yes" the caller
        # never finished saying.
        if isinstance(frame, TranscriptionFrame) and (frame.text or "").strip():
            if self._on_new_turn is not None:
                try:
                    self._on_new_turn()
                except Exception as exc:
                    logger.error("BookingTranscriptTap: new-turn listener failed: %s", exc)

            # Awaited BEFORE the frame is pushed on, deliberately: the
            # aggregator downstream turns this same transcription into the
            # LLMContextFrame, and BookingProcessor must already have armed
            # (or committed) by the time that frame reaches it. Pushing first
            # would let the context frame overtake the state it depends on.
            await self._booking.on_user_utterance(frame.text)

        await self.push_frame(frame, direction)


async def _build_availability_note(
    tenant_id: Optional[str],
    doctor_id: Optional[str],
    doctor_name: Optional[str],
    slot_utc,
    reason: str,
) -> str:
    """Build an [AVAILABILITY_NOTE] system message listing the doctor's REAL
    open slots for the day the caller asked about, so the LLM offers actual
    alternatives instead of inventing a nearby time (see booking_rules.py
    rule 6). Unlike the static per-call doctor roster in
    pipeline.py::_clinic_facts_block, this is computed per-request since it
    depends on the specific day the caller asked about.
    """
    from backend.services.availability import compute_available_slots
    from backend.services.timeutil import format_ist_clock, ist_now, to_ist

    name = doctor_name or "that doctor"
    target_date = to_ist(slot_utc).date() if slot_utc is not None else ist_now().date()
    slots = await compute_available_slots(tenant_id, doctor_id, target_date) if (tenant_id and doctor_id) else []

    if not slots:
        return (
            f"[AVAILABILITY_NOTE] {name} has no open slots on that day (reason: {reason}). "
            "Ask the caller for a different day."
        )

    times = ", ".join(format_ist_clock(to_ist(s)) for s in slots[:5])
    return (
        f"[AVAILABILITY_NOTE] {name} is only actually open at these times that day: {times}. "
        "Only offer these specific times to the caller — never invent a nearby time."
    )


# ── Standalone DB commit function ─────────────────────────────────────────────

async def _commit_booking_to_db(
    tenant_id: str,
    doctor_id: str,
    slot_time: str,
    patient_phone: str,
    slot_date: Optional[str] = None,
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

    ``slot_date`` and ``slot_time`` are the caller's day and time as SEPARATE
    strings ("Tomorrow", "11 baje"), because that is what create_appointment
    parses. Handing it one combined display string silently lost the day.

    ``patient_name`` is passed through (when the caller gave one during the
    call) so a LATER cancel/reschedule call can find this row again — the
    lookup in his.sync_appointment_to_db matches on name AND phone, and a
    NULL name can never match.
    """
    try:
        from backend.services.his import create_appointment  # Lazy import — avoids circular deps

        from backend.models.appointment import SOURCE_VOICE

        result = await create_appointment(
            tenant_id=tenant_id,
            doctor_id=doctor_id,
            slot_time=slot_time,
            slot_date=slot_date,
            patient_phone=patient_phone,
            patient_name=patient_name,
            call_id=call_record_id,
            source=SOURCE_VOICE,
        )
        if not result or not result.get("appointment_id"):
            logger.error("[BookingProcessor] create_appointment returned no appointment_id: %r", result)
            return False, (result or {})
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

        from backend.models.appointment import SOURCE_VOICE

        result = await execute_booking_action(
            action=action,
            tenant_id=tenant_id,
            name=patient_name,
            phone=patient_phone,
            date_str=new_slot_day or "",
            time_str=new_slot_time or "",
            doctor_name="",
            call_id=call_record_id,
            source=SOURCE_VOICE,
        )
        if not result.get("success"):
            logger.warning("[BookingProcessor] %s failed: %s", action, result)
            return False, result
        logger.info(
            "[BookingProcessor] %s committed: appointment_id=%s reason=%s",
            action, result.get("appointment_id"), result.get("reason") or "-",
        )
        # A no-op reschedule succeeded without changing the appointment, so the
        # call outcome must not be recorded as "rescheduled".
        if result.get("reason") != "already_at_that_time":
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
