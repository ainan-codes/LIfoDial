"""
backend/agent/processors/voice_action.py

Turns the voice agent's SPOKEN promise into a real database row.

Why this exists
---------------
Until this processor, the voice path had exactly one writer: the keyword state
machine in ``processors/booking_processor.py``. That machine can only commit when
the CALLER names the doctor and then, in a LATER utterance, says a word from a
confirm list. A real call does neither:

    caller     "I have some chest pain, which doctor is best?"
    agent      "For chest pain I'd suggest Dr Salman, our cardiologist…"
    caller     "Tomorrow at 2 PM then, I'll come."
    agent      "Done — your appointment with Dr Salman is booked for 2 PM."   ← a lie

The doctor was chosen by the AGENT (the FSM never sees the agent's words) and
there was no yes/no confirmation turn at all, because the model skipped straight
to confirming. So the FSM never armed, never committed — and nothing stopped the
model from claiming success anyway. Measured against production on 2026-08-12:
every appointment row in the database had ``call_id IS NULL``, i.e. not one voice
call in the product's lifetime had ever booked anything, and both calls made that
morning ended with the agent telling the caller in Hindi that their appointment
was booked.

The chat channel does not have this problem, because there the MODEL signals the
write with an ``[ACTION: BOOK|…]`` tag and the router executes it before
producing the user-facing reply. This processor gives the voice channel the same
mechanism, using the same parser (``services/action_tag.py``) and the same
executor (``services/his.execute_booking_action``).

How a turn flows
----------------
Placed between ``llm`` and ``tag_scrub``, so it sees the model's raw streamed
text before anything is spoken.

* The model is instructed (``booking_rules.VOICE_ACTION_TAG_BLOCK``) to put the
  tag at the very START of a reply that performs an action. So the FIRST text
  chunk answers "is this an action turn?" — and only action turns pay any cost:

  - **Ordinary turn** (first chunk is not a bracket): every frame passes straight
    through, unbuffered. Zero added latency, which is the whole point of
    deciding on the first chunk instead of buffering the response.

  - **Action turn** (reply starts with ``[``): the whole reply is HELD — nothing
    is spoken. Once the tag closes, the DB write is awaited, the true outcome is
    injected into the LLM context as ``[BOOKING_RESULT …]``, the model's
    pre-outcome text is DISCARDED, and the LLM is re-run so what the caller
    hears is generated from the real result. This is deliberately identical to
    what the chat path does with its phase-1 text.

* Two recovery paths for a model that ignores the instructions:

  - tag emitted LATE (after prose that was already spoken): the write still
    happens; if it FAILED, the LLM is re-run so the agent corrects itself
    immediately instead of leaving a false confirmation standing.
  - no tag at all, but the reply claims the appointment is booked/cancelled/
    rescheduled (the exact production bug): the model is re-prompted to emit
    the tag it forgot, and the resulting action executes normally.

Both recoveries are capped at one per user turn, so a stubborn model costs a
bounded number of extra LLM calls, never a loop.

Nothing here may raise: an exception in ``process_frame`` becomes
``push_error()``, which routes UPSTREAM and SKIPS the ``push_frame`` below —
leaving the caller listening to silence forever. Same rule, and the same
reasoning, as BookingProcessor's guard.
"""

import logging
from typing import Optional

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: The CALLER's words are TextFrames too — TranscriptionFrame subclasses it in
#: pipecat 1.5. They are consumed by context_aggregator.user() well upstream of
#: this processor and so should never arrive here, but "should never" is exactly
#: how the original bug happened in reverse. Treating a caller's sentence as LLM
#: output would mean holding their speech back and, worse, executing an
#: "[ACTION: …]" tag the CALLER dictated.
_USER_TEXT_FRAMES = (TranscriptionFrame, InterimTranscriptionFrame)

from backend.agent.booking_rules import BOOKING_RESULT_FALSE, BOOKING_RESULT_TRUE
from backend.models.appointment import SOURCE_VOICE
from backend.services.action_tag import (
    ActionTag,
    claims_any_completion,
    has_open_action_tag,
    is_placeholder,
    missing_identity_fields,
    needs_real_time,
    parse_action_tag,
    promises_followup,
    scrub_reply,
)
from backend.services.booking_trace import (
    CONFIRMED,
    EXECUTED,
    EXECUTING,
    INTENT,
    REPLIED,
    VOICE,
    new_trace_id,
    trace,
)

logger = logging.getLogger(__name__)


# What the model is told after an action, so its next words describe the REAL
# outcome. Built from the same result dict the chat path renders its own
# [BOOKING_RESULT …] line from; kept separate only because the wording is
# spoken-language ("caller", one short sentence) rather than chat-language.
def build_result_message(action: str, res: dict) -> str:
    """The authoritative ``[BOOKING_RESULT …]`` line for a spoken reply."""
    action = (action or "").upper()
    verb = {"BOOK": "booking", "RESCHEDULE": "reschedule", "CANCEL": "cancellation"}.get(
        action, "request"
    )
    done = {"BOOK": "booked", "RESCHEDULE": "rescheduled", "CANCEL": "cancelled"}.get(
        action, "done"
    )

    if res.get("reason") == "already_at_that_time":
        return (
            f"{BOOKING_RESULT_TRUE} The caller's appointment was ALREADY at exactly that time "
            f"(appointment id {res.get('appointment_id')}), so nothing needed to change. Tell them "
            "in ONE short spoken sentence that it is already at that time and still confirmed."
        )
    if res.get("success"):
        detail = f"appointment id {res.get('appointment_id')}"
        if res.get("doctor_name"):
            detail += f", doctor {res['doctor_name']}"
        if res.get("slot"):
            detail += f", {res['slot']}"
        return (
            f"{BOOKING_RESULT_TRUE} The appointment IS {done} in the system ({detail}). "
            "Tell the caller in ONE short spoken sentence, in their language. Do not read out the "
            "appointment id."
        )

    reason = res.get("reason") or ""
    if reason == "disabled":
        return (
            f"{BOOKING_RESULT_FALSE} This clinic has that feature turned off, so NOTHING was "
            f"{done}. Do NOT say it is {done}. Say you cannot do that on this line and offer to "
            "pass them to the clinic's staff."
        )
    if reason in ("doctor_not_found", "doctor_required"):
        docs = ", ".join(res.get("available_doctors") or [])
        avail = (
            f" The doctors at this clinic are: {docs}."
            if docs else " There are no doctors listed at this clinic yet."
        )
        req = (
            "The doctor the caller asked for is not at this clinic"
            if reason == "doctor_not_found" else "No specific available doctor was chosen"
        )
        return (
            f"{BOOKING_RESULT_FALSE} {req}, so NOTHING was booked. Do NOT say it is booked.{avail} "
            "Ask the caller which of those doctors they would like."
        )
    if reason == "not_found":
        return (
            f"{BOOKING_RESULT_FALSE} There is no active appointment on that phone number, so NOTHING "
            f"was {done}. Do NOT say it is {done}.\n"
            "Do NOT ask the caller to spell or repeat their name — the search does not depend on "
            "spelling, so asking again cannot change this answer. (On a live call this instruction "
            "was missing and the agent asked the same caller to spell their name four times, for 280 "
            "seconds, and cancelled nothing.) Ask ONCE for the phone number the appointment was "
            "booked under, and if that is no help, offer to pass them to the clinic's staff."
        )
    if reason == "invalid_time":
        return (
            f"{BOOKING_RESULT_FALSE} No valid TIME was given, so NOTHING was {done} — never assume "
            f"a time. Do NOT say it is {done}. Ask the caller to say the time again, like "
            "'3 PM' or '11:30 AM'."
        )
    if reason == "invalid_date":
        return (
            f"{BOOKING_RESULT_FALSE} The DAY in your tag was not a real date, so NOTHING was "
            f"{done} — and it must never be guessed at. Do NOT say it is {done}. Ask the caller "
            "which day they want, and write it as the caller's own word ('tomorrow') or as "
            "DD/MM/YYYY."
        )
    if reason == "missing_details":
        need = " and ".join(res.get("missing") or ["the caller's details"])
        return (
            f"{BOOKING_RESULT_FALSE} NOTHING was {done}: you have not asked the caller for their "
            f"{need} yet, and an appointment without it can never be found again to cancel or "
            f"reschedule. Do NOT say it is {done}. Ask for their {need} in ONE short question."
        )
    if reason in ("slot_taken", "outside_hours", "slot_in_past", "slot_unavailable"):
        who = res.get("doctor_name") or "That doctor"
        alts = ", ".join(res.get("alternatives") or [])
        offer = (
            f" {who} IS free at: {alts}. Offer those exact times and nothing else."
            if alts else " Ask the caller for a different day or time."
        )
        what = {
            "slot_taken": "That exact time is ALREADY BOOKED for that doctor",
            "outside_hours": f"{who} does not consult at that time",
            "slot_in_past": "That time has already passed",
        }.get(reason, "That time is not open on that doctor's real schedule")
        return (
            f"{BOOKING_RESULT_FALSE} {what}, so NOTHING was {done}. Do NOT say it is {done}, and do "
            f"NOT offer that same time again.{offer}"
        )
    if reason == "no_schedule":
        who = res.get("doctor_name") or "that doctor"
        return (
            f"{BOOKING_RESULT_FALSE} {who} has no consulting hours set for that day, so NOTHING was "
            f"{done}. Do NOT say it is {done}. Tell the caller that and ask for a different day."
        )
    if reason == "doctor_unavailable":
        who = res.get("doctor_name") or "That doctor"
        return (
            f"{BOOKING_RESULT_FALSE} {who} is ON LEAVE, so NOTHING was {done}. Do NOT say it is "
            f"{done}. Say that doctor is not available and offer another doctor at this clinic."
        )
    return (
        f"{BOOKING_RESULT_FALSE} The {verb} could NOT be saved because of a system error, so NOTHING "
        f"was {done}. Do NOT say it is {done}. Apologize in one short sentence and ask if they'd like "
        "you to try again."
    )


#: Appended when the LLM is re-run so it never emits a second tag for work that
#: has already happened (which would re-enter this whole flow).
_NO_SECOND_TAG = (
    " The action has already been carried out by the system — do NOT emit another "
    "[ACTION: ...] tag in this reply."
)

#: One strict re-prompt after the model told the caller an appointment was
#: booked/cancelled/rescheduled without emitting a tag — so nothing happened.
#: The voice analogue of the chat path's _repair_missing_action_tag.
_REPAIR_INSTRUCTION = (
    "[BOOKING_RESULT success=false] You just told the caller an appointment was booked, cancelled "
    "or rescheduled — or that you were about to do it — but you did NOT emit an [ACTION: ...] tag, "
    "so NOTHING happened and what you said is not true. Saying it does not do it; only the tag "
    "does. There is nothing running in the background and nothing will happen later.\n"
    "Fix it NOW in your next reply, choosing EXACTLY ONE of:\n"
    "  (a) Output the correct [ACTION: ...] tag as the WHOLE of your reply, nothing else. For a "
    "CANCEL you need only the caller's name and number — put N/A in the date, time and doctor "
    "fields. If the caller's existing appointments are listed above, every detail you need is "
    "already there; do not ask for it again.\n"
    "  (b) If a detail is genuinely missing, ask for THAT ONE detail in one short question, and "
    "claim nothing.\n"
    "Never say 'hold on', 'one moment', 'I'll start the process' or 'I'll proceed' — nothing "
    "follows it, and the caller will wait for a reply that never comes."
)

#: The model's whole reply was a machine tag this system could not parse (a
#: mangled field list, or the token cap cutting it off mid-tag). Nothing was
#: written and nothing could be spoken, so the caller is sitting in silence.
_MALFORMED_TAG_INSTRUCTION = (
    "[BOOKING_RESULT success=false] Your last reply was a machine tag this system could not read, so "
    "NOTHING was saved and the caller heard nothing at all. Reply again, choosing EXACTLY ONE of:\n"
    "  (a) The correct tag, exactly in the form "
    "[ACTION: BOOK|Name|Phone|DD/MM/YYYY|Time|Doctor|Notes] — seven fields separated by | inside one "
    "pair of square brackets, and nothing else in the reply.\n"
    "  (b) If any field is missing, ONE short spoken question asking the caller for it, with no tag "
    "and no claim that anything is done."
)


class VoiceActionProcessor(FrameProcessor):
    """Executes the LLM's ``[ACTION: …]`` tags on the voice path.

    Constructor args:
        context: the shared ``LLMContext`` the aggregators wrap — the same object
            BookingProcessor injects into. Result messages are appended here.
        tenant: tenant dict (needs ``id``).
        agent_config: agent config dict (capability toggles).
        call_meta: call metadata — ``caller_phone`` fills in a phone the model
            never had to ask for, ``call_record_id`` is the booking idempotency
            key and the row's ``call_id``.
    """

    def __init__(
        self,
        context,
        tenant: dict,
        agent_config: dict,
        call_meta: dict,
    ) -> None:
        super().__init__()
        self._context = context
        self._tenant = tenant or {}
        self._agent_config = agent_config or {}
        self._call_meta = call_meta or {}

        # ── Per-response state (one LLM generation) ───────────────────────────
        #: Everything the model produced this response, kept whole. The end-of-
        #: response checks read THIS, not _buf: _buf is emptied whenever held
        #: text is released or a tag is executed, and a check that read it would
        #: silently stop seeing the parts of the reply that had already moved on.
        self._full: str = ""
        #: The portion currently being HELD back, pending "is this a tag?".
        self._buf: str = ""
        #: None until the first non-blank chunk decides it: True = hold the whole
        #: response (it starts with a bracket, so a tag may be forming),
        #: False = stream it through untouched.
        self._holding: Optional[bool] = None
        #: True once this response's tag has been executed: everything it says
        #: after that was written before the outcome was known, so it is dropped.
        self._dropping: bool = False
        #: Set while this response exists only to report an outcome that already
        #: happened — it must not execute tags or trigger another repair.
        self._is_followup: bool = False
        #: An LLMRunFrame owed to the pipeline. Pushed only AFTER this response's
        #: LLMFullResponseEndFrame has gone downstream: the assistant aggregator
        #: at the tail builds the re-run from the current response's close, and
        #: handing it a run frame mid-response would race that.
        self._pending_rerun: bool = False

        # ── Per-user-turn state (survives the responses within one turn) ───────
        self._acted_this_turn: bool = False
        self._repaired_this_turn: bool = False
        self._next_is_followup: bool = False

        self._trace_id: str = new_trace_id()

        logger.info(
            "VoiceActionProcessor initialised | tenant=%s call_record=%s trace_id=%s",
            self._tenant.get("id"), self._call_meta.get("call_record_id"), self._trace_id,
        )

    # ── FrameProcessor interface ──────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # REQUIRED first (pipecat 1.5): lets the base class handle system frames
        # and mark itself started.
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        try:
            if isinstance(frame, InterruptionFrame):
                # The caller barged in. Held text belongs to an abandoned reply,
                # and an owed re-run would speak over them.
                self._reset_response()
                self.reset_turn()

            elif isinstance(frame, LLMFullResponseStartFrame):
                self._reset_response()
                self._is_followup = self._next_is_followup
                self._next_is_followup = False

            elif isinstance(frame, TextFrame) and not isinstance(frame, _USER_TEXT_FRAMES):
                if await self._on_text(frame, direction):
                    return  # held or dropped — do NOT push it

            elif isinstance(frame, LLMFullResponseEndFrame):
                # The end frame goes first, so the response is closed before any
                # re-run is requested (see _pending_rerun).
                await self.push_frame(frame, direction)
                await self._on_response_end(direction)
                return

        except Exception as exc:
            # Never strand the caller: fall back to plain pass-through.
            logger.error(
                "VoiceActionProcessor: turn work failed, passing the frame on so the caller "
                "still hears a reply: %s", exc, exc_info=True,
            )
            await self._release_held(direction)
            self._holding = False
            self._dropping = False

        await self.push_frame(frame, direction)

    # ── Streaming decision ────────────────────────────────────────────────────

    async def _on_text(self, frame: TextFrame, direction: FrameDirection) -> bool:
        """Accumulate one chunk. Returns True if the frame must NOT be pushed
        (because it is being held, or dropped as pre-outcome text)."""
        if self._dropping:
            # This response's action already ran; everything it says now was
            # written before the outcome was known. The re-run speaks instead.
            return True

        self._full += frame.text or ""

        if self._holding is False:
            return False  # streaming — push it straight through

        self._buf += frame.text or ""

        if self._holding is None:
            stripped = self._buf.lstrip()
            if not stripped:
                return True  # only whitespace so far; nothing to decide on yet
            if stripped.startswith("["):
                self._holding = True
                logger.debug("VoiceActionProcessor: reply starts with a bracket — holding.")
            else:
                self._holding = False
                # Only whitespace-only chunks preceded this one, so pushing this
                # frame emits everything that matters.
                self._buf = ""
                return False

        # Holding. Has a complete tag arrived?
        tag = parse_action_tag(self._buf)
        if tag is not None:
            if self._acted_this_turn:
                # One action per caller utterance, full stop. The re-run is told
                # not to emit a second tag; if it does anyway, ignoring it is what
                # keeps "execute -> re-run -> execute" from becoming a loop.
                # tag_scrub downstream removes the tag from what is spoken.
                logger.warning(
                    "VoiceActionProcessor: ignoring a second [ACTION:] tag in the same turn — "
                    "one action per utterance."
                )
                await self._release_held(direction)
                self._holding = False
                return True
            await self._execute_and_regenerate(tag)
            return True

        if not has_open_action_tag(self._buf):
            # The leading bracket was not a machine tag after all. Release
            # everything held so far rather than swallow real words.
            await self._release_held(direction)
            self._holding = False
            return True

        return True  # still could become a tag — keep holding

    async def _on_response_end(self, direction: FrameDirection) -> None:
        """Resolve whatever this response left unfinished, then honour an owed
        re-run. Called AFTER the LLMFullResponseEndFrame has been forwarded."""
        full = self._full
        held = self._buf
        was_holding = self._holding is True
        was_followup = self._is_followup
        acted = self._acted_this_turn
        # A re-run already owed means this response's action ran and its outcome
        # is on its way to the caller. Nothing below applies, and stacking a
        # second recovery on top of it is how a loop would start.
        owed_rerun = self._pending_rerun
        self._reset_response()

        if was_holding and held:
            # Held to the end without ever becoming a parseable tag. An
            # unterminated machine tag is dropped (tag_scrub would drop it too);
            # anything else is real speech and must not be swallowed.
            speakable_held = scrub_reply(held)
            if speakable_held:
                logger.warning(
                    "VoiceActionProcessor: held a bracketed reply that never became a tag — "
                    "speaking it: %r", speakable_held[:120],
                )
                await self.push_frame(TextFrame(speakable_held), direction)

        if owed_rerun or not full.strip():
            await self._flush_rerun(direction)
            return

        speakable = scrub_reply(full)
        tag = parse_action_tag(full)

        if not speakable:
            # NOTHING reached TTS: the model replied with a machine tag this
            # system could not parse, or one the token cap cut off mid-way. The
            # caller is sitting in silence — the worst possible outcome on a
            # phone call — so re-prompt rather than hang up on them.
            if self._repaired_this_turn:
                logger.error(
                    "VoiceActionProcessor: an unspeakable machine-tag reply AGAIN — giving up on "
                    "this turn rather than looping: %r", full[:160],
                )
            else:
                logger.error(
                    "VoiceActionProcessor: the whole reply was an unparseable machine tag, so there "
                    "was nothing to speak — re-prompting: %r", full[:160],
                )
                self._repaired_this_turn = True
                self._inject(_MALFORMED_TAG_INSTRUCTION, rerun=True)

        elif not was_followup:
            # ── Recovery 1: the tag came LATE (after words already spoken) ─────
            if tag is not None and not acted:
                logger.warning(
                    "VoiceActionProcessor: the [ACTION:] tag arrived AFTER spoken text — executing "
                    "it now. It is required to come first; see VOICE_ACTION_TAG_BLOCK."
                )
                res = await self._execute(tag)
                # Re-run the LLM only when the outcome CONTRADICTS what was said.
                # On success the words the caller heard are now true, and a second
                # sentence would only repeat them.
                self._inject(build_result_message(tag.action, res), rerun=not res.get("success"))

            # ── Recovery 2: talked about acting, without acting ────────────────
            #
            # Two shapes, one cause — the model described an action it never
            # signalled, so NOTHING was written:
            #
            #   claimed  "your appointment is booked"        (2026-08-12, 2 of 2 calls)
            #   promised "I'll start cancelling it now"      (2026-08-12 cancel call:
            #            "मैं इस अपॉइंटमेंट को कैंसिल करने की प्रक्रिया शुरू करूंगा", then
            #            280 seconds and nothing cancelled)
            #
            # A promise is worse than a claim, because the caller waits for it. The
            # chat path has caught both since 2026-08-10; voice caught neither.
            elif (
                tag is None
                and not acted
                and not self._repaired_this_turn
                and (claims_any_completion(speakable) or promises_followup(speakable))
            ):
                fabricated = claims_any_completion(speakable)
                logger.error(
                    "VoiceActionProcessor: the agent %s an appointment action with no [ACTION:] "
                    "tag — nothing was written. Re-prompting for the tag. Said: %r",
                    "CLAIMED" if fabricated else "PROMISED to perform",
                    speakable[:160],
                )
                trace(self._trace_id, VOICE, "action_tag_missing",
                      repairing="true", fabricated=str(fabricated).lower())
                self._repaired_this_turn = True
                self._inject(_REPAIR_INSTRUCTION, rerun=True)

        await self._flush_rerun(direction)

    async def _flush_rerun(self, direction: FrameDirection) -> None:
        """Push the LLMRunFrame owed to the pipeline, if any."""
        if not self._pending_rerun:
            return
        self._pending_rerun = False
        self._next_is_followup = True
        trace(self._trace_id, VOICE, REPLIED, source="regenerated")
        await self.push_frame(LLMRunFrame(), direction)

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute_and_regenerate(self, tag: ActionTag) -> None:
        """The compliant path: the tag came first, so NOTHING has been spoken yet.

        The write is awaited and the model's pre-outcome text is discarded, then
        the LLM is re-run with the real result — so the first words the caller
        hears about their appointment are generated from what actually happened.
        Exactly what the chat path does with its phase-1 text.
        """
        held, self._buf = self._buf, ""
        # Everything else in this response was written before the outcome was
        # known, so it is dropped as it arrives.
        self._dropping = True
        self._holding = False

        res = await self._execute(tag)

        leftover = scrub_reply(held)
        if leftover:
            logger.info(
                "VoiceActionProcessor: discarding pre-outcome text from the action turn: %r",
                leftover[:120],
            )
        self._inject(build_result_message(tag.action, res), rerun=True)

    async def _execute(self, tag: ActionTag) -> dict:
        """Run one tag against the real booking service and return its result dict.

        Every gate the chat path applies is applied here, from the same shared
        module — with one voice-only addition: a phone number the model never
        collected falls back to the CALLER'S OWN number from caller ID. On a
        phone call that is better evidence than anything the model could have
        transcribed, and it is what BookingProcessor has always stored.
        """
        tenant_id = str(self._tenant.get("id") or "")
        action = tag.action
        if not tenant_id:
            logger.error("VoiceActionProcessor: no tenant on this call — refusing to write.")
            return {"success": False, "reason": "db_error", "appointment_id": None}

        trace(
            self._trace_id, VOICE, INTENT, action=action, doctor=tag.doctor or None,
            slot=f"{tag.date} {tag.time}".strip() or None,
        )
        trace(self._trace_id, VOICE, CONFIRMED, action=action)
        self._acted_this_turn = True

        phone = tag.phone
        if is_placeholder(phone):
            caller = self._call_meta.get("caller_phone") or ""
            if not is_placeholder(caller):
                phone = caller
                logger.info("VoiceActionProcessor: using caller ID for the patient's phone.")

        filled = tag._replace(phone=phone)

        # Capability toggles — a clinic that switched a tool off must not have it
        # used, exactly as on the chat path.
        can_book = bool(self._agent_config.get("can_book_appointments", True))
        can_cancel = bool(self._agent_config.get("can_cancel_appointments", True))
        allowed = can_book if action in ("BOOK", "RESCHEDULE") else (
            can_cancel if action == "CANCEL" else False)

        from backend.services.his import (
            execute_booking_action,
            is_date_str_parseable,
            is_time_str_parseable,
        )

        date_str = self._resolve_date(tag)

        missing = missing_identity_fields(filled)
        if needs_real_time(action) and not is_time_str_parseable(tag.time):
            res = {"success": False, "reason": "invalid_time", "appointment_id": None}
        elif (
            needs_real_time(action)
            and not is_placeholder(date_str)
            and not is_date_str_parseable(date_str)
        ):
            # A day that was GIVEN but names nothing ("next week") would silently
            # become TODAY. Refuse and ask, exactly as for a missing time.
            #
            # A day that was not given at all is left to execute_booking_action,
            # which is the one place that can give it the right meaning per action:
            # a BOOK with no day is refused, a RESCHEDULE with no day stays on the
            # day the appointment is already on.
            res = {"success": False, "reason": "invalid_date", "appointment_id": None}
        elif missing:
            res = {"success": False, "reason": "missing_details", "appointment_id": None,
                   "missing": missing}
        elif not allowed:
            res = {"success": False, "reason": "disabled", "appointment_id": None}
        else:
            trace(self._trace_id, VOICE, EXECUTING, action=action, tenant=tenant_id)
            try:
                res = await execute_booking_action(
                    action=action,
                    tenant_id=tenant_id,
                    name=filled.name,
                    phone=filled.phone,
                    date_str=date_str,
                    time_str=tag.time,
                    doctor_name=tag.doctor,
                    notes=tag.notes,
                    call_id=self._call_meta.get("call_record_id"),
                    source=SOURCE_VOICE,
                )
            except Exception as exc:
                logger.error("VoiceActionProcessor: %s failed: %s", action, exc, exc_info=True)
                res = {"success": False, "reason": "db_error", "appointment_id": None}

        trace(
            self._trace_id, VOICE, EXECUTED, action=action,
            ok=str(bool(res.get("success"))).lower(),
            appointment_id=res.get("appointment_id"), reason=res.get("reason"),
        )
        logger.info(
            "VoiceActionProcessor: %s -> success=%s reason=%s appointment_id=%s",
            action, res.get("success"), res.get("reason") or "-", res.get("appointment_id"),
        )

        if res.get("success") and res.get("reason") != "already_at_that_time":
            await self._mark_call_outcome(action)

        return res

    def _resolve_date(self, tag: ActionTag) -> str:
        """The day to actually book: the CALLER's word beats the model's arithmetic.

        Measured live 2026-08-12 — the caller said "कल दोपहर 3 बजे" with
        "Today is Wednesday, 12/08/2026" in the prompt, and the model wrote
        15/08/2026 into the tag. A real appointment was created three days out, and
        nothing downstream could have caught it: 15/08 was a valid future date and
        the doctor really was free at 3 PM on it.

        The days the caller named come from BookingProcessor via the shared
        ``call_meta`` dict; the reconciliation rules (and why they are deliberately
        conservative) are in services/dayref.py.
        """
        said = self._call_meta.get("said_dates") or []
        if not said:
            return tag.date
        try:
            from backend.services.dayref import reconcile_requested_date
            from backend.services.timeutil import ist_now

            date_str, correction = reconcile_requested_date(tag.date, said, ist_now().date())
            if correction:
                logger.warning(
                    "VoiceActionProcessor: OVERRULING the model's date — %s. Using %s.",
                    correction, date_str,
                )
                trace(self._trace_id, VOICE, "date_corrected",
                      model=tag.date or "-", used=date_str)
            return date_str
        except Exception as exc:
            logger.error("Date reconciliation failed, using the tag's date: %s", exc)
            return tag.date

    async def _mark_call_outcome(self, action: str) -> None:
        """Record the real outcome on the call record, so the dashboard's
        resolution rate and the All Calls status reflect it — the same fix
        booking_processor._mark_call_booked made for the FSM path. Best-effort:
        the caller's booking has already succeeded either way."""
        call_record_id = self._call_meta.get("call_record_id")
        if not call_record_id:
            return
        outcome = {"BOOK": "booked", "CANCEL": "cancelled", "RESCHEDULE": "rescheduled"}.get(
            (action or "").upper(), "resolved")
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
        except Exception as exc:
            logger.error("VoiceActionProcessor: failed to mark call %s %s: %s",
                         call_record_id, outcome, exc)

    # ── Context injection + LLM re-run ────────────────────────────────────────

    def _inject(self, message: str, *, rerun: bool = False) -> None:
        """Append an authoritative system line to the shared LLM context, and
        optionally owe the pipeline a re-run so the model speaks from it.

        Injection uses the same mechanism BookingProcessor does, and happens even
        when no re-run follows: a LATER turn ("is it done?") must be answered from
        the real outcome, not from the model's memory of its own words (booking
        rule 7).

        The re-run itself is deferred to ``_on_response_end``, which pushes an
        ``LLMRunFrame`` downstream. That frame reaches
        ``context_aggregator.assistant()`` at the tail of the pipeline, which
        pushes the updated context back UPSTREAM to the LLM — pipecat's own
        re-run path, so nothing here has to reach around the pipeline and poke
        the LLM service directly.
        """
        if rerun:
            message += _NO_SECOND_TAG
            self._pending_rerun = True
        if self._context is None:
            logger.error("VoiceActionProcessor: no LLM context — cannot inject %r", message[:60])
            return
        try:
            self._context.add_message({"role": "system", "content": message})
        except Exception as exc:
            logger.error("VoiceActionProcessor: failed to inject the booking result: %s", exc)

    # ── State ─────────────────────────────────────────────────────────────────

    def _reset_response(self) -> None:
        self._full = ""
        self._buf = ""
        self._holding = None
        self._dropping = False

    def reset_turn(self) -> None:
        """Start a fresh caller utterance: the once-per-turn caps lift.

        Called from BookingTranscriptTap, which sits upstream of
        ``context_aggregator.user()`` — the only place a finalised
        TranscriptionFrame still exists (the aggregator consumes it, pipecat
        1.5.0 llm_response_universal.py:794), and therefore the only place that
        can see a new utterance begin.
        """
        self._acted_this_turn = False
        self._repaired_this_turn = False
        self._pending_rerun = False
        self._next_is_followup = False
        self._is_followup = False

    async def _release_held(self, direction: FrameDirection) -> None:
        """Speak text that was held and turned out not to be a machine tag."""
        if not self._buf:
            return
        text, self._buf = self._buf, ""
        if text.strip():
            await self.push_frame(TextFrame(text), direction)
