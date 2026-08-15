"""
backend/agent/pipeline.py — Pipecat voice agent pipeline for Lifodial.

Architecture:
    LiveKit Room (caller)
        → LiveKitTransport.input()
        → SarvamSTTService          (transcription, Indian language support)
        → LLMContextAggregator      (builds message history for LLM)
        → BookingProcessor          (booking state machine, transparent)
        → GoogleLLMService          (Gemini 2.0 Flash, streaming)
        → LLMAssistantContextAggregator (stores assistant replies)
        → CallLoggerProcessor       (latency tracking, DB writes, transparent)
        → SarvamTTSService          (text-to-speech, Indian voices, streaming)
        → LiveKitTransport.output() (sends audio back to caller)

Key production guarantees:
  ✓ Sarvam STT + TTS — first-party Pipecat service (no custom HTTP wrappers)
  ✓ Silero VAD — barge-in / interruption detection
  ✓ BookingProcessor — multi-turn appointment booking state machine
  ✓ CallLoggerProcessor — call record DB writes + credit deduction (background tasks)
  ✓ Zero added latency — all DB writes are asyncio.create_task (fire-and-forget)
  ✓ All existing services (credit_service, his, call_evaluator) used unchanged
  ✓ All existing FastAPI routers untouched

Entrypoint: run `python -m backend.agent.pipeline start`
This boots a LiveKit agent worker that connects to your LiveKit cloud project
and handles inbound calls dispatched by the LiveKit SIP trunk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from loguru import logger as pipecat_logger

# ── Pipecat core ──────────────────────────────────────────────────────────────
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from dataclasses import replace

from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSModel, SarvamTTSService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

# Deepgram lazy import (requires deepgram-sdk; guarded so missing dep doesn't
# crash the worker if Deepgram is not the configured provider)
def _import_deepgram_stt():
    from pipecat.services.deepgram.stt import DeepgramSTTService
    return DeepgramSTTService

# ── Pipecat LiveKit token helper ──────────────────────────────────────────────
from livekit import api as livekit_api

# ── Local processors ──────────────────────────────────────────────────────────
from backend.agent.processors.booking_processor import BookingProcessor, BookingTranscriptTap
from backend.agent.processors.call_logger_processor import (
    CallLoggerProcessor,
    UserTranscriptTap,
)
from backend.agent.processors.language_switcher import LanguageSwitchProcessor
from backend.agent.processors.context_trim import ContextTrimProcessor
from backend.agent.processors.tag_scrub import TagScrubProcessor
from backend.agent.processors.voice_action import VoiceActionProcessor
from backend.agent.processors.transcript_publisher import LiveKitTranscriptPublisher

# ── App config ────────────────────────────────────────────────────────────────
from backend.config import settings

# Standard logger for non-pipecat code
log = logging.getLogger(__name__)


# ── Language → Sarvam code mapping ────────────────────────────────────────────
_LANG_TO_SARVAM: dict[str, str] = {
    "hi-IN": "hi-IN",
    "en-IN": "en-IN",
    "ta-IN": "ta-IN",
    "te-IN": "te-IN",
    "kn-IN": "kn-IN",
    "ml-IN": "ml-IN",
    "mr-IN": "mr-IN",
    "bn-IN": "bn-IN",
    "pa-IN": "pa-IN",
    "gu-IN": "gu-IN",
}


def _safe_lang(lang: str) -> str:
    """Return a Sarvam-supported language code, defaulting to hi-IN."""
    return _LANG_TO_SARVAM.get(lang, "hi-IN")


def _kb_context_block(tenant: dict) -> str:
    """Render the tenant's knowledge base as an appendable prompt block. Empty
    string when there are no entries (turn proceeds normally without KB)."""
    entries = tenant.get("knowledge_base") or []
    if not entries:
        return ""
    lines = [f"[{(e.get('category') or 'info').upper()}] {e.get('title','')}: {e.get('content','')}" for e in entries]
    return (
        "\n\n--- CLINIC KNOWLEDGE BASE ---\n"
        + "\n".join(lines)
        + "\n--- END KNOWLEDGE BASE ---\n"
        "Use the knowledge base above to answer clinic-specific questions. "
        "If it doesn't cover something, say you'll check with the clinic — never invent details."
    )


#: What a prompt TEMPLATE gets for {working_hours} when the clinic never set any.
#:
#: There is deliberately no _DEFAULT_WORKING_HOURS any more. It read
#: "9 AM – 7 PM, Mon–Sat" and was substituted wherever real hours were missing,
#: which meant an unconfigured clinic's agent stated invented opening times as
#: fact — and, because _clinic_facts_block pairs the hours with "say the clinic
#: is closed then", turned callers away on the strength of them. Reported
#: 2026-08-15 as the agent giving out wrong time slots.
#:
#: A template cannot omit an interpolated value the way the facts block can omit
#: a line, so it gets a phrase that is TRUE when hours are unknown.
_UNKNOWN_WORKING_HOURS = "not on file — do not state any opening hours"

# ── Deepgram language support ─────────────────────────────────────────────────
# Verified by live probes against the Deepgram API on 2026-07-28. Deepgram's own
# 400 body is explicit about the tier: "No such model/language/tier combination
# found. You could try the 'general' model (language: ta, Nova-3 tier)."
#
#   nova-3 + en/hi/ta/te/kn/mr/bn/gu -> 200
#   nova-3 + ml/pa                   -> 400 (unsupported by Deepgram entirely)
#   nova-2 + ta/te/kn/ml/mr/bn/pa/gu -> 400
#   nova-2 + hi/en-*                 -> 200
#
# Do NOT "fix" an Indic 400 by falling back to nova-2 — that is the combination
# Deepgram rejects, and it is how this shipped broken: the 400 is swallowed by
# pipecat's Deepgram _connection_handler (bare `except`, `while True`, no backoff),
# so the agent greets the caller and then loops forever without transcribing.
# Defined in backend/services/provider_registry.py, which imports no pipecat, so
# the API process can share these facts. It needs them too (the widget path picks
# a Deepgram language), and importing them from THIS module raised
# `No module named 'pipecat'` inside a live request handler.
from backend.services.provider_registry import (
    DEEPGRAM_LANG_MAP as _DG_LANG_MAP,
    DEEPGRAM_NOVA2_UNSUPPORTED_LANGS as _DG_NOVA2_UNSUPPORTED_LANGS,
    DEEPGRAM_NOVA3_MULTI_LANGS as _DG_NOVA3_MULTI_LANGS,
    DEEPGRAM_UNSUPPORTED_LANGS as _DG_UNSUPPORTED_LANGS,
)

# Which languages each STT provider can really transcribe, and how to spell our
# stored code in that provider's own API. Imports no pipecat, same as above, so
# the API process can serve the Transcriber Language dropdown from it.
from backend.services import agent_defaults, stt_catalog


# ── Sarvam STT model selection ────────────────────────────────────────────────

def _register_sarvam_v4_with_pipecat() -> bool:
    """Teach pipecat 1.5.0 how to build ``saaras:v4``. Returns True if registered.

    pipecat 1.5.0 ships MODEL_CONFIGS with exactly three keys — saarika:v2.5,
    saaras:v2.5, saaras:v3 — and its constructor does:

        if resolved_model not in MODEL_CONFIGS:
            raise ValueError(f"Unsupported model '{resolved_model}'...")

    So simply offering saaras:v4 in the dashboard would not degrade gracefully; it
    would raise at pipeline build time and the caller would hear dead air. It has
    to be registered here or not offered at all.

    Registering it is safe because v4 is wire-identical to v3 on the endpoint we
    use. Verified live on 2026-08-06 by transcribing the SAME Malayalam WAV with
    both models:

        saaras:v3  200  keys=['language_code','request_id','transcript']
        saaras:v4  200  keys=['language_code','request_id','transcript']
        both returned 'നാളെ രാവിലെ പത്തരയ്ക്ക് ഡോക്ടറെ കാണാൻ സമയം വേണം.' verbatim

    and both accept the same parameters (``language_code``, optional
    ``mode=transcribe``, optional ``sample_rate=16000``). The config below is
    therefore v3's, copied field for field — it is a statement that v4 has the same
    capabilities, which is what the probe showed.

    Deliberately NOT registered: ``saaras:v4-multispk`` and ``saaras:v3-realtime``.
    Sarvam's request validator lists them, but the transcribe endpoint itself
    answers "Invalid model 'saaras:v4-multispk'. Supported models: 'saarika:v2.5',
    'saaras:v3', 'saaras:v4'." Offering a model the endpoint rejects is how an
    agent gets saved into a configuration that cannot take a call.

    Remove this shim once pipecat ships a MODEL_CONFIGS entry of its own — the
    ``setdefault`` below means pipecat's version wins the moment it exists.
    """
    try:
        from pipecat.services.sarvam.stt import MODEL_CONFIGS

        v3 = MODEL_CONFIGS.get("saaras:v3")
        if v3 is None:  # pipecat restructured; do not guess
            return False
        MODEL_CONFIGS.setdefault("saaras:v4", replace(v3))
        return "saaras:v4" in MODEL_CONFIGS
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Could not register saaras:v4 with pipecat: %s", e)
        return False


_SARVAM_V4_AVAILABLE = _register_sarvam_v4_with_pipecat()

#: Models pipecat's SarvamSTTService knows how to build (its MODEL_CONFIGS keys),
#: including saaras:v4 once the shim above has registered it. If registration ever
#: fails, v4 drops out of this set and resolve_sarvam_stt_model falls back to the
#: default rather than raising inside the constructor.
SARVAM_STT_MODELS = frozenset(
    {"saarika:v2.5", "saaras:v2.5", "saaras:v3"} | ({"saaras:v4"} if _SARVAM_V4_AVAILABLE else set())
)

#: Retired model ids the dashboard may still have stored. Sarvam answers HTTP 400
#: for these ("Model 'saarika:v2' has been deprecated"), so they are upgraded
#: rather than treated as unknown. Mirrors the same table in
#: backend/routers/agent_test.py so the widget path and the live call path cannot
#: disagree about what "saaras:v2" means.
_SARVAM_STT_ALIASES = {
    "saarika:v1": "saarika:v2.5",
    "saarika:v2": "saarika:v2.5",
    "saaras:v1":  "saaras:v3",
    "saaras:v2":  "saaras:v3",
}

#: Transcribe, not translate — see resolve_sarvam_stt_model.
SARVAM_STT_DEFAULT = "saaras:v3"


def resolve_sarvam_stt_model(requested: str | None, *, room_name: str = "") -> str:
    """Pick the Sarvam STT model to build, defaulting to TRANSCRIBE not translate.

    ⚠️ saaras:v2.5 is Sarvam's speech-to-text-TRANSLATE model. Pipecat builds it
    against ``speech_to_text_translate_streaming`` and its output is ENGLISH text
    no matter what the caller spoke — verified against the live API on
    2026-07-29 with identical audio:

        caller says "नमस्ते, मुझे कल सुबह डॉक्टर से अपॉइंटमेंट चाहिए।"
        saaras:v2.5 → "Hello, I need an appointment with the doctor tomorrow morning."
        saaras:v3   → "नमस्ते, मुझे कल सुबह डॉक्टर से अपॉइंटमेंट चाहिए।"

    That is what the model is for, not a bug in it — but it is catastrophic for
    THIS product. The LLM only ever sees English, so the "reply in the caller's
    language" rule answers a Hindi caller in English, and LanguageSwitchProcessor
    (which detects the script of the transcript) can never see Devanagari and so
    never retunes the TTS voice.

    This used to coerce EVERY unrecognised model to exactly that model, including:
      * the "saaras:v2" default in _load_tenant_and_config, i.e. every agent that
        had never explicitly picked a model, and
      * a leftover Deepgram model id like "nova-3" after someone switched
        stt_provider from Deepgram back to Sarvam.

    So silent English-translation was the effective default. saaras:v3
    (transcribe, keeps the caller's language) is the right one.
    """
    requested = (requested or "").strip()
    model = _SARVAM_STT_ALIASES.get(requested, requested)

    if model not in SARVAM_STT_MODELS:
        if requested:
            log.info(
                "STT model %r is not a Sarvam model (probably left over from another "
                "provider) — using %s for room=%s",
                requested, SARVAM_STT_DEFAULT, room_name,
            )
        return SARVAM_STT_DEFAULT

    if model == "saaras:v2.5":
        log.warning(
            "STT model saaras:v2.5 is a TRANSLATE model — every caller utterance will "
            "reach the LLM as ENGLISH text regardless of the language spoken, so the "
            "agent will answer in English and mid-call language switching cannot work. "
            "Choose saaras:v3 (transcribe) unless English-only output is intended. "
            "room=%s", room_name,
        )
    return model


def _clinic_facts_block(tenant: dict) -> str:
    """Clinic hours + the full doctor roster, as a block appended to EVERY prompt.

    Why this is separate from the template's own {working_hours} / {doctors_list}
    placeholders: those are interpolated ONLY when the prompt comes from
    prompt_templates. A clinic with a custom system_prompt — which is precedence
    #1 in _build_system_prompt — got neither. So the two things a clinic admin can
    actually edit about their clinic (its timings, and its doctors) were invisible
    to the agent for exactly the clinics that had customised anything.

    Same reasoning as _doctor_availability_block below, which already had to solve
    this for on-leave doctors. This block carries the positive roster and the
    hours; that one carries the emphatic do-not-book warning for absences.

    The roster half is now redundant with the shared REAL DOCTOR AVAILABILITY
    block (services/availability_prompt.py), which lists the same names plus
    real open times — but it is kept, because that block is best-effort and this
    one is built from data already in memory. What this must NEVER do is assert
    the roster is empty when the roster could not be READ: see the
    _facts_unavailable branch.
    """
    # NOT defaulted to _DEFAULT_WORKING_HOURS. A clinic that has never filled in
    # its hours has no hours to state, and the old fallback turned that absence
    # into the confident sentence "Working hours: 9 AM - 7 PM, Mon-Sat" —
    # immediately followed by the instruction below to refuse anything outside
    # them and to "say the clinic is closed then". So an unconfigured clinic
    # had the agent quoting invented opening times as fact and turning callers
    # away on the strength of them.
    #
    # The honest source for what is actually bookable is the REAL DOCTOR
    # AVAILABILITY block (services/availability_prompt.py), which is computed
    # from the clinic's real DoctorAvailability rows by the same engine that
    # gates the write. When there are no configured clinic hours, say nothing
    # about hours and let that block do its job.
    hours = (tenant.get("working_hours") or "").strip()
    doctors = tenant.get("doctors") or []

    if tenant.get("_facts_unavailable"):
        # The clinic's data could not be loaded (see _load_tenant_and_config's
        # except branch). Saying "working hours are 9-7" and "no doctors have
        # been added" here would be two confident fabrications in three lines —
        # and that is the exact prompt that told callers at a three-doctor
        # clinic that no doctor information existed. Volunteer nothing.
        from backend.services.availability_prompt import lookup_failed_block

        return lookup_failed_block()

    lines = [f"Working hours: {hours}"] if hours else []
    if tenant.get("address"):
        lines.append(f"Address: {tenant['address']}")

    available = [d for d in doctors if d.get("is_available", True)]
    if available:
        lines.append("Doctors available to book:")
        lines += [
            f"  - {d['name']} ({d.get('specialization') or 'Specialist'})"
            for d in available
        ]
    elif doctors:
        lines.append(
            "No doctor is available right now — every doctor on staff is on leave."
        )
    else:
        # An empty roster is a real state for a new clinic. Say so explicitly, or
        # the model invents a doctor to be helpful.
        lines.append(
            "No doctors have been added to this clinic yet. You therefore CANNOT "
            "book an appointment with a named doctor — offer to take the caller's "
            "details so the clinic can call them back instead, and never invent a "
            "doctor's name."
        )

    if hours:
        hours_rule = (
            "Only ever offer or confirm appointment times that fall INSIDE the working "
            "hours above. If the caller asks for a time outside them, say the clinic is "
            "closed then and offer the nearest time that is open. "
        )
    else:
        # No hours on file. The model must not fill that silence — asked when the
        # clinic opens, it will happily produce a plausible answer, and a plausible
        # answer here is a wrong one told to a patient.
        hours_rule = (
            "This clinic has NOT told you its opening hours. You therefore do not know "
            "them: never state, guess, imply or agree to any opening or closing time, "
            "and never say the clinic is closed at a particular hour. Offer only the "
            "specific times listed in the REAL DOCTOR AVAILABILITY section — that "
            "section is built from the doctors' actual schedules and is the only thing "
            "you know about when appointments can happen. If the caller asks what time "
            "the clinic opens, say you can check which appointment times are free and "
            "then offer them. "
        )

    return (
        "\n\n--- CLINIC DETAILS ---\n"
        + "\n".join(lines)
        + "\n--- END CLINIC DETAILS ---\n"
        + hours_rule
        + "Never invent a doctor, a specialization, or an opening time that is not "
        "listed above.\n"
    )


def _doctor_availability_block(tenant: dict) -> str:
    """Doctors currently on leave, as an appendable prompt block. Empty string
    when everyone is available (turn proceeds normally). Appended alongside
    the KB block so this reaches the LLM regardless of whether the clinic uses
    a custom prompt, a template, or the hardcoded fallback — the template's own
    doctors_list placeholder (below) is template-only and does NOT cover a
    custom prompt, so this is the one place that reaches every prompt path."""
    unavailable = [d for d in (tenant.get("doctors") or []) if not d.get("is_available", True)]
    if not unavailable:
        return ""
    lines = [
        f"- {d['name']} ({d.get('specialization', 'Specialist')}) is ON LEAVE"
        + (f" — {d['leave_reason']}" if d.get("leave_reason") else "")
        for d in unavailable
    ]
    return (
        "\n\n--- DOCTOR AVAILABILITY ---\n"
        + "\n".join(lines)
        + "\n--- END DOCTOR AVAILABILITY ---\n"
        "Any doctor listed above is NOT available right now. If the caller asks for one of "
        "them by name or specialization, tell them clearly that the doctor is on leave "
        "(mention the reason if one is given) — do not offer to book that doctor. Instead, "
        "offer another available doctor with the same specialization if one exists, or ask "
        "the caller if they'd like to be notified when the doctor is back."
    )


# Appended to EVERY system prompt (custom, template, or fallback). This is the
# honesty contract that pairs with BookingProcessor._commit_and_inject_result:
# the DB write is awaited and its outcome arrives as a [BOOKING_RESULT] system
# message BEFORE the LLM generates — so the model must never claim success on
# its own (audit FIX 4). Defined once in backend/agent/booking_rules.py and
# shared with the chat/embed path (agent_test.py) so the two implementations
# cannot drift apart again.
from backend.agent.booking_rules import BOOKING_RULES_BLOCK as _BOOKING_RULES_BLOCK
from backend.agent.booking_rules import voice_action_tag_block as _voice_action_tag_block
from backend.services.timeutil import ist_now as _ist_now


# Also appended to EVERY system prompt. The blocks above each forbid inventing one
# SPECIFIC class of fact — a doctor, an opening time, a booking confirmation, a KB
# answer — because each was added in response to a specific incident. That leaves a
# gap by construction: anything not on the list (a price, a phone number, a
# department, whether the clinic accepts an insurer, whether parking exists) had no
# rule at all, and a helpful model fills those in.
#
# The instruction to say "I don't have that information" is the important half.
# "Don't invent" on its own leaves the model with no sanctioned way to answer, and
# a model with no permitted answer tends to guess anyway.
_NO_FABRICATION_RULE = (
    "\n\n--- ONLY SAY WHAT YOU KNOW ---\n"
    "Every fact you state about this clinic must come from the CLINIC DETAILS, "
    "DOCTOR AVAILABILITY or CLINIC KNOWLEDGE BASE sections above, or from something "
    "the caller told you in this conversation. That includes doctor names, "
    "specializations, timings, prices, fees, phone numbers, addresses, departments, "
    "services, insurance or payment options, and waiting times.\n"
    "If you are asked something those sections do not answer, say plainly that you "
    "do not have that information and offer to take the caller's details so the "
    "clinic can call them back. A short honest answer is always better than a "
    "plausible guess — never fill a gap with something that sounds reasonable, and "
    "never state a detail merely because it is typical of clinics generally.\n"
)


# VOICE ONLY. Appended here in pipeline.py and deliberately not in
# booking_rules.py, because the chat channel and the embed widget render
# Markdown and a numbered list is genuinely useful there.
#
# Measured on a live Hindi call 2026-08-13: the agent opened by SPEAKING its four
# intake questions as a list — "1. आपका पूरा नाम क्या है? 2. ... 3. ... 4. ..." —
# which is unbearable to listen to and asks a caller to hold four questions in
# their head at once. The model was not misbehaving; it was pattern-matching its
# instructions. Every block we append to the voice prompt is itself an ordered
# numbered list (BOOKING_RULES_BLOCK's rules 1-8, the per-template "BOOKING FLOW"
# steps in prompt_templates.py), and nothing anywhere told it that its OUTPUT is
# spoken aloud rather than rendered. Note the templates DO say "ask only ONE
# question at a time" — so the instruction existed and lost to the surrounding
# format. Stating the channel explicitly is what makes it stick.
#
# The rules below are about SHAPE only. Nothing here may relax the booking
# honesty contract or the [ACTION:] tag rules — a tag-only reply is not prose and
# is still exactly correct.
_VOICE_STYLE_RULE = (
    "\n\n--- YOU ARE SPEAKING OUT LOUD ---\n"
    "Everything you write is read to the caller by a text-to-speech voice over a "
    "phone line. They HEAR it; they cannot see it, scroll back, or re-read it.\n"
    "Never use a numbered or bulleted list. Never say or write '1.', '2.', "
    "'first, second, third' as a way of listing questions. Speak the way a human "
    "receptionist speaks on the phone: ordinary sentences.\n"
    "Ask for ONE thing at a time and then STOP and wait for the answer. A caller "
    "cannot answer four questions at once, and a reply that asks four is a reply "
    "they will get wrong. If you need several details, collect them over several "
    "short turns — that is faster in practice than one long question.\n"
    "Keep each reply to about two short sentences. No headings, no asterisks, no "
    "bullet characters, no emoji, no formatting marks of any kind: they are read "
    "aloud literally and sound like noise.\n"
    "When you must offer a few choices, such as open appointment times, say them "
    "in one flowing sentence — 'I have eleven in the morning, or two or four in "
    "the afternoon' — not as a list.\n"
    "This rule governs the SHAPE of your speech only. It never overrides the "
    "booking rules above: when it is time to act, your entire reply is still the "
    "[ACTION: ...] tag alone, with no words around it.\n"
)


def _build_system_prompt(
    agent_config: dict, tenant: dict, availability_block: str = "",
) -> str:
    """
    Build the LLM system prompt from stored config, or render from template,
    then append the clinic knowledge base (if any) and the booking honesty
    rules (always).

    Precedence:
      1. agent_config['system_prompt'] — custom prompt set by clinic admin
      2. Rendered prompt_templates entry for agent_config['template']
      3. Hardcoded fallback

    ``availability_block`` is the shared real-roster-and-real-slots block from
    services/availability_prompt.py — the SAME text the chat channel builds for
    the same clinic. Passed in rather than fetched here because building it is
    async and does DB work: the caller does it during call setup, off the audio
    hot path. BookingProcessor refreshes it mid-call when the caller raises a day
    it does not cover.
    """
    # The LLM is the third consumer of THE one language field (STT and TTS are the
    # other two). Naming the configured language explicitly is what makes the
    # single source of truth reach the actual words: previously the prompt only
    # said "mirror the caller", so an agent configured for Malayalam would happily
    # hold the whole call in English if the caller opened in English — the
    # configured language had no influence on the LLM at all.
    #
    # Still paired with LanguageSwitchProcessor, which retunes STT/TTS when the
    # caller genuinely switches. So the rule is: default to the configured
    # language, but follow the caller when they clearly choose another one.
    # Appended to every prompt path (custom, template, fallback) so a clinic's own
    # prompt cannot lose it.
    _configured = agent_config.get("language") or agent_defaults.DEFAULT_LANGUAGE
    _lang_name = agent_defaults.language_name(_configured)
    #
    # The REQUEST rule below is not a refinement of the mirror rule — it is a
    # carve-out from it, and the mirror rule without it was an active bug. "Always
    # reply in the SAME language the caller used in their most recent message",
    # applied to a caller asking *in Hindi* "Aap English mein baat kar sakte ho
    # kya?", instructs the model to answer that Hindi question in Hindi. A real
    # transcript from this project shows exactly that: the request was ignored and
    # the call continued in Hindi. The model was following its instructions.
    #
    # Paired with LanguageSwitchProcessor.detect_language_request, which retunes the
    # voice for the same utterance. Both halves are needed: the processor alone
    # would give English audio of Hindi words, and this rule alone would give
    # English words in a voice still tuned for Hindi.
    _LANGUAGE_MIRROR_RULE = (
        "\n\n--- LANGUAGE ---\n"
        f"This agent is configured for {_lang_name} ({_configured}). Speak "
        f"{_lang_name} by default, including your very first message.\n"
        "If the caller clearly speaks a different language, switch to THAT language "
        "and continue in it for as long as they use it. Never announce the switch or "
        "comment on which language is being spoken — just answer in it.\n"
        f"Never reply in English merely because a few English words appear in "
        f"{_lang_name} speech — Indian callers code-switch constantly, and that is "
        f"still {_lang_name}.\n"
        "IF THE CALLER ASKS YOU TO SPEAK A PARTICULAR LANGUAGE — for example "
        "\"can you speak English?\", \"Aap English mein baat kar sakte ho kya?\", or "
        "the same question in any other language — that request OVERRIDES every rule "
        "above. Switch to the language they asked for immediately, acknowledge it in "
        "one short sentence in that new language, and stay in it for the rest of the "
        "call. Do this even though their request itself was made in a different "
        "language: answer the request, do not mirror the language it was asked in. "
        "If you genuinely cannot speak the language they asked for, say so plainly "
        "in one sentence and continue in the current language — never ignore the "
        "question.\n"
    )

    kb_block = (
        _kb_context_block(tenant)
        # Hours + roster BEFORE the availability warning, so the model reads "who
        # exists and when we're open" and then "who of those is away".
        + _clinic_facts_block(tenant)
        + _doctor_availability_block(tenant)
        # Real names + real open times, from the shared builder the chat channel
        # uses. Placed after the roster so the concrete times are the last thing
        # the model reads about availability.
        + (availability_block or "")
        + _BOOKING_RULES_BLOCK
        # HOW to actually perform an action, as opposed to how to talk about one.
        # Appended to every prompt shape (custom, template, fallback) for the same
        # reason the honesty rules are: a clinic that writes its own prompt must
        # not thereby lose the only mechanism that writes to the appointment book.
        # The anchor date is the clinic's own IST today — the tag's Date field is
        # required to be a real DD/MM/YYYY.
        + _voice_action_tag_block(_ist_now().strftime("%A, %d/%m/%Y"))
        + _NO_FABRICATION_RULE
        + _VOICE_STYLE_RULE
        + _LANGUAGE_MIRROR_RULE
    )

    custom_prompt = (agent_config.get("system_prompt") or "").strip()
    if custom_prompt:
        return custom_prompt + kb_block

    # Try template render
    try:
        from backend.agent.prompt_templates import get_template, render_prompt

        lang = agent_config.get("language") or agent_defaults.DEFAULT_LANGUAGE
        template_key = agent_config.get("template", "clinic_receptionist")
        tmpl = get_template(template_key, lang)

        doctors = tenant.get("doctors", [])
        doctors_list = "\n".join(
            f"- {d['name']} ({d.get('specialization', 'Specialist')})"
            + ("" if d.get("is_available", True) else " — ON LEAVE, do not book")
            for d in doctors
        ) or (
            # This used to fall back to "- General Physician available", which
            # FABRICATED a doctor. Two things were wrong with it:
            #
            #   * it is not true. Both live clinics have an empty roster, so every
            #     templated prompt was telling the model a General Physician was
            #     bookable. A caller asking "who can I see?" gets an invented answer,
            #     and the booking then fails at doctor lookup — after the caller has
            #     been told someone is available.
            #   * it CONTRADICTED _clinic_facts_block, which is appended to the same
            #     prompt and says "No doctors have been added to this clinic yet …
            #     never invent a doctor's name." The model was handed both statements
            #     and had to pick.
            #
            # The honest rendering of an empty roster is that it is empty — but
            # ONLY when we actually know it is empty. A failed read is a
            # different statement (see _clinic_facts_block's _facts_unavailable
            # branch, which carries the full instruction).
            "- (the doctor list could not be loaded right now — say you cannot look "
            "it up at the moment; do NOT say the clinic has no doctors)"
            if tenant.get("_facts_unavailable") else
            "- (none yet — no doctors have been added to this clinic, so you cannot "
            "book with a named doctor)"
        )

        rendered = render_prompt(
            tmpl["system_prompt"],
            {
                "clinic_name": tenant.get("clinic_name", "the clinic"),
                "agent_name": agent_config.get("agent_name", "Receptionist"),
                "clinic_location": tenant.get("location", "India"),
                # The templates interpolate {working_hours} into a sentence, so
                # unlike the facts block this one cannot simply omit the line.
                # It gets an honest placeholder instead of invented hours — the
                # binding rule the model follows is the one in _clinic_facts_block,
                # which for an unconfigured clinic forbids stating any hours at all.
                "working_hours": (
                    tenant.get("working_hours") or _UNKNOWN_WORKING_HOURS
                ),
                "emergency_number": tenant.get("emergency_number", "108"),
                "doctors_list": doctors_list,
            },
        )
        return rendered + kb_block

    except Exception as exc:
        log.warning("Template render failed, using fallback prompt: %s", exc)

    # Hardcoded fallback
    return (
        f"You are {agent_config.get('agent_name', 'Receptionist')}, "
        f"the AI voice receptionist for {tenant.get('clinic_name', 'the clinic')}. "
        "Be concise, professional, and helpful. Maximum 2 sentences per response. "
        "Never give medical advice."
    ) + kb_block


def _generate_agent_token(room_name: str) -> str:
    """
    Generate a LiveKit access token for the Pipecat agent to join a room.

    NOTE: agent=True in VideoGrants does NOT make LiveKit report this participant
    with kind=AGENT — verified empirically against LiveKit Cloud (livekit 1.1.3):
    a token carrying the agent grant still joins as kind=0/STANDARD. Only the
    livekit-agents job participant (from ctx.connect()) gets kind=AGENT, and it
    publishes no tracks. That is why the frontend cannot rely on
    useVoiceAssistant/kind to find the speaking agent and instead resolves it from
    the subscribed audio track's participant (see TestVoiceCallLK.tsx). The grant
    is kept because it is harmless and semantically correct.
    """
    token = livekit_api.AccessToken(
        settings.livekit_api_key,
        settings.livekit_api_secret,
    )
    token.with_identity(f"lifodial-agent-{uuid.uuid4().hex[:6]}")
    token.with_name("Lifodial AI Agent")
    token.with_grants(
        livekit_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            agent=True,  # REQUIRED: marks this as the AI agent for useVoiceAssistant
        )
    )
    return token.to_jwt()


@asynccontextmanager
async def _session_or(db):
    """Yield `db` when the caller already has a session, else open a fresh one.

    Lets the whole call-setup path share ONE Supabase connection without every
    helper needing two code paths. See _load_tenant_and_config's `db` docstring
    for the measured cost of not doing this.
    """
    if db is not None:
        yield db
        return
    from backend.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


async def _load_tenant_and_config(
    tenant_id: Optional[str],
    agent_id: Optional[str],
    metadata: dict,
    db=None,
) -> tuple[dict, dict]:
    """
    Load agent config and tenant data from DB.

    Falls back to metadata defaults if DB is unavailable (graceful degradation).

    Args:
        db: An already-open AsyncSession to reuse. Pass one whenever the caller
            has more DB work to do in the same setup — backend/db.py configures
            the Postgres engine with NullPool (Supabase manages pooling), so
            every `async with AsyncSessionLocal()` is a fresh TCP+TLS+auth
            handshake to Supabase. On the agent worker that is not a rounding
            error: measured on a live call (room=testcall-72cb61a4-0dfb0a02,
            2026-07-29 09:12 UTC) the four separate sessions this call path used
            to open — config, call record, STT keys, TTS keys — cost 2.9s, 1.1s,
            1.0s and 1.5s respectively, i.e. ~6.5 of the 13.2 seconds the caller
            spent listening to nothing before the agent joined the room.
            None (the default) opens and closes one, as before.

    Returns:
        (agent_config dict, tenant dict)
    """
    agent_config: dict = {
        "agent_name":      metadata.get("agent_name", "Receptionist"),
        "first_message":   metadata.get("first_message", ""),
        "first_message_mode": metadata.get("first_message_mode", "assistant-speaks-first"),
        "system_prompt":   metadata.get("system_prompt", ""),
        "template":        metadata.get("template", "clinic_receptionist"),
        # THE one language. Everything language-related below derives from it.
        # Falls back through the legacy mirrors so a room dispatched by an older
        # backend revision (whose metadata predates this key) still works.
        "language":        (
            metadata.get("language")
            or metadata.get("tts_language")
            or agent_defaults.DEFAULT_LANGUAGE
        ),
        "stt_provider":    metadata.get("stt_provider", "sarvam"),
        "tts_provider":    metadata.get("tts_provider", "sarvam"),
        "tts_voice":       metadata.get("tts_voice", "priya"),
        "tts_language":    metadata.get("tts_language", "hi-IN"),
        "tts_model":       metadata.get("tts_model", "bulbul:v3"),
        "tts_pace":        float(metadata.get("tts_pace", 1.05)),
        "tts_pitch":       float(metadata.get("tts_pitch", 0.0) or 0.0),
        "tts_loudness":    float(metadata.get("tts_loudness", 1.0) or 1.0),
        "tts_input_preprocessing": bool(metadata.get("tts_input_preprocessing", True)),
        "tts_stability":   metadata.get("tts_stability"),
        "tts_clarity":     metadata.get("tts_clarity"),
        "tts_style":       metadata.get("tts_style"),
        "tts_use_speaker_boost": bool(metadata.get("tts_use_speaker_boost", False)),
        "tts_speed":       metadata.get("tts_speed"),
        "stt_model":       metadata.get("stt_model", "saaras:v2"),
        "stt_language":    metadata.get("stt_language", "hi-IN"),
        # "gemini-2.0-flash" was RETIRED — it 404s on generateContent while still
        # appearing in ListModels (verified 2026-08-13). See
        # resilience.PROVIDER_DEFAULT_MODEL for why this is an alias, not a pin.
        "llm_model":       metadata.get("llm_model", agent_defaults.DEFAULT_LLM_MODEL),
        # The agent's EXPLICIT LLM provider choice. Without this key,
        # resilience.select_llm_provider() only ever saw "" and had to guess the
        # provider from the model-name prefix (_provider_from_model), so choosing
        # Anthropic/Mistral/Ollama or a custom OpenAI-compatible endpoint silently
        # ran Gemini instead, and a Cerebras "llama-*" model got routed to Groq.
        # The dashboard's text-test path already read agent.llm_provider, so the
        # test chat and the real voice call disagreed about which model was running.
        "llm_provider":    (metadata.get("llm_provider") or "").strip(),
        "llm_temperature": float(metadata.get("llm_temperature", 0.3)),
        "max_response_tokens": int(metadata.get("max_response_tokens", 120)),
        # ── Tool toggles (Tools tab) ──────────────────────────────────────
        "can_book_appointments":   bool(metadata.get("can_book_appointments", True)),
        "can_cancel_appointments": bool(metadata.get("can_cancel_appointments", True)),
        "can_check_availability":  bool(metadata.get("can_check_availability", True)),
        "can_transfer_emergency":  bool(metadata.get("can_transfer_emergency", True)),
        "emergency_transfer_number": metadata.get("emergency_transfer_number"),
        # ── Post-call analysis toggles (Analysis tab) ──────────────────────
        "summary_enabled":            bool(metadata.get("summary_enabled", True)),
        "success_evaluation_enabled": bool(metadata.get("success_evaluation_enabled", True)),
        "structured_output_enabled":  bool(metadata.get("structured_output_enabled", False)),
        # ── Call Behavior ───────────────────────────────────────────────────
        "silence_timeout_seconds": int(metadata.get("silence_timeout_seconds", 10) or 10),
        "max_duration_seconds":    int(metadata.get("max_duration_seconds", 300) or 300),
        "end_call_phrases":        metadata.get("end_call_phrases") or [],
        # None (not the hardcoded English phrase) when the clinic hasn't set
        # one — resolved to a language-appropriate default just before return.
        "end_call_message":        metadata.get("end_call_message"),
        "recording_consent_plan":  metadata.get("recording_consent_plan", "none"),
        # No real agent_id (ad-hoc/metadata-only test room) => nothing to
        # unpublish, so default to allowed. Overwritten below when a real
        # AgentConfig row is loaded.
        "status": "ACTIVE",
    }

    tenant: dict = {
        "id":            tenant_id or "",
        "clinic_name":   metadata.get("clinic_name", "Clinic"),
        # Empty, not "9 AM – 7 PM, Mon–Sat". This literal is the seed for a room
        # that carries no tenant at all, so there is nothing behind it — stating
        # it would be inventing a clinic's opening hours out of nothing. The DB
        # branch below overwrites this with the real value when there is one.
        "working_hours": "",
        "doctors":       [],
        "knowledge_base": [],
    }

    if not tenant_id and not agent_id:
        log.warning("No tenant_id or agent_id in room metadata — using defaults.")
        if not agent_config.get("end_call_message"):
            from backend.agent.resilience import default_end_call_message
            agent_config["end_call_message"] = default_end_call_message(agent_config["language"])
        return agent_config, tenant

    try:
        from backend.models.agent_config import AgentConfig
        from backend.models.tenant import Tenant
        from sqlalchemy import select

        async with _session_or(db) as db:
            # Load AgentConfig
            if agent_id:
                result = await db.execute(
                    select(AgentConfig).where(AgentConfig.id == agent_id)
                )
                cfg = result.scalar_one_or_none()
                if cfg:
                    agent_config.update({
                        "agent_name":          cfg.agent_name or "Receptionist",
                        "first_message":       cfg.first_message or "",
                        "first_message_mode":  getattr(cfg, "first_message_mode", "assistant-speaks-first") or "assistant-speaks-first",
                        "system_prompt":       cfg.system_prompt or "",
                        "template":            getattr(cfg, "template", "clinic_receptionist"),
                        # THE one language — see agent_defaults. getattr-guarded so an
                        # agent worker running this revision against a database that has
                        # not had the migration applied yet still resolves a language
                        # from the legacy mirror instead of crashing the job.
                        "language":            agent_defaults.resolve_language(
                            language=getattr(cfg, "language", None),
                            tts_language=cfg.tts_language,
                            stt_language=cfg.stt_language,
                        )[0],
                        "stt_provider":        getattr(cfg, "stt_provider", "sarvam") or "sarvam",
                        "tts_provider":        getattr(cfg, "tts_provider", "sarvam") or "sarvam",
                        "tts_voice":           cfg.tts_voice or "priya",
                        "tts_language":        cfg.tts_language or "hi-IN",
                        "tts_model":           cfg.tts_model or "bulbul:v3",
                        "tts_pace":            float(cfg.tts_pace or 1.05),
                        "tts_pitch":           float(cfg.tts_pitch if cfg.tts_pitch is not None else 0.0),
                        "tts_loudness":        float(cfg.tts_loudness if cfg.tts_loudness is not None else 1.0),
                        "tts_input_preprocessing": bool(cfg.tts_input_preprocessing if cfg.tts_input_preprocessing is not None else True),
                        "tts_stability":       cfg.tts_stability,
                        "tts_clarity":         cfg.tts_clarity,
                        "tts_style":           cfg.tts_style,
                        "tts_use_speaker_boost": bool(cfg.tts_use_speaker_boost or False),
                        "tts_speed":           cfg.tts_speed,
                        "stt_model":           cfg.stt_model or "saaras:v2",
                        "stt_language":        cfg.stt_language or "hi-IN",
                        # Retired model — see the note at the other default above.
                        "llm_model":           cfg.llm_model or agent_defaults.DEFAULT_LLM_MODEL,
                        # See the note on "llm_provider" in the metadata-only branch
                        # above — the AgentConfig row has always had this column, the
                        # pipeline just never read it, so the provider was inferred
                        # from the model name and any non-inferable choice ran Gemini.
                        "llm_provider":        (getattr(cfg, "llm_provider", "") or "").strip(),
                        "llm_temperature":     float(cfg.llm_temperature or 0.3),
                        "max_response_tokens": int(cfg.max_response_tokens or 120),
                        "can_book_appointments":   bool(cfg.can_book_appointments if cfg.can_book_appointments is not None else True),
                        "can_cancel_appointments": bool(cfg.can_cancel_appointments if cfg.can_cancel_appointments is not None else True),
                        "can_check_availability":  bool(cfg.can_check_availability if cfg.can_check_availability is not None else True),
                        "can_transfer_emergency":  bool(cfg.can_transfer_emergency if cfg.can_transfer_emergency is not None else True),
                        "emergency_transfer_number": cfg.emergency_transfer_number,
                        "summary_enabled":            bool(cfg.summary_enabled if cfg.summary_enabled is not None else True),
                        "success_evaluation_enabled": bool(cfg.success_evaluation_enabled if cfg.success_evaluation_enabled is not None else True),
                        "structured_output_enabled":  bool(cfg.structured_output_enabled or False),
                        "silence_timeout_seconds": int(cfg.silence_timeout_seconds or 10),
                        "max_duration_seconds":    int(cfg.max_duration_seconds or 300),
                        "end_call_phrases":        cfg.end_call_phrases or [],
                        "end_call_message":        cfg.end_call_message or None,
                        "recording_consent_plan":  getattr(cfg, "recording_consent_plan", None) or "none",
                        # Clinic-owned facts the receptionist must know: working
                        # hours, address, emergency number, services, FAQs. This is
                        # what Settings -> Clinic Profile writes its calling hours
                        # into, and it is the ONLY place those hours are stored —
                        # Tenant has no working_hours column (see below).
                        "clinic_info":         cfg.clinic_info if isinstance(cfg.clinic_info, dict) else {},
                        "status":              cfg.status,
                    })
                    log.info("AgentConfig loaded from DB: agent_id=%s", agent_id)

            # Load Tenant + Doctors
            if tenant_id:
                t_result = await db.execute(
                    select(Tenant).where(Tenant.id == tenant_id)
                )
                t = t_result.scalar_one_or_none()
                if t:
                    tenant["id"]            = str(t.id)
                    tenant["clinic_name"]   = t.clinic_name
                    tenant["location"]      = getattr(t, "location", "India")

                    # Working hours come from AgentConfig.clinic_info, which is where
                    # Settings -> Clinic Profile saves them.
                    #
                    # This used to read getattr(t, "working_hours", "9 AM – 7 PM, Mon–Sat")
                    # — but Tenant has NO working_hours column, so getattr always fell
                    # through to that default. Every clinic's agent therefore believed it
                    # opened 9-7 Mon-Sat no matter what the clinic had configured, and a
                    # clinic that set 10:00-18:00 had an agent happily offering 9 AM.
                    #
                    # Left EMPTY when the clinic has not set them, rather than
                    # defaulted: _clinic_facts_block now omits the hours line
                    # entirely in that case and forbids the model from inventing
                    # one. Defaulting here would put the fabrication back one
                    # layer down, where the block can no longer tell a real
                    # "9 AM - 7 PM" from a made-up one.
                    _ci = agent_config.get("clinic_info") or {}
                    tenant["working_hours"] = (_ci.get("working_hours") or "").strip()
                    # Same source for the other clinic facts the prompt interpolates.
                    if (_ci.get("emergency_number") or "").strip():
                        tenant["emergency_number"] = _ci["emergency_number"].strip()
                    if (_ci.get("address") or "").strip():
                        tenant["address"] = _ci["address"].strip()

                # THE doctor lookup — services/his.py::get_doctors, the exact
                # function the chat/embed channel uses. This used to be a
                # private Doctor query right here, which is the second half of
                # why the two channels answered "which doctors do you have?"
                # differently: even once both could read the DB, only one of
                # them went through the cached, shared accessor.
                from backend.services.his import get_doctors

                tenant["doctors"] = await get_doctors(tenant_id)
                log.info(
                    "Tenant loaded from DB: %s (%d doctors)",
                    tenant["clinic_name"], len(tenant["doctors"]),
                )

                # Knowledge base entries (same source the WS/embed path already
                # injects) — so the pipecat pipeline is KB-aware too.
                try:
                    from backend.models.knowledge_base import KnowledgeBase
                    kb_result = await db.execute(
                        select(KnowledgeBase).where(
                            KnowledgeBase.tenant_id == tenant_id,
                            KnowledgeBase.is_active == True,  # noqa: E712
                        )
                    )
                    tenant["knowledge_base"] = [
                        {"category": e.category, "title": e.title, "content": e.content}
                        for e in kb_result.scalars().all()
                    ]
                    log.info("Knowledge base loaded: %d entries", len(tenant["knowledge_base"]))
                except Exception as kb_exc:
                    log.warning("Knowledge base load failed (non-fatal): %s", kb_exc)

    except Exception as exc:
        # CRITICAL, not warning: reaching here means this call runs with no
        # clinic data at all — no doctors, no configured working hours, no
        # knowledge base. It is not a degraded call, it is a blind one.
        log.critical(
            "DB load FAILED for tenant=%s agent=%s — this call has NO doctor roster, "
            "NO configured working hours and NO knowledge base, and the agent will "
            "have to tell the caller it cannot look their details up. Error: %s: %s",
            tenant_id, agent_id, type(exc).__name__, exc, exc_info=True,
        )
        # The prompt builders MUST be able to tell "this clinic has no doctors"
        # (a real state for a new clinic — say so plainly) apart from "we could
        # not read the clinic's doctors" (never say anything about the roster).
        # Without this flag both look like an empty list, and the second one had
        # the agent confidently informing callers at a clinic with three
        # cardiologists that no doctor information existed.
        tenant["_facts_unavailable"] = True
        # The session now belongs to the CALLER (see the `db` arg), and a failed
        # statement leaves SQLAlchemy's transaction needing a rollback — every
        # later statement on it would raise PendingRollbackError. When we owned
        # the session that was harmless (it was thrown away); sharing it means a
        # single bad read here would otherwise take the credit gate, the key
        # lookups and the CallRecord down with it. Roll back so the rest of call
        # setup still runs on a usable connection.
        if db is not None:
            try:
                await db.rollback()
            except Exception as rb_exc:
                log.warning("Rollback after failed config load also failed: %s", rb_exc)

    # Only a bare TTSSpeakFrame (see speak_and_end_call in
    # call_logger_processor.py) — it never passes through the LLM, so unlike
    # every other reply it cannot pick up the call's language on its own.
    # Resolved here, after "language" has settled to its final value above, so
    # a clinic that never configured a custom end_call_message still gets one
    # spoken in the call's actual language instead of a hardcoded English
    # phrase every caller heard regardless of what language the call was in.
    if not agent_config.get("end_call_message"):
        from backend.agent.resilience import default_end_call_message
        agent_config["end_call_message"] = default_end_call_message(agent_config["language"])

    return agent_config, tenant


async def _resolve_provider_keys(
    db, stt_provider: str, tts_provider: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve every API key this call could need, on ONE session.

    Returns ``(stt_keys, tts_keys)`` — dicts keyed by provider id, always
    populated with "" rather than missing, so the build chains below can use
    ``.get()``/``[]`` without a KeyError killing the job before the agent joins
    (that exact KeyError — a missing "sarvam" entry behind the TTS `else:` — is
    why the fallback provider is always resolved, whichever one is selected).

    Only the providers that can actually be read are resolved: the selected one,
    the Sarvam fallback both chains end in, and the OpenAI key for the ids that
    alias to it.
    """
    from backend.agent import providers as provider_registry

    stt_needed = {stt_provider, "sarvam"}
    if stt_provider in ("openai", "whisper"):
        stt_needed.add("openai")

    tts_needed = {tts_provider, "sarvam"}
    if tts_provider == "openai_tts":
        tts_needed.add("openai")

    stt_keys = {
        p: await provider_registry.resolve_key(db, p, category="stt") for p in stt_needed
    }
    tts_keys = {
        p: await provider_registry.resolve_key(db, p, category="tts") for p in tts_needed
    }
    return stt_keys, tts_keys


async def _create_call_record(
    tenant_id: Optional[str],
    agent_id: Optional[str],
    call_meta: dict,
    db=None,
) -> Optional[str]:
    """
    Create a CallRecord row at call start and return its UUID.

    Returns None if DB write fails (call continues regardless).
    """
    if not tenant_id:
        return None

    try:
        from datetime import datetime, timezone

        from backend.models.call_record import CallRecord

        call_id = call_meta.get("call_record_id") or str(uuid.uuid4())
        call_meta["call_record_id"] = call_id
        async with _session_or(db) as db:
            record = CallRecord(
                id=call_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                call_type=call_meta.get("call_type", "inbound"),
                patient_number_masked=_mask_phone(
                    call_meta.get("caller_phone", "unknown")
                ),
                started_at=datetime.now(timezone.utc),
                status="active",
                turn_count=0,
                transcript=[],
            )
            db.add(record)
            await db.commit()

        log.info("CallRecord created: id=%s", call_id)
        return call_id

    except Exception as exc:
        log.error("Failed to create CallRecord: %s", exc, exc_info=True)
        # Same reasoning as the rollback in _load_tenant_and_config: a caller-owned
        # session must be left usable for whatever runs after this.
        if db is not None:
            try:
                await db.rollback()
            except Exception as rb_exc:
                log.warning("Rollback after failed CallRecord insert also failed: %s", rb_exc)
        return None


def _mask_phone(phone: str) -> str:
    """Mask phone number for HIPAA-style PII reduction: +91XXXXXXX3456."""
    if not phone or phone == "unknown":
        return "unknown"
    if len(phone) > 4:
        return phone[:-4].replace(phone[2:-6], "X" * max(len(phone) - 6, 0)) + phone[-4:]
    return phone


# ── Main entrypoint ───────────────────────────────────────────────────────────

async def entrypoint(ctx) -> None:
    """
    LiveKit agent entrypoint.

    Called once per incoming call by the livekit-agents worker.
    Builds the Pipecat pipeline and runs it until the call ends.

    ctx: livekit.agents.JobContext
    """
    # ── Parse call metadata ─────────────────────────────────────────────────
    # Prefer room metadata (the web-call flow sets it at create_room). Fall back
    # to the job's dispatch metadata, which is where an explicit agent dispatch
    # (SIP inbound, or a programmatic create_dispatch) carries it. Without this
    # fallback, explicit-dispatch jobs saw tenant/agent = None and ran on
    # defaults.
    # ORDER MATTERS. ctx.room is a *not-yet-connected* rtc.Room at this point
    # (ctx.connect() happens further down), so ctx.room.metadata is ALWAYS "" here
    # — reading it first meant tenant_id/agent_id were silently None on every
    # single call, and the agent ran the whole conversation on hardcoded defaults:
    # wrong clinic name, no DB first_message, no knowledge base, no publish/credit
    # gate, and no CallRecord ("No call_record_id — skipping finalization").
    # ctx.job.room.metadata is the room's metadata as delivered with the job
    # dispatch and IS populated before connect — that's the authoritative source.
    _entry_t0 = time.monotonic()
    metadata: dict = {}
    _raw_meta = ""
    try:
        _job = getattr(ctx, "job", None)
        for _candidate in (
            getattr(getattr(_job, "room", None), "metadata", ""),  # dispatch payload (reliable pre-connect)
            getattr(ctx.room, "metadata", ""),                     # only set post-connect
            getattr(_job, "metadata", ""),                         # explicit create_dispatch metadata
        ):
            _raw_meta = (_candidate or "").strip()
            if _raw_meta:
                break
        metadata = json.loads(_raw_meta or "{}")
    except (json.JSONDecodeError, AttributeError):
        pass

    tenant_id: Optional[str]  = metadata.get("tenant_id")
    agent_id: Optional[str]   = metadata.get("agent_id")
    caller_phone: str         = (
        metadata.get("caller_phone")
        or metadata.get("from_number")
        or metadata.get("patient_phone")
        or "unknown"
    )
    room_name: str = ctx.room.name

    log.info(
        "Agent entrypoint | room=%s tenant=%s agent=%s caller=%s",
        room_name, tenant_id, agent_id, caller_phone,
    )

    # ── One DB connection for the whole of call setup ───────────────────────
    # Everything that has to touch Postgres before the agent can speak — agent
    # config, tenant, doctors, knowledge base, the credit gate, the CallRecord
    # row and every provider API key — runs inside THIS session. backend/db.py
    # uses NullPool (Supabase does the pooling), so each extra session is a
    # complete TCP + TLS + auth handshake from Singapore, and this path used to
    # open four of them. See _load_tenant_and_config's `db` docstring for the
    # measured per-session cost on a real call.
    _setup_t0 = time.monotonic()
    from backend.db import AsyncSessionLocal

    async with AsyncSessionLocal() as setup_db:
        agent_config, tenant = await _load_tenant_and_config(
            tenant_id, agent_id, metadata, db=setup_db
        )

        # ── Publish/Unpublish enforcement — single source of truth is
        # AgentConfig.status (see backend/routers/embed.py's _is_published for the
        # matching check on the widget side). Only enforced when this room is tied
        # to a real agent_id — an unpublished agent must not take NEW calls, but a
        # call already in progress when it's unpublished is unaffected (this check
        # only runs once, at room-join time, not mid-call). Declining here — before
        # ctx.connect() — means the room is never joined, so no call minutes/audio
        # are billed or recorded for a call that was never allowed to start.
        # test_mode (in-dashboard "Test Agent") bypasses the publish gate so an admin
        # can test an agent that isn't ACTIVE yet — it's the same pipeline, just not
        # a real/billable inbound call.
        test_mode = bool(metadata.get("test_mode", False))
        if agent_id and not test_mode and agent_config.get("status") != "ACTIVE":
            log.warning(
                "Declining call: agent_id=%s is unpublished (status=%s) — not joining room %s",
                agent_id, agent_config.get("status"), room_name,
            )
            return
        if test_mode:
            log.info("TEST MODE call — publish gate bypassed for agent_id=%s", agent_id)

        # No credit gate here. Credits are not enforced in this MVP phase — a call
        # is never declined for balance/suspension reasons, for any clinic. The
        # publish gate above is the only reason this worker declines a room. See
        # backend/services/credit_service.py.

        # ── Create call record ──────────────────────────────────────────────
        # The row is only READ at finalisation (CallLoggerProcessor writes
        # duration/transcript into it when the call ends), so nothing between
        # here and the greeting needs it. Reserving the id locally and writing
        # the row in the background takes an INSERT + COMMIT round trip off the
        # path the caller is listening to silence on. call_meta is the same dict
        # CallLoggerProcessor holds a reference to, and it already has the id.
        call_meta = {
            "caller_phone":   caller_phone,
            "call_type":      "inbound",
            "room_name":      room_name,
            "call_record_id": str(uuid.uuid4()),
        }
        _record_task = asyncio.create_task(
            _create_call_record(tenant_id, agent_id, call_meta)
        )
        # Retained so the task isn't garbage-collected mid-flight, and awaited in
        # the finally block below so a fast hang-up can't race the INSERT.
        call_record_id = call_meta["call_record_id"]

        # ── Resolve provider API keys (DB first, env fallback) ──────────────
        # Hoisted up here from the STT/TTS build sections purely so they share
        # `setup_db`. Which providers matter is decided by the config we just
        # loaded; the resolution itself is one indexed SELECT each.
        #
        # Deliberately LAST inside this session: resolve_provider_key swallows its
        # own DB errors and falls back to the env key, so a failure here leaves the
        # transaction needing a rollback without ever telling us. Nothing else uses
        # the session afterwards, so that can't poison anything that reads it
        # earlier in setup the way it would if these ran first.
        _stt_provider_cfg = agent_config.get("stt_provider", "sarvam") or "sarvam"
        _tts_provider_cfg = agent_config.get("tts_provider", "sarvam") or "sarvam"
        _stt_keys, _tts_keys = await _resolve_provider_keys(
            setup_db, _stt_provider_cfg, _tts_provider_cfg
        )

    log.info(
        "Call setup DB work finished in %.2fs (one connection) | room=%s",
        time.monotonic() - _setup_t0, room_name,
    )

    # ── Connect to LiveKit room (REQUIRED by livekit-agents framework) ────────
    # ctx.connect() MUST be called here so livekit-agents worker framework marks
    # the dispatched job as accepted with LiveKit Cloud. auto_subscribe=False so
    # Pipecat's LiveKitTransport handles audio stream subscriptions cleanly.
    #
    # Started as a task rather than awaited: the LLM provider probe and the
    # STT/TTS websocket handshakes below are all network waits too, and none of
    # them depend on the room being joined. Awaited before the pipeline runs.
    _connect_task = asyncio.create_task(ctx.connect(auto_subscribe=False))

    # ── Generate agent token ───────────────────────────────────────────────
    agent_token = _generate_agent_token(room_name)

    # ── Resolve TTS voice & model ──────────────────────────────────────────
    tts_model_str = agent_config.get("tts_model", "bulbul:v3")
    tts_voice     = agent_config.get("tts_voice", "priya")
    tts_pace      = min(max(float(agent_config.get("tts_pace", 1.05)), 0.5), 2.0)
    # Derived from THE one language field, not from a separate tts_language column.
    # _safe_lang still maps it onto Sarvam's own code table (Sarvam is the only TTS
    # that takes a language at all).
    agent_language = agent_config.get("language") or agent_defaults.DEFAULT_LANGUAGE
    tts_language  = _safe_lang(agent_language)
    # bulbul:v2 is the only Sarvam model that accepts pitch/loudness — Pipecat's
    # SarvamTTSService silently ignores them for v3/v3-beta, so it's always
    # safe to pass through (unlike the raw-httpx Sarvam calls elsewhere, which
    # error on these params for v3 and must guard explicitly).
    tts_pitch     = min(max(float(agent_config.get("tts_pitch") or 0.0), -0.75), 0.75)
    tts_loudness  = min(max(float(agent_config.get("tts_loudness") or 1.0), 0.3), 3.0)
    tts_input_preprocessing = bool(agent_config.get("tts_input_preprocessing", True))

    # Validate tts_model against Pipecat's SarvamTTSModel enum values
    valid_tts_models = {m.value for m in SarvamTTSModel}
    if tts_model_str not in valid_tts_models:
        log.warning(
            "Unknown TTS model '%s' — falling back to bulbul:v3", tts_model_str
        )
        tts_model_str = "bulbul:v3"

    # ── Build system prompt ────────────────────────────────────────────────
    # The shared real-roster-and-real-slots block, built HERE (call setup, before
    # the caller has said anything) rather than per turn: it costs a DB read, and
    # a voice turn cannot afford a Supabase handshake. Today + tomorrow for every
    # doctor is what a fresh call needs; BookingProcessor refreshes it mid-call
    # when the caller raises a different day. Best-effort by construction — on
    # failure it returns the "could not look it up" block, never an empty string
    # and never a claim that the clinic has no doctors.
    availability_block = ""
    caller_appointments = ""
    if tenant.get("id") and not tenant.get("_facts_unavailable"):
        from backend.services.availability_prompt import (
            caller_appointments_block,
            real_availability_block,
        )

        availability_block = await real_availability_block(str(tenant["id"]))

        # What this caller ALREADY has booked, keyed on the number they are
        # calling from — so "cancel my appointment" is answered from the database
        # instead of interrogating them for details it already holds. Empty (and
        # therefore absent from the prompt) when there is no caller ID or no
        # existing appointment; BookingProcessor injects it mid-call once the
        # caller says a name or a number.
        caller_phone = (call_meta or {}).get("caller_phone") or ""
        if caller_phone:
            caller_appointments = await caller_appointments_block(
                str(tenant["id"]), str(caller_phone),
            )

    system_prompt = _build_system_prompt(
        agent_config, tenant, availability_block + caller_appointments,
    )

    # ── Build first message ────────────────────────────────────────────────
    first_message: str = (
        agent_config.get("first_message", "").strip()
        or f"Namaste! {tenant['clinic_name']} mein aapka swagat hai. "
           f"Main {agent_config.get('agent_name', 'Receptionist')} hoon. "
           "Aaj main aapki kaise madad kar sakti hoon?"
    )

    # ── STT Settings ───────────────────────────────────────────────────────
    stt_model = resolve_sarvam_stt_model(agent_config.get("stt_model"), room_name=room_name)

    # STT Language dropdown was previously ignored — _load_tenant_and_config
    # never loaded stt_language into agent_config, so this always fell back to
    # the TTS language. Now wired: use the agent's own STT language setting,
    # falling back to TTS language only if it's genuinely unset.
    #
    # Kept CANONICAL here (BCP-47, or "auto") and translated per provider in the
    # build branches below via stt_catalog.to_provider_code. This used to run
    # through _safe_lang — which is *Sarvam's* eleven-code table with a "hi-IN"
    # fallback — for EVERY provider. So selecting ar-SA, en-US, od-IN or
    # auto-detect silently transcribed HINDI, including on Deepgram, which serves
    # ar-SA and en-US natively. The dropdown said Arabic, the agent listened in
    # Hindi, and nothing logged a warning. _safe_lang is still correct for TTS
    # (Sarvam is the only TTS that takes a language) and is left alone there.
    # Derived from THE one language field plus the auto_detect_language boolean —
    # the only two inputs there are. This used to read a separate stt_language
    # column, which is how one agent came to transcribe Tamil while speaking
    # Malayalam: the two columns were independently editable and had drifted apart.
    stt_language = stt_catalog.canonicalize(
        agent_defaults.effective_stt_language(
            agent_language,
            auto_detect=bool(agent_config.get("auto_detect_language", False)),
        )
    )

    # saaras:v2.5 takes no language at all (pipecat marks it
    # supports_language=False and raises if one is passed); the catalogue returns
    # None for it, and for any language this model cannot actually serve.
    _sarvam_stt_lang = stt_catalog.to_provider_code("sarvam", stt_model, stt_language)
    if _sarvam_stt_lang is None:
        stt_settings = SarvamSTTService.Settings(
            model=stt_model,
        )
    else:
        stt_settings = SarvamSTTService.Settings(
            model=stt_model,
            language=_sarvam_stt_lang,
        )

    # ── Instantiate Pipecat services ───────────────────────────────────────

    # Transport — connects Pipecat to the LiveKit room.
    # agent_token has agent=True in VideoGrants so the frontend useVoiceAssistant
    # hook identifies this participant as the AI agent correctly.
    transport = LiveKitTransport(
        url=settings.livekit_url,
        token=agent_token,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # ⚠️ Do NOT put vad_enabled / vad_analyzer here. Those were pipecat
            # 0.x transport params; in 1.5.0 LiveKitParams has neither field and
            # pydantic silently DROPS unknown kwargs — so the Silero analyzer
            # that used to be configured here was never instantiated and the
            # tuned VADParams were dead config. VAD is now a real pipeline
            # processor (see VADProcessor below), which is how 1.5.0 wires it.
        ),
    )

    # STT — Deepgram (real-time streaming), Sarvam AI, OpenAI Whisper, ElevenLabs,
    # or AssemblyAI
    stt_provider = agent_config.get("stt_provider", "sarvam")

    # Set by the branches below and consumed by LanguageSwitchProcessor: whether a
    # mid-call language change has to be pushed into the STT service, and how to
    # translate our BCP-47 code into that provider's code. Left False for models
    # that already detect language themselves (Sarvam saaras, Deepgram nova-3
    # "multi") — reconnecting a healthy socket would only add deaf time.
    stt_needs_language_switch = False
    stt_language_translator = None

    # STT/TTS provider keys were resolved during the single setup session above
    # (_resolve_provider_keys) — DB (AI Platform dashboard) first, env fallback,
    # via backend/agent/providers.py::resolve_key. A key saved through the
    # dashboard takes effect on the very next call: no redeploy, no env var edit,
    # no worker restart. See providers.py for how to register a new provider.
    from backend.agent import providers as provider_registry

    # ── Deaf-agent guard ────────────────────────────────────────────────────
    # NONE of the STT services raise when handed an empty api_key — they build
    # fine and only fail later at the websocket/HTTP handshake with a 401. The
    # pipeline keeps running, TTS still has its own key, so the agent joins the
    # room, speaks its greeting, and then never transcribes a single word. That
    # silent failure mode is what made "voice input is not taken at all" so hard
    # to see: the only symptom is the ABSENCE of transcription.
    #
    # This is the exact bug that shipped when stt_provider was switched to
    # deepgram while DEEPGRAM_API_KEY was never added to the agent worker's env
    # (render.yaml's lifodial-agent service). So: verify the selected provider's
    # key up front, shout if it's missing, and degrade to a provider that can
    # actually hear rather than running the whole call deaf.
    if not (_stt_keys.get(stt_provider) or "").strip():
        _fallback = "sarvam" if (_stt_keys.get("sarvam") or "").strip() else None
        log.critical(
            "STT provider '%s' is selected but its API key is MISSING/EMPTY. The STT "
            "socket will 401 and the agent would greet the caller and then never hear "
            "a word (silently deaf). %s",
            stt_provider,
            f"Falling back to '{_fallback}' STT for room={room_name}." if _fallback
            else "No fallback STT key available either — this call WILL have no speech input.",
        )
        if _fallback:
            stt_provider = _fallback

    # Unbuildable-provider guard. The guard above only proves the SELECTED provider
    # has a key — it can't see that the provider has no `elif` branch below and will
    # therefore fall through to the Sarvam `else:`. google_stt and azure_stt are both
    # in the AI Platform catalog and both resolve a key successfully (via
    # provider_status._SPECIAL_ATTR), so they passed the key check and then silently
    # transcribed through Sarvam instead — or, with no Sarvam key, ran the call
    # completely deaf, which is exactly what the guard above exists to prevent.
    #
    # The buildable set is imported, not redeclared: backend/services/provider_registry.py
    # is the one place that knows what this file can construct, and the API now
    # refuses to SAVE anything outside it — so reaching this branch means either an
    # older row or a registry/pipeline mismatch. Both are worth shouting about.
    from backend.services.provider_registry import BUILDABLE_STT as _BUILDABLE_STT
    if stt_provider not in _BUILDABLE_STT:
        log.critical(
            "STT provider '%s' is selected but this pipeline has no build branch for it "
            "(buildable: %s). Falling back to Sarvam STT for room=%s — transcription will "
            "NOT use the provider shown in the dashboard. Add a branch in pipeline.py + a "
            "builder in backend/agent/providers.py to support it.",
            stt_provider, sorted(_BUILDABLE_STT), room_name,
        )
        stt_provider = "sarvam"
        if not (_stt_keys.get("sarvam") or "").strip():
            log.critical(
                "…and there is no Sarvam key either — room=%s WILL have no speech input.",
                room_name,
            )

    # Deepgram cannot transcribe Malayalam or Punjabi on ANY tier (verified against
    # the live API — both nova-2 and nova-3 answer HTTP 400). Switching provider
    # here, BEFORE the build chain, rather than inside the deepgram branch: doing it
    # in there would leave `stt_provider == "deepgram"` for the rest of the chain,
    # miss every `elif`, and land in the Sarvam `else:` — which is fine — but doing
    # it the other way round (breaking the elif chain into separate `if`s) would let
    # a SUCCESSFUL Deepgram build get silently overwritten by that same `else:`.
    if stt_provider == "deepgram":
        # ⚠️ Ask this about the agent's CONFIGURED language, never about
        # `stt_language` — which is "auto" whenever auto_detect_language is set.
        #
        # "Can Deepgram do auto?" answers YES (nova-3 has a multi mode), so an
        # auto-detect Malayalam agent used to sail past this guard and get built on
        # nova-3 "multi" — a mode whose language set does NOT include Malayalam. It
        # would transcribe Malayalam speech as whatever multi could nearest-match,
        # silently, with no 400 to reveal it.
        #
        # Auto-detect changes whether we PIN a language, not which languages the
        # provider can hear. A Malayalam agent needs a provider that can hear
        # Malayalam either way. This was latent until STT was locked to Deepgram:
        # before that, such agents happened to be on Sarvam already.
        _capability_lang = agent_language
        _dg_base = (_DG_LANG_MAP.get(_capability_lang, "en-IN") or "").split("-")[0]
        # Ask the catalogue, not just the ml/pa set: it also covers od-IN and the
        # rest of the Sarvam-only codes, which used to reach here as "hi-IN"
        # because _safe_lang had already flattened them.
        #
        # Deliberately asked against "nova-3", NOT the configured model: the block
        # below UPGRADES a nova-2 row to nova-3 for exactly the languages nova-2
        # rejects, so "can Deepgram do this at all?" is a question about its best
        # tier. Passing the stored model here would send every nova-2 agent to
        # Sarvam instead of letting that upgrade happen.
        if _dg_base in _DG_UNSUPPORTED_LANGS or not stt_catalog.is_supported(
            "deepgram", "nova-3", _capability_lang
        ):
            log.warning(
                "Deepgram has no model/tier for the agent's language %s (%s) — using Sarvam "
                "STT for room=%s so the agent can actually hear the caller. Deepgram would "
                "answer HTTP 400 (or, on auto-detect, silently mis-transcribe via 'multi') "
                "and pipecat would retry in a hot loop without ever transcribing.",
                _capability_lang, _dg_base, room_name,
            )
            stt_provider = "sarvam"

    if stt_provider == "deepgram":
        # Deepgram: real-time streaming, ~200ms TTFB (vs ~800ms Sarvam batch).
        log.info("Instantiating Deepgram streaming STT...")
        DeepgramSTTService = _import_deepgram_stt()
        # AUTO means "let nova-3 code-switch": seeding en-IN makes _base "en",
        # which the nova-3 block below turns into "multi".
        #
        # Anything else falls back to the code ITSELF, not to "en-IN". Deepgram
        # serves ar-SA / en-US / en-GB natively but DEEPGRAM_LANG_MAP has no entry
        # for them (it exists to translate the Indic codes, not to enumerate), so
        # the old `.get(stt_language, "en-IN")` collapsed Arabic to English — the
        # second half of the "selectable but silently wrong" bug.
        if stt_language == stt_catalog.AUTO:
            dg_lang = "en-IN"
        else:
            dg_lang = _DG_LANG_MAP.get(stt_language) or stt_language

        # ── Model/language selection ─────────────────────────────────────────
        # This block used to have the tier logic BACKWARDS, and the failure was
        # silent. Verified live against the Deepgram API (2026-07-28):
        #
        #   nova-2 + ta/te/kn/ml/mr/bn/pa/gu  -> HTTP 400
        #       "No such model/language/tier combination found. You could try the
        #        'general' model (language: ta, Nova-3 tier)."
        #   nova-3 + en/hi/ta/te/kn/mr/bn/gu  -> 200 OK
        #   nova-3 + ml, pa                   -> 400 (Deepgram supports neither)
        #
        # The old code did the opposite: it defaulted Indic languages to nova-2 and
        # actively DOWNGRADED an explicit nova-3 choice to nova-2 for exactly the
        # languages nova-2 rejects. Worse, the resulting 400 was invisible —
        # pipecat's Deepgram _connection_handler catches it in a bare `except` and
        # retries in a `while True` with no backoff, so the agent greeted the caller
        # and then sat in a hot reconnect loop, never transcribing a word.
        #
        # nova-3 is now the default for every language.
        _dg_requested = (agent_config.get("stt_model") or "").strip()
        dg_model = (
            _dg_requested
            if _dg_requested.startswith(("nova-", "base", "enhanced"))
            else "nova-3"
        )
        if _dg_requested and dg_model != _dg_requested:
            log.info(
                "STT model %r is not a Deepgram model — using %s for room=%s",
                _dg_requested, dg_model, room_name,
            )

        _base = dg_lang.split("-")[0]

        # ── nova-2 cannot serve this language: upgrade rather than go deaf ─────
        # The tier table at the top of this file is not advisory. nova-2 answers
        # HTTP 400 for ta/te/kn/ml/mr/bn/pa/gu, pipecat's Deepgram
        # _connection_handler swallows it in a bare `except` and retries forever,
        # and the only symptom is that the agent greets the caller and then never
        # transcribes a word.
        #
        # An explicit nova-2 choice is not proof of intent here: until 2026-07-29
        # the AI Platform catalog listed ONLY nova-2 models for Deepgram, and the
        # agent dashboard auto-selects models[0] and saves it — so every agent
        # switched to Deepgram through the UI was written a nova-2 id it never
        # asked for. Those rows are still out there. Honour the choice where it
        # can work, upgrade it where it provably cannot.
        if not dg_model.startswith("nova-3") and _base in _DG_NOVA2_UNSUPPORTED_LANGS:
            log.warning(
                "Deepgram model %s cannot transcribe %s (nova-2 and older tiers answer "
                "HTTP 400 for it, which pipecat retries silently forever) — using nova-3 "
                "for room=%s so the agent can actually hear the caller.",
                dg_model, _base, room_name,
            )
            dg_model = "nova-3"
        elif not dg_model.startswith("nova-3"):
            log.warning(
                "Deepgram model %s has no multilingual tier, so it is pinned to a single "
                "language (%s) and will not hear a caller who code-switches — which Indian "
                "callers routinely do mid-sentence. nova-3 handles Hindi and English in one "
                "socket; prefer it unless this model was chosen deliberately. room=%s",
                dg_model, _base, room_name,
            )

        if dg_model.startswith("nova-3"):
            # Prefer "multi" where nova-3 offers it: multilingual code-switches
            # inside ONE socket, so a caller moving English → Hindi mid-call costs
            # nothing. Pinning a single code would make LanguageSwitchProcessor send
            # an STTUpdateSettingsFrame, and Deepgram's _update_settings reconnects
            # the websocket — ~200-400ms deaf at exactly the moment the caller
            # switched.
            dg_lang = "multi" if _base in _DG_NOVA3_MULTI_LANGS else _base

        log.info("Deepgram STT: model=%s language=%s", dg_model, dg_lang)
        stt = DeepgramSTTService(
            api_key=_stt_keys["deepgram"],
            settings=DeepgramSTTService.Settings(
                model=dg_model,
                language=dg_lang,
                smart_format=True,
                interim_results=True,
                endpointing=300,  # 300ms silence before utterance finalized
                punctuate=True,
                utterance_end_ms=1000,
            ),
        )
        # "multi" already code-switches inside one socket, so only the
        # language-pinned models need a mid-call STT retune.
        stt_needs_language_switch = dg_lang != "multi"

        def stt_language_translator(code: str, _model: str = dg_model) -> str:
            base = (_DG_LANG_MAP.get(code, "") or "").split("-")[0]
            if not base or base in _DG_UNSUPPORTED_LANGS:
                return ""  # no valid Deepgram target — leave STT as it is
            if _model.startswith("nova-3"):
                return "multi" if base in _DG_NOVA3_MULTI_LANGS else base
            return _DG_LANG_MAP.get(code, "")
    elif stt_provider in ("openai", "whisper"):
        log.info("Instantiating OpenAI Whisper STT...")
        stt = OpenAISTTService(
            api_key=_stt_keys.get(stt_provider) or _stt_keys["openai"],
            model="whisper-1"
        )
    elif stt_provider == "elevenlabs":
        log.info("Instantiating ElevenLabs Realtime STT...")
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService

        # ElevenLabs Scribe takes ISO-639-3 THREE-letter codes ("hin", "kan"), which
        # a bogus language_code makes it enumerate. This used to send
        # `stt_language.split("-")[0]` — two-letter "hi"/"kn" — which is not a value
        # Scribe accepts, so picking a language for ElevenLabs never took effect.
        # None == blank == Scribe auto-detects, which is what AUTO should mean.
        stt_lang = stt_catalog.to_provider_code("elevenlabs", None, stt_language)

        stt = ElevenLabsRealtimeSTTService(
            api_key=_stt_keys["elevenlabs"],
            settings=ElevenLabsRealtimeSTTService.Settings(
                language=stt_lang or None,
            )
        )
        stt_needs_language_switch = bool(stt_lang)
        stt_language_translator = lambda code: (
            stt_catalog.to_provider_code("elevenlabs", None, code) or ""
        )
    elif stt_provider == "assemblyai":
        log.info("Instantiating AssemblyAI streaming STT...")
        stt = provider_registry.build_assemblyai_stt(api_key=_stt_keys["assemblyai"])
        # AssemblyAI's streaming v3 API auto-detects language — no per-language
        # reconnect needed mid-call.
        stt_needs_language_switch = False
    else:
        log.info("Instantiating Sarvam STT...")
        stt = SarvamSTTService(
            api_key=_stt_keys["sarvam"],
            settings=stt_settings,
        )
        # saaras:v2.5 was built with no language at all (it auto-detects); the
        # pinned models (saarika, saaras:v3) need to be told. Keyed off what was
        # actually passed rather than re-deriving the model name.
        stt_needs_language_switch = _sarvam_stt_lang is not None
        # Was _safe_lang, whose fallback is "hi-IN" — so a mid-call switch into
        # Odia or any saaras-only language retuned STT to Hindi. The catalogue
        # knows this model's real set and returns "unknown" (Sarvam's own
        # auto-detect) rather than a wrong language.
        stt_language_translator = lambda code, _m=stt_model: (
            stt_catalog.to_provider_code("sarvam", _m, code) or ""
        )

    # ── One honest line about what the caller will actually be heard by ────────
    # "Real-time" is a property of the SERVICE, not of the pipeline: only
    # providers that emit InterimTranscriptionFrame put words on screen while the
    # caller is still talking, and only they let end-of-turn fire quickly.
    # Pipecat's own measured p99 "speech end → final transcript" figures
    # (pipecat/services/stt_latency.py) are what the turn-stop strategy budgets
    # against, so they are the honest number to log:
    #
    #     Deepgram  0.35s  + interim results   → live transcript, fast turns
    #     AssemblyAI 0.42s + interim results
    #     Sarvam    1.17s  NO interim results  → transcript only after the caller
    #                                            stops, and ~0.8s more dead air
    #                                            per turn than Deepgram
    #
    # Sarvam's pipecat service pushes a TranscriptionFrame only from a `data`
    # message (services/sarvam/stt.py::_handle_message) and never an
    # InterimTranscriptionFrame — so with Sarvam the live-transcript panel in the
    # dashboard stays empty until the caller pauses. That is the provider, not a
    # bug in this pipeline, and it is the single biggest lever on perceived
    # latency for anyone reporting "it doesn't hear me".
    # Providers whose pipecat service actually constructs an
    # InterimTranscriptionFrame. Verified against the installed pipecat-ai 1.5.0
    # source on 2026-08-03: elevenlabs was listed here but its STT service emits
    # NO interim frames and is not even websocket-based, so it was being reported
    # as real-time when it is not. Kept in sync with the same set in
    # frontend/src/components/TestVoiceCallLK.tsx.
    _STT_REALTIME = {"deepgram", "assemblyai"}
    log.info(
        "STT ready | provider=%s model=%s language=%s realtime_interim=%s ttfs_p99=%.2fs | room=%s",
        stt_provider,
        getattr(getattr(stt, "_settings", None), "model", stt_model),
        stt_language,
        stt_provider in _STT_REALTIME,
        float(getattr(stt, "_ttfs_p99_latency", 0.0) or 0.0),
        room_name,
    )
    if stt_provider not in _STT_REALTIME:
        log.info(
            "STT provider '%s' emits no interim results — the live transcript will only "
            "update when the caller pauses, and each turn waits ~%.2fs longer than "
            "Deepgram before the agent replies. Switch STT to Deepgram for real-time "
            "transcription. room=%s",
            stt_provider,
            max(0.0, float(getattr(stt, "_ttfs_p99_latency", 1.17) or 1.17) - 0.35),
            room_name,
        )

    # LLM — resilient provider selection (audit FIX 2). Probe the configured
    # provider first, fall back through healthy alternatives. This is what makes
    # a dead/leaked primary key (the Gemini key is currently revoked) non-fatal:
    # the whole call runs on the first reachable provider instead of going silent.
    # Probes run once here at setup — never in the per-turn hot loop.
    from backend.agent.resilience import select_llm_provider, build_llm, ResilienceProcessor

    llm_provider, llm_key, llm_model = await select_llm_provider(agent_config)
    log.info("Using LLM provider=%s model=%s for room=%s", llm_provider, llm_model, room_name)
    llm = await build_llm(llm_provider, llm_key, llm_model, system_prompt, agent_config)

    # Build LLM context (conversation history) + a PROVIDER-AGNOSTIC aggregator.
    # llm.create_context_aggregator(...) only exists on GoogleLLMService; since
    # the LLM is now chosen at runtime (Gemini/Groq/OpenAI — audit FIX 2), use
    # the universal LLMContextAggregatorPair, which drives any provider off the
    # same LLMContext.
    # NO system message here — build_llm() already passes `system_prompt` to the
    # service as `system_instruction`, and pipecat's OpenAI-family adapter runs
    # with discard_context_system=False, i.e. it KEEPS BOTH. Every request was
    # therefore carrying two full copies of the system prompt (KB + booking rules
    # + language rule); measured with a stand-in prompt: 7602 chars vs 3802 for a
    # two-message exchange. The worker logged the warning on every turn:
    #
    #   "Both system_instruction and an initial system message in context are
    #    set, which may be unintended. Keeping both..."
    #
    # Doubling the prompt doubles input tokens, LLM time-to-first-byte and the
    # JSON the free-tier worker has to serialise per turn. Nothing reads
    # messages[0] — BookingProcessor and the greeting only ever append — so the
    # instruction alone is the single source of truth. Gemini's adapter
    # (discard_context_system=True) prefers system_instruction as well, so this is
    # correct for every provider build_llm() can return.
    context = LLMContext(messages=[])
    # ── Turn-stop strategy — cheap timer, not a local transformer ───────────
    # pipecat 1.5 defaults the STOP strategy to TurnAnalyzerUserTurnStopStrategy,
    # which runs the Local Smart Turn v3 ONNX transformer over the utterance audio
    # to decide semantically whether the caller is done. It is a good default on a
    # real CPU. This worker runs on Render's FREE plan (0.1 CPU), where that
    # inference is a synchronous block inside the event loop — and the live logs
    # show what it costs: bursts of
    #
    #   "libwebrtc audio_stream queue overflow; dropped 400 queued frames"
    #
    # right after each utterance. Dropped inbound frames plus a stalled loop is
    # exactly the reported symptom set: TTS that stutters mid-sentence and
    # barge-in that "doesn't stop" (the InterruptionFrame cannot be processed
    # while the loop is blocked, so the caller keeps hearing buffered audio).
    #
    # SpeechTimeoutUserTurnStopStrategy replaces it with two plain timers: no
    # model load per job, no inference per utterance. End-of-turn becomes
    # stop_secs (0.2) + user_speech_timeout (0.6) ≈ 0.8s, predictable.
    #
    # Semantic turn detection is strictly better once there is CPU for it, so this
    # is a flag, not a deletion: set AGENT_SMART_TURN=true after moving off the
    # free plan to get it back.
    if settings.agent_smart_turn:
        log.info("Turn detection: Local Smart Turn v3 (semantic) — AGENT_SMART_TURN is on")
        user_params = LLMUserAggregatorParams()
    else:
        log.info("Turn detection: speech-timeout timers (cheap) — set AGENT_SMART_TURN=true to use Smart Turn v3")
        user_params = LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                # start=[...] intentionally omitted — the default
                # [VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy]
                # is what fires barge-in, and must stay.
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)],
            ),
        )

    context_aggregator = LLMContextAggregatorPair(context, user_params=user_params)

    # ── VAD — barge-in / interruption (pipecat 1.5.0 wiring) ────────────────
    # This is the piece that was silently missing. In 1.5.0 VAD is neither a
    # transport param nor implicit: it is a PROCESSOR. Placed FIRST, right after
    # transport.input(), it broadcasts VADUserStartedSpeakingFrame /
    # VADUserStoppedSpeakingFrame, which two different consumers need:
    #
    #   1. The user aggregator's default turn-start strategies are
    #      [VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy]. The VAD
    #      one fires the interruption on speech ONSET (~start_secs); without it
    #      only the transcription strategy ran, so barge-in waited for Deepgram's
    #      first interim transcript to make the network round-trip. Interruptions
    #      themselves are ON by default in 1.5.0
    #      (BaseUserTurnStartStrategy.enable_interruptions) — the analyzer was the
    #      only missing half.
    #   2. SegmentedSTTService subclasses (OpenAI Whisper) slice utterances off
    #      these exact frames. That is why VAD must sit UPSTREAM of `stt` and not
    #      on the aggregator: an aggregator-hosted analyzer is downstream of STT,
    #      so whisper would never see a boundary and never transcribe. Deepgram
    #      and Sarvam are streaming services and segment server-side.
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                # start_secs 0.2  → speech onset (and interruption) at ~200ms
                # confidence 0.55 → slightly more sensitive, fewer missed starts
                #
                # ⚠️ stop_secs MUST stay at pipecat 1.5's 0.2 default. It was set to
                # 0.6 here as a "latency optimisation" carried over from 0.x, where
                # stop_secs directly gated end-of-turn. In 1.5 the turn machinery
                # subtracts stop_secs from the STT p99 latency budget, and the
                # worker logged exactly what goes wrong:
                #
                #   "VAD stop_secs (0.6s) >= STT p99 latency (0.35s). STT wait
                #    timeout collapsed to 0s, which may cause delayed turn
                #    detection specified by the user_turn_stop_timeout parameter"
                #
                # With the budget collapsed to 0 the turn fell through to the
                # user_turn_stop_timeout fallback (5s default), which is precisely
                # the stall seen on live calls: VAD stopped 15:38:22.7, turn
                # inference only fired 15:38:27.5 — 4.8s of dead air per turn.
                # Raising this "for latency" costs latency.
                stop_secs=0.2,
                start_secs=0.2,
                confidence=0.55,
            )
        )
    )

    # TTS — Sarvam AI, ElevenLabs, OpenAI, or Cartesia
    tts_provider = agent_config.get("tts_provider", "sarvam")

    # Providers this function can actually BUILD, i.e. those with an `elif` branch
    # below — imported from the shared registry rather than redeclared here, so the
    # API's write-time validation and this run-time guard can never disagree.
    from backend.services.provider_registry import BUILDABLE_TTS as _BUILDABLE_TTS

    # _tts_keys came from the same single setup session as _stt_keys. "sarvam" is
    # ALWAYS in it because the final `else:` is the Sarvam branch — the fallback
    # every unrecognised provider lands on, reading _tts_keys["sarvam"]. Omitting
    # it once raised KeyError: 'sarvam' inside entrypoint(), which kills the job
    # before the agent joins the room: the caller hears dead air and the logs show
    # no reason why.

    # Mute-agent guard — the TTS mirror of the deaf-agent guard above. Say so
    # loudly when the configured provider cannot be built, instead of quietly
    # speaking in a different voice than the dashboard shows.
    if tts_provider not in _BUILDABLE_TTS:
        log.critical(
            "TTS provider '%s' is selected but this pipeline has no build branch for it "
            "(buildable: %s). Falling back to Sarvam TTS for room=%s — the caller will "
            "hear a DIFFERENT voice than the dashboard shows. Add a branch in "
            "pipeline.py + a builder in backend/agent/providers.py to support it.",
            tts_provider, sorted(_BUILDABLE_TTS), room_name,
        )
        tts_provider = "sarvam"
    if not (_tts_keys.get(tts_provider) or "").strip():
        log.critical(
            "TTS provider '%s' has no API key (DB or env). Synthesis will 401 and the "
            "caller will hear NOTHING at all for room=%s.", tts_provider, room_name,
        )

    if tts_provider == "elevenlabs":
        # Safe fallback: if tts_voice is empty or is a Sarvam voice name, default to ElevenLabs' Rachel ID
        selected_voice = tts_voice
        sarvam_voice_ids = {
            "priya", "ritu", "neha", "simran", "kavya", "ishita", "shreya", "tanya", "pooja", "roopa",
            "kavitha", "suhani", "shruti", "niharika", "rupali", "rahul", "aditya", "ashutosh", "rohan",
            "amit", "dev", "ratan", "varun", "manan", "sumit", "kabir", "aayan", "shubh", "advait",
            "anand", "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham", "meera", "bulbul"
        }
        if not selected_voice or selected_voice.lower() in sarvam_voice_ids:
            selected_voice = "21m00Tcm4TlvDq8ikWAM"  # Rachel (Premium Female English)

        tts_model_configured = agent_config.get("tts_model", "eleven_flash_v2_5")
        if tts_model_configured not in ("eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_turbo_v2_5"):
            tts_model_configured = "eleven_flash_v2_5"

        # Voice-character sliders (Stability / Clarity / Style / Speaker Boost /
        # Speed) — mapped 1:1 to ElevenLabsTTSSettings, which is the class this
        # websocket-based service actually exposes (confirmed against the
        # installed pipecat-ai package). `speed` is clamped to ElevenLabs'
        # accepted 0.7–1.2 range since the agent's slider goes 0.5–2.0.
        el_speed = agent_config.get("tts_speed")
        el_speed = min(max(float(el_speed), 0.7), 1.2) if el_speed is not None else None

        log.info("Instantiating ElevenLabs TTS for voice: %s, model: %s", selected_voice, tts_model_configured)
        tts = ElevenLabsTTSService(
            api_key=_tts_keys["elevenlabs"],
            voice_id=selected_voice,
            settings=ElevenLabsTTSService.Settings(
                model=tts_model_configured,
                stability=agent_config.get("tts_stability"),
                similarity_boost=agent_config.get("tts_clarity"),
                style=agent_config.get("tts_style"),
                use_speaker_boost=agent_config.get("tts_use_speaker_boost"),
                speed=el_speed,
            )
        )
    elif tts_provider == "openai_tts":
        # tts_model_str was validated against Sarvam's own model enum above
        # (line ~767) and forced to "bulbul:v3" if it didn't match — read the
        # raw configured model directly here instead, same as the Cartesia and
        # ElevenLabs branches already do for their own model lists. Without
        # this, every OpenAI TTS call silently used gpt-4o-mini-tts regardless
        # of the admin's actual choice (tts-1 / tts-1-hd), because
        # "bulbul:v3" doesn't start with "gpt-"/"tts-" either.
        openai_model = agent_config.get("tts_model") or "gpt-4o-mini-tts"
        if not (openai_model.startswith("gpt-") or openai_model.startswith("tts-")):
            openai_model = "gpt-4o-mini-tts"
        log.info("Instantiating OpenAI TTS for voice: %s, model: %s", tts_voice, openai_model)
        openai_speed = agent_config.get("tts_speed")
        openai_speed = min(max(float(openai_speed), 0.25), 4.0) if openai_speed is not None else None
        tts = OpenAITTSService(
            api_key=_tts_keys.get("openai_tts") or _tts_keys["openai"],
            settings=OpenAITTSService.Settings(
                voice=tts_voice or "alloy",
                model=openai_model,
                speed=openai_speed,
            ),
        )
    elif tts_provider == "cartesia":
        # tts_model_str was validated against Sarvam's own model enum above
        # (line ~607) and forced to a Sarvam value if it didn't match — read
        # the raw configured model directly here instead, same as the
        # ElevenLabs branch already does for its own model list.
        cartesia_model = agent_config.get("tts_model") or "sonic-2"
        log.info("Instantiating Cartesia TTS for voice: %s, model: %s", tts_voice, cartesia_model)
        tts = provider_registry.build_cartesia_tts(
            api_key=_tts_keys["cartesia"],
            voice_id=tts_voice or None,
            model=cartesia_model,
        )
    else:
        log.info("Instantiating Sarvam TTS...")
        # NOTE: SarvamTTSService.__init__ only accepts `api_key`/`model`/
        # `voice_id` as direct kwargs in the installed pipecat-ai release —
        # voice/language/pace/pitch/loudness/enable_preprocessing must go
        # through `settings=`, or they're silently swallowed by **kwargs and
        # never reach Sarvam at all (confirmed against pipecat-ai 1.5.0;
        # requirements.agent.txt pins no upper bound so this is what a fresh
        # deploy installs).
        tts = SarvamTTSService(
            api_key=_tts_keys["sarvam"],
            settings=SarvamTTSService.Settings(
                voice=tts_voice,
                model=tts_model_str,
                language=tts_language,
                pace=tts_pace,
                pitch=tts_pitch,
                loudness=tts_loudness,
                enable_preprocessing=tts_input_preprocessing,
                # ── Time-to-first-audio ────────────────────────────────────
                # These are sent to Sarvam in the websocket config frame and
                # decide when it STARTS synthesizing. pipecat's defaults are
                # min_buffer_size=50 / max_chunk_length=150 characters, which are
                # tuned for long-form narration and are wrong for a phone call:
                # this agent is instructed to answer in at most 2 sentences and
                # runs with max_response_tokens=120, so a typical reply is well
                # under 50 characters. Sarvam therefore buffered the ENTIRE reply
                # without emitting a sample, and audio only began after pipecat
                # flushed on LLMFullResponseEndFrame — i.e. the caller waited for
                # the whole LLM turn to finish before hearing the first word.
                #
                # 30/60 lets a short reply start synthesizing almost immediately
                # while still batching enough text for natural prosody. Costs
                # nothing but marginally more chunking on long replies.
                #
                # min_buffer_size has a hard floor of 30 on Sarvam's side (range
                # 30-200); this was shipped at 15 and Sarvam rejected the whole
                # config with a generic "Input parameters has to be a valid
                # dictionary" error, which pipecat's reconnect loop retried 3x and
                # then gave up — the call went completely silent on TTS. Verified
                # against a live production failure (room=testcall-72cb61a4-0dfb0a02,
                # 2026-07-29 09:12 UTC) and Sarvam's documented bounds.
                min_buffer_size=30,
                max_chunk_length=60,
            ),
        )

    # Custom processors — booking state machine + call logging
    # call_logger is constructed FIRST now: both booking_processor and
    # voice_action need its action_in_progress flag (see
    # _enforce_silence_timeout) so the silence watchdog can see EITHER
    # processor's own DB commit in flight, not just voice_action's. Before this,
    # a cancel/reschedule committed via booking_processor's keyword-confirm path
    # (the FSM, still live in production alongside voice_action's [ACTION:] tag
    # mechanism) raised nothing, so a slow commit + LLM/TTS turn could outrun
    # the silence timeout and end the call with the hardcoded end_call_message
    # BEFORE the caller ever heard whether their booking succeeded.
    call_logger = CallLoggerProcessor(
        tenant_id=tenant_id or "",
        agent_id=agent_id,
        call_meta=call_meta,
        agent_config=agent_config,
    )
    booking_processor = BookingProcessor(
        tenant=tenant,
        agent_config=agent_config,
        call_meta=call_meta,
        call_logger=call_logger,
    )
    # Executes the [ACTION: …] tags the MODEL emits — the mechanism that makes a
    # spoken "your appointment is booked" correspond to a real row. The FSM above
    # can only commit when the caller names the doctor themselves and then says a
    # bare "yes" in a later turn, which a real call does not do; until this
    # processor existed, no voice call in the product's lifetime had ever written
    # an appointment. Both writers stay: they share one idempotency key
    # (call_record_id), so whichever fires first wins and the other is a no-op.
    voice_action = VoiceActionProcessor(
        context=context,
        tenant=tenant,
        agent_config=agent_config,
        call_meta=call_meta,
        call_logger=call_logger,
    )
    # Delivers the caller's words to the booking state machine, and starts a fresh
    # turn for voice_action (its one-action-and-one-repair-per-utterance caps have
    # to lift when the caller speaks again). booking_processor itself sits
    # downstream of context_aggregator.user() (it needs the LLMContextFrame), and
    # the aggregator eats TranscriptionFrames — so without this tap the FSM
    # receives nothing at all and no call can ever book. See
    # BookingTranscriptTap's docstring for the full account.
    booking_transcript_tap = BookingTranscriptTap(
        booking_processor, on_new_turn=voice_action.reset_turn,
        # The FSM chain this tap awaits is the agent working on the caller's
        # request, and it runs BEFORE the LLM turn. Handing it the call logger
        # lets it hold the silence watchdog's clock for that window, the same
        # way voice_action holds it for a booking write.
        call_logger=call_logger,
    )
    # Feeds user utterances to call_logger from a position where
    # TranscriptionFrames still exist. Without it the logger counts zero turns
    # and stores an empty transcript, because context_aggregator.user() swallows
    # those frames before they can reach it (see UserTranscriptTap's docstring).
    user_transcript_tap = UserTranscriptTap(call_logger)

    # Caps what one turn can cost. Placed immediately before `llm` so it trims
    # the context exactly as the model would receive it — after booking_processor
    # has injected this turn's system messages. Keeps every system message and
    # only drops old dialogue; see the module docstring for why that distinction
    # is load-bearing rather than a detail.
    context_trim = ContextTrimProcessor()

    # Never-silence guard (audit FIX 2): sits at the tail of the pipeline and,
    # on any LLM/TTS ErrorFrame, speaks a short reassurance phrase in the agent's
    # language instead of leaving dead air. Task is bound after PipelineTask
    # construction below.
    # The LLM service, provider and model are passed in so a rate limit can be
    # RECOVERED (switch to a Groq model that still has budget and re-ask this turn)
    # rather than only apologised for. The setup-time probe above cannot prevent this
    # case: listing models costs no tokens, so a key with an exhausted daily budget
    # passes the probe and fails on the caller's first question.
    resilience = ResilienceProcessor(
        language=tts_language,
        llm=llm,
        llm_provider=llm_provider,
        llm_model=llm_model,
        # A rate-limit model switch re-asks the LLM; that round trip is agent
        # work, not caller silence.
        call_logger=call_logger,
    )

    # Mid-call language switching: watches the caller's final transcripts and
    # retunes TTS (and STT, when the model is language-pinned) the moment they
    # change language. Sits between `stt` and the user aggregator for the same
    # reason user_transcript_publisher does — the aggregator eats
    # TranscriptionFrames without forwarding them.
    language_switcher = LanguageSwitchProcessor(
        tts=tts,
        stt=stt,
        initial_language=tts_language,
        stt_language_map=stt_language_translator,
        switch_stt=stt_needs_language_switch,
        # One clear turn is enough — a caller who switches expects to be answered
        # in the new language on the very next reply, not two turns later.
        confirm_turns=1,
        on_switch=resilience.set_language,
        # Bounds what an explicit "please speak X" request may switch TO. Sourced
        # from the TTS provider's real catalogue, because TTS is the half with no
        # fallback: honouring a request for a language the voice cannot speak would
        # replace "the agent ignored me" with "the agent stopped talking".
        supported_languages={
            lang["code"] for lang in agent_defaults.tts_languages(tts_provider)
        },
    )

    # ── Build the Pipeline ─────────────────────────────────────────────────
    # Data flows left to right through each processor:
    #
    #   audio in → STT → context_in → booking → LLM → tag scrub → TTS → logger → audio out
    #
    # Mirrors the agent's spoken text into the room as transcriptions so the
    # browser Test widget shows a live transcript (transparent passthrough,
    # fully guarded — see LiveKitTranscriptPublisher). Placed right after TTS so
    # it sees TTSTextFrames (the text actually being spoken).
    # TWO publisher instances, one per side of the conversation. They cannot be a
    # single processor because the two frame types they mirror only exist at
    # opposite ends of the pipeline:
    #
    #   • user_transcript_publisher MUST sit between `stt` and
    #     context_aggregator.user(). The user aggregator CONSUMES both
    #     TranscriptionFrame and InterimTranscriptionFrame without pushing them
    #     downstream (pipecat 1.5.0, llm_response_universal.py:794-799 — final
    #     transcriptions go to _handle_transcription() with no push_frame, and the
    #     interim branch is a bare `pass` whose own comment says "not pushed
    #     downstream, same as final TranscriptionFrame"). While the publisher sat
    #     AFTER the aggregator it received zero user transcriptions for the whole
    #     call, so the browser never got a DataReceived event and the caller saw
    #     nothing at all while speaking. STT was fine; only the mirror was dead.
    #     This is the same swallowing bug as context_aggregator.assistant() below,
    #     at the other end of the pipeline.
    #
    #   • agent_transcript_publisher MUST sit after `tts`, because TTSTextFrame is
    #     pushed downstream BY the TTS service — nothing upstream of it can see one.
    user_transcript_publisher = LiveKitTranscriptPublisher(transport)
    agent_transcript_publisher = LiveKitTranscriptPublisher(transport)

    # Strips [BOOKING_RESULT …] / [AVAILABILITY_NOTE] / [ACTION: …] out of the
    # LLM's text before TTS can speak it. Those tags are addressed to the model
    # (BookingProcessor injects them into its context), and a model that has
    # just been shown one echoes it — which on the voice path would be read out
    # loud to the caller, since nothing downstream of the LLM inspects the text.
    # MUST sit between `llm` and `tts`. See processors/tag_scrub.py.
    tag_scrub = TagScrubProcessor()

    pipeline = Pipeline([
        transport.input(),                       # Audio in from LiveKit room
        resilience,                              # Never-silence: ErrorFrame → spoken fallback
        vad,                                     # Silero VAD → speech start/stop (barge-in + segmentation)
        stt,                                     # Speech → Transcription/InterimTranscriptionFrame
        language_switcher,                       # Caller changed language? retune STT/TTS (transparent)
        user_transcript_tap,                     # Feed user turns to call_logger (transparent)
        user_transcript_publisher,               # Mirror USER text → room data channel (transparent)
        booking_transcript_tap,                  # Feed caller utterances to booking_processor (transparent)
        context_aggregator.user(),               # Accumulates user turns into LLMContext
        booking_processor,                       # Booking state machine (transparent)
        context_trim,                            # Cap the context's token cost (transparent)
        llm,                                     # LLMContext → LLMResponseFrame (streaming)
        voice_action,                            # Execute the model's [ACTION:] tags for real
        tag_scrub,                               # Never speak a machine tag (transparent)
        tts,                                     # LLMResponseFrame → TTSAudioRawFrame
        call_logger,                             # Metrics + call record updates (transparent)
        agent_transcript_publisher,              # Mirror AGENT text → room transcript (transparent)
        transport.output(),                      # Audio out to LiveKit room
        context_aggregator.assistant(),          # Stores assistant reply in context — MUST BE LAST
    ])
    # ⚠️ `resilience` MUST sit UPSTREAM of stt / llm / tts — never at the tail.
    # Pipecat pushes ErrorFrames UPSTREAM, not downstream:
    # FrameProcessor.push_error() → push_error_frame() → push_frame(error,
    # FrameDirection.UPSTREAM) (pipecat 1.5.0, frame_processor.py:722). While
    # this processor sat second-from-last, every provider ErrorFrame travelled
    # away from it and it never fired once in production — so a Groq 429, a TTS
    # failure, or an exception in any processor produced pure dead air, and the
    # model-failover added in 0816a4e could never run either. Both the fallback
    # phrase and the failover are reached only from here, so the position is the
    # feature. Verified by test_error_frame_reaches_resilience_from_llm_position.
    #
    # It costs one extra passthrough hop per input audio frame, which is the
    # price of also covering STT: an STT service that dies upstream of any other
    # placement would otherwise be the one provider failure still able to hang a
    # call silently.
    #
    # ⚠️ `booking_transcript_tap` MUST sit BEFORE context_aggregator.user(), and
    # `booking_processor` AFTER it. The two halves need frames that exist on
    # opposite sides of the aggregator (TranscriptionFrame before, LLMContextFrame
    # after), and the aggregator consumes the former without forwarding it
    # (llm_response_universal.py:794). With both on the downstream side — as
    # shipped until now — the booking FSM received zero utterances and no voice
    # call could book, cancel or reschedule anything. Verified by
    # test_transcription_reaches_booking_processor_through_pipeline.
    #
    # ⚠️ `voice_action` MUST sit between `llm` and `tag_scrub`. It has to see the
    # model's RAW streamed text (tag_scrub strips exactly the tags it acts on), and
    # it has to sit upstream of `tts` so a reply that starts with a tag can be held
    # back and never spoken. Its LLM re-run also depends on the tail of the
    # pipeline: it pushes an LLMRunFrame downstream to context_aggregator.assistant(),
    # which pushes the updated context back UPSTREAM to `llm` (pipecat's own re-run
    # path — llm_response_universal.py:1623). Downstream of `tts` it could no longer
    # withhold anything, and upstream of `llm` it would never see the text at all.
    #
    # ⚠️ context_aggregator.assistant() MUST be the LAST processor — never between
    # the LLM and TTS. LLMAssistantAggregator.process_frame() CONSUMES
    # LLMFullResponseStartFrame / LLMFullResponseEndFrame / TextFrame without
    # pushing them downstream (it buffers them to build the assistant context
    # message). Sitting it directly after the LLM therefore swallowed 100% of the
    # LLM's output text before it could reach `tts` — so the agent joined the
    # room, published an audio track, and then never spoke a single word on any
    # turn, including the greeting. That was the sole cause of the "room is
    # created but there's no conversation" bug. Verified against
    # pipecat-ai 1.5.0 (processors/aggregators/llm_response_universal.py:1493-1498
    # and _handle_text at :1935 — all return without push_frame).
    #
    # call_logger also moved to AFTER `tts`: it reacts to TTSStartedFrame (resets
    # the silence-timeout idle clock when the AGENT speaks) and to the TTS
    # MetricsFrame (TTFB latency). Both are pushed DOWNSTREAM by `tts`, so while
    # call_logger sat upstream of it neither ever arrived — the idle-clock reset
    # added in c594ea6 was dead code and TTS latency was never recorded.
    # TranscriptionFrame / End / Cancel still reach it: they propagate downstream
    # from stt and the task source respectively.

    # ── Build & run the task ───────────────────────────────────────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            # NOTE: allow_interruptions used to be passed here. PipelineParams has
            # no such field in pipecat 1.5.0 and pydantic drops unknown kwargs, so
            # it was a no-op. Interruptions are ON by default in 1.5.0 — the knob
            # that actually matters is the VAD analyzer on the user aggregator
            # above (BaseUserTurnStartStrategy.enable_interruptions defaults True).
            enable_metrics=True,          # Enables MetricsFrame for latency tracking
            enable_usage_metrics=True,    # Enables token usage tracking
        ),
    )

    # ── Event handlers ─────────────────────────────────────────────────────

    first_message_mode = agent_config.get("first_message_mode", "assistant-speaks-first")
    recording_consent_plan = agent_config.get("recording_consent_plan", "none") or "none"
    _CONSENT_NOTICE = "This call may be recorded for quality and training purposes."

    # ── Recording is NOT implemented (audit FIX 5, Option B) ─────────────────
    # Call audio is never captured and recording_url is never written. Asking a
    # caller to consent to a recording that does not exist is a trust/legal
    # problem, so the consent prompt is force-disabled at runtime regardless of
    # the stored recording_consent_plan. The admin field is left intact; when
    # real recording (LiveKit Egress → Supabase recordings/) is built in a later
    # batch, flip RECORDING_IMPLEMENTED to True and this suppression lifts itself.
    RECORDING_IMPLEMENTED = False
    if not RECORDING_IMPLEMENTED and recording_consent_plan != "none":
        log.warning(
            "Recording is not implemented — ignoring recording_consent_plan=%s and NOT asking "
            "the caller to consent to a recording that will not happen (audit FIX 5, Option B).",
            recording_consent_plan,
        )
        recording_consent_plan = "none"

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport_ref, participant_id: str) -> None:
        """
        Greet the caller and start the silence watchdog.

        IMPORTANT: silence watchdog starts HERE (not at room-connect) so the
        agent greeting time (~4-5s) is not counted toward the silence timeout.
        Previously last_activity_ts was set at __init__ and the 10s clock started
        at call-connect — the greeting ate ~5s, leaving callers only ~5s to speak
        before the call was axed.
        """
        # Reset activity clock to NOW (participant just joined, greeting about to play)
        call_logger.last_activity_ts = time.time()

        # Start the silence watchdog NOW — after participant joins — so greeting
        # time is excluded from the idle clock.
        if not _silence_watchdog_task:  # only start once
            _silence_watchdog_task.append(
                asyncio.create_task(
                    _enforce_silence_timeout(task, call_logger, silence_timeout_seconds, end_call_message)
                )
            )
            watchdog_tasks.append(_silence_watchdog_task[0])

        if recording_consent_plan == "require":
            log.info("Participant joined: %s — asking for recording consent before proceeding.", participant_id)
            call_logger.begin_consent_gate(
                decline_message=(
                    "No problem — this call will not be recorded. Unfortunately I can't "
                    "continue without your consent, so I'll have to end the call here. Goodbye."
                ),
                resume_message=first_message if first_message_mode != "wait" else None,
            )
            await task.queue_frames([
                TTSSpeakFrame(f"{_CONSENT_NOTICE} Is that okay with you?", append_to_context=False)
            ])
            return

        effective_first_message = first_message
        if recording_consent_plan == "inform":
            effective_first_message = f"{_CONSENT_NOTICE} {first_message}"

        if first_message_mode == "wait":
            log.info("Participant joined: %s — mode=wait, staying silent until caller speaks.", participant_id)
            context.add_message({"role": "assistant", "content": effective_first_message})
            return
        # Timed from _entry_t0, deliberately. Railway stamps log lines at FLUSH
        # time, so several lines can share one millisecond and sub-second maths on
        # those timestamps is meaningless (observed 2026-07-31: one timestamp
        # covering both "prewarm" and "DB work finished in 1.67s"). These deltas are
        # monotonic and in-process, so they are the only trustworthy attribution of
        # the greeting path — measured at 4.42s from agent-ready to audible audio,
        # the single largest chunk of first-call latency, and until now completely
        # opaque: this handler cannot even run until runner.run() starts the
        # transport, which is after DB + provider setup + room join.
        log.info(
            "Participant joined: %s — speaking first message. "
            "[greeting-path] participant_joined at %.2fs after entrypoint",
            participant_id, time.monotonic() - _entry_t0,
        )
        # TTSSpeakFrame (not TextFrame): TTSService handles TTSSpeakFrame as a
        # standalone utterance and synthesizes it immediately. A bare TextFrame
        # queued at the task source is only ever flushed as part of an LLM
        # response turn, so the greeting never got spoken.
        # append_to_context=False because we add it to the context ourselves above.
        context.add_message({"role": "assistant", "content": effective_first_message})
        await task.queue_frames([
            TTSSpeakFrame(effective_first_message, append_to_context=False)
        ])
        # Everything after this point is TTS synthesis + WebRTC delivery, which the
        # caller experiences as continued silence. Pair this number with the probe's
        # wire-side dispatch_to_first_audio_ms to split the greeting path into
        # "our setup" vs "the TTS round trip" — the two have completely different
        # fixes (pre-synthesise vs switch/stream the provider), so guessing which
        # one dominates would send the optimisation the wrong way.
        log.info(
            "[greeting-path] greeting queued for synthesis at %.2fs after entrypoint "
            "(%d chars) — any further delay before audio is TTS TTFB + transport",
            time.monotonic() - _entry_t0, len(effective_first_message),
        )

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport_ref, participant_id: str) -> None:
        """End the pipeline when the caller hangs up and delete the room instantly."""
        log.info("Participant disconnected: %s — ending pipeline and deleting room %s.", participant_id, room_name)
        await task.cancel()
        try:
            async with livekit_api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
                await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
                log.info("Deleted LiveKit room %s on disconnect ✓", room_name)
        except Exception as _del_err:
            log.warning("Could not delete room %s on disconnect (non-fatal): %s", room_name, _del_err)

    # ── Call-length watchdogs (Call Behavior tab) ───────────────────────────
    end_call_message = agent_config.get("end_call_message") or "Thank you for calling. Goodbye!"
    max_duration_seconds = int(agent_config.get("max_duration_seconds", 300) or 300)
    # FIX: default 30s (was 10s) and clamp to ≥20s so the greeting (4-5s) plus
    # a reasonable pause never trips the timeout on a normal call.
    silence_timeout_seconds = max(int(agent_config.get("silence_timeout_seconds", 30) or 30), 20)

    # Give the logger a way to end the call directly (used for closing-intent
    # detection — see CallLoggerProcessor._on_user_speech).
    call_logger.task = task

    # …and a way to know when audio has genuinely finished playing, so the silence
    # timer restarts and a goodbye hangs up only after the caller has heard the
    # whole sentence.
    call_logger.set_playout_drain(_make_playout_drain(transport))

    # Let the never-silence guard inject a spoken phrase via the source on error.
    resilience.bind_task(task)

    # Silence watchdog is started AFTER the first participant joins (see
    # on_first_participant_joined below) so the greeting time is not counted.
    # max_duration watchdog starts immediately (measures total call wall-time).
    _silence_watchdog_task: list = []  # mutable container so the closure can populate it

    watchdog_tasks = [
        asyncio.create_task(
            _enforce_max_duration(task, max_duration_seconds, end_call_message, call_logger)
        ),
        asyncio.create_task(_monitor_event_loop_lag(room_name)),
    ]

    # ── Run ────────────────────────────────────────────────────────────────
    # handle_sigint=False: the livekit-agents worker runs each job in its own
    # subprocess/thread and owns process lifecycle + signal handling. Letting
    # PipelineRunner install its own SIGINT handler crashes with
    # "signal only works in main thread" (and is unnecessary — the worker
    # already handles graceful shutdown).
    runner = PipelineRunner(handle_sigint=False)
    try:
        # The room join was kicked off before the services were built; it has to
        # have completed before the transport starts publishing audio into it.
        await _connect_task
        log.info(
            "Agent ready %.2fs after entrypoint | room=%s",
            time.monotonic() - _entry_t0, room_name,
        )
        await runner.run(task)
    finally:
        for t in watchdog_tasks:
            if not t.done():
                t.cancel()
        # The CallRecord INSERT was moved off the greeting path; make sure it has
        # landed before finalisation tries to UPDATE that same row, or a call that
        # ends within a second or two would finalise a row that doesn't exist yet.
        try:
            await asyncio.wait_for(_record_task, timeout=10.0)
        except Exception as exc:
            log.warning("CallRecord insert did not complete before teardown: %s", exc)
        # Keep the job process alive until the CallRecord is finalized. The
        # end/cancel frame schedules finalization as a task; without this await
        # the process could exit before duration/transcript/latency persist
        # (audit FIX 3 — call_records must finalize on real hangups).
        try:
            ok = await call_logger.wait_finalized(timeout=10.0)
            log.info("Finalization %s for room=%s", "completed" if ok else "TIMED OUT", room_name)
        except Exception as exc:
            log.error("Error awaiting finalization for room=%s: %s", room_name, exc)

    log.info("Pipeline finished for room=%s", room_name)


def _make_playout_drain(transport, timeout: float = 6.0):
    """Build an awaitable that waits for LiveKit to finish PLAYING queued audio.

    pipecat's BotStoppedSpeakingFrame fires when the output transport has written
    the last audio chunk, but LiveKit's rtc.AudioSource is constructed with a
    1000ms queue and capture_frame() self-paces against it — so up to a second of
    speech can still be unplayed at that moment. Hanging up or restarting the
    silence timer there clips the tail of the agent's last sentence (measured on a
    live call: a ~2s goodbye reported "stopped speaking" 1.26s in).

    rtc.AudioSource.wait_for_playout() is the real end-of-audio signal. Reaching
    it means touching pipecat's private transport internals, which is why every
    step is guarded and a failure simply means "don't wait" — the same
    best-effort approach LiveKitTranscriptPublisher takes to resolve the room.
    """
    async def drain() -> None:
        try:
            output = transport.output()
            source = getattr(getattr(output, "_client", None), "_audio_source", None)
            if source is None or not hasattr(source, "wait_for_playout"):
                return
            queued = getattr(source, "queued_duration", 0) or 0
            if queued <= 0:
                return
            await asyncio.wait_for(source.wait_for_playout(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("LiveKit playout drain exceeded %.0fs — continuing.", timeout)
        except Exception as exc:
            log.debug("LiveKit playout drain unavailable (non-critical): %s", exc)

    return drain


async def _monitor_event_loop_lag(
    room_name: str,
    sample_interval: float = 0.5,
    warn_threshold: float = 0.15,
) -> None:
    """Log whenever the event loop is starved, with the size of the stall.

    Stuttering TTS on this stack has exactly one mechanism: LiveKit's AudioSource
    is created with a 1000ms queue and its capture_frame() self-paces, so pipecat
    keeps roughly a second of audio buffered ahead. The caller therefore only
    hears a gap when the event loop is blocked long enough to drain that — i.e.
    hundreds of milliseconds to a second, not the ~40ms of a chunk boundary.

    That makes "is the loop stalling, and for how long" the one measurement worth
    having, and it is not something a code read can answer. This sampler sleeps
    for a known interval and reports the overshoot, so a real call leaves evidence
    in the logs instead of an argument. Cost is two wakeups a second and a
    subtraction; it only logs when it has something to report.
    """
    try:
        worst = 0.0
        while True:
            before = time.monotonic()
            await asyncio.sleep(sample_interval)
            lag = time.monotonic() - before - sample_interval
            if lag >= warn_threshold:
                worst = max(worst, lag)
                log.warning(
                    "Event loop stalled %.0fms (worst %.0fms this call) — audio "
                    "output can gap when this approaches 1000ms | room=%s",
                    lag * 1000, worst * 1000, room_name,
                )
    except asyncio.CancelledError:
        if worst:
            log.info("Worst event-loop stall this call: %.0fms | room=%s", worst * 1000, room_name)


async def _enforce_max_duration(
    task: "PipelineTask",
    max_duration_seconds: int,
    end_call_message: str,
    call_logger: "CallLoggerProcessor | None" = None,
) -> None:
    """Ends the call once it has run longer than the agent's configured ceiling."""
    from backend.agent.processors.call_logger_processor import speak_and_end_call

    try:
        await asyncio.sleep(max_duration_seconds)
        log.info("Max call duration (%ss) reached — ending call.", max_duration_seconds)
        await speak_and_end_call(
            task,
            end_call_message,
            wait_playback=call_logger.wait_playback_complete if call_logger else None,
        )
    except asyncio.CancelledError:
        pass


async def _enforce_silence_timeout(
    task: "PipelineTask",
    call_logger: "CallLoggerProcessor",
    silence_timeout_seconds: int,
    end_call_message: str,
) -> None:
    """Ends the call if the CALLER goes silent for longer than configured.

    "Silent" means the caller is not speaking AND the agent is not speaking to
    them. The timer previously ran continuously and was only reset when TTS
    STARTED, so the whole playback of an answer counted as caller silence: a
    multi-sentence reply (doctor names, dates, slots) could exceed the timeout and
    hang up on a caller who was simply listening, mid-sentence.

    Three things fix that, all keyed on something real rather than an estimate:
      * while call_logger.bot_speaking is True the timer does not advance at all;
      * while call_logger.action_in_progress is True the timer does not advance
        either — set by VoiceActionProcessor while a booking/cancel/reschedule
        write, or the corrected reply that follows one, is in flight. Measured
        live 2026-08-12: a booking round trip plus one repair pass is 2-3 LLM
        calls end to end, and without this the caller's own request could
        outrun the timeout and get their call ended mid-booking — punished for
        the system being slow, not for having gone quiet.
      * BotStoppedSpeakingFrame — pushed from the output transport's audio task
        after the audio has drained — resets last_activity_ts, so the countdown
        starts from the moment the caller could actually begin replying.
    """
    from backend.agent.processors.call_logger_processor import speak_and_end_call

    try:
        while True:
            await asyncio.sleep(2.0)

            # The agent is talking, or the system is working on the caller's own
            # request: the caller is not silent either way. Keep the clock pinned
            # to now so no silence accrues.
            if call_logger.bot_speaking or call_logger.action_in_progress:
                call_logger.last_activity_ts = time.time()
                continue

            idle_seconds = time.time() - call_logger.last_activity_ts
            if idle_seconds >= silence_timeout_seconds:
                log.info(
                    "Silence timeout (%ss) reached with no caller speech — ending call.",
                    silence_timeout_seconds,
                )
                await speak_and_end_call(
                    task, end_call_message, wait_playback=call_logger.wait_playback_complete
                )
                return
    except asyncio.CancelledError:
        pass


# ── Worker bootstrap ──────────────────────────────────────────────────────────

# Single source of truth for the dispatch name — shared with
# backend/routers/web_calls.py and the API's pre-warm probe. If these ever
# disagree, dispatched calls connect but no agent ever joins (audit FIX 1.2), so
# the constant lives in one import-free module rather than being re-typed here.
from backend.agent.agent_name import AGENT_NAME

_PLACEHOLDER_LK_URL = "wss://your-project.livekit.cloud"


def prewarm(proc) -> None:
    """Pay the one-off import/model costs at WORKER BOOT instead of mid-call.

    livekit-agents calls this once per job process, before any job is assigned.
    It used to be a no-op with a comment saying pipecat warms itself — which is
    true of the Silero weights but not of everything else the first caller was
    quietly funding. From the 2026-07-29 09:12 production call:

        09:12:02.752  Agent entrypoint
        09:12:04.929  "Database engine: Supabase PostgreSQL"   ← 2.2s

    That 2.2s is SQLAlchemy + asyncpg + the model modules being imported for the
    first time, inside the entrypoint, while a human sits in a silent room. The
    same applies to the Silero ONNX session: constructing SileroVADAnalyzer loads
    and initialises the model, and on Render's 0.1-CPU plan that is not free.

    Everything here is best-effort — a warm-up failure must never stop a worker
    from booting, because a worker that refuses to boot takes every call down
    with it, whereas a cold one merely starts slowly.
    """
    t0 = time.monotonic()

    try:
        import backend.db  # noqa: F401  — SQLAlchemy engine + asyncpg import
        from backend.services import provider_status  # noqa: F401

        # EVERY model, via the one canonical loader — never a hand-picked list.
        # This used to import five modules by name (AgentConfig, CallRecord,
        # Doctor, KnowledgeBase, Tenant), which is how the worker lost its
        # entire database on 2026-08-10: the availability-engine commit gave
        # Doctor a relationship to DoctorAvailability, that module was not on
        # the list, and nothing else in the worker imported it. SQLAlchemy
        # resolves relationship targets by CLASS NAME at mapper-configure time,
        # so the first ORM query raised "expression 'DoctorAvailability' failed
        # to locate a name" — and, because a failed configure_mappers() is
        # cached, EVERY later query in the process raised too. See
        # _verify_orm_registry_or_die for the guard that now makes this loud.
        backend.db._import_all_models()
        log.info("Prewarm: DB layer imported (%.2fs)", time.monotonic() - t0)
    except Exception as exc:
        log.warning("Prewarm: DB layer import failed (non-fatal): %s", exc)

    # NO DB connection warm-up here, deliberately. prewarm_fnc is synchronous, so
    # opening one means asyncio.run() and a throwaway event loop — and asyncpg
    # connections are BOUND to the loop that created them. With DB_POOL_SIZE set
    # (backend/db.py) that connection would be parked in the pool and later handed
    # to a job running on a different loop, which is a much worse failure than the
    # handshake it saves. The live worker refused it outright:
    #   "Prewarm: DB connection warm-up failed: Task <Task pending …_touch()…>"
    # The pool already solves this properly — the first call pays the handshake
    # once and every call after it reuses the connection.

    try:
        t1 = time.monotonic()
        # Constructing the analyzer is what loads + initialises the ONNX session.
        # Discarded immediately; onnxruntime and the weights stay in the process,
        # so the per-call construction in entrypoint() becomes cheap.
        SileroVADAnalyzer(params=VADParams(stop_secs=0.2, start_secs=0.2, confidence=0.55))
        log.info("Prewarm: Silero VAD model loaded (%.2fs)", time.monotonic() - t1)
    except Exception as exc:
        log.warning("Prewarm: Silero VAD load failed (non-fatal): %s", exc)

    log.info("Agent worker pre-warmed in %.2fs.", time.monotonic() - t0)


def _verify_orm_registry_or_die() -> None:
    """Fail LOUDLY at worker boot if the ORM model registry is incomplete.

    Why this exists, and why it is fatal rather than a warning:

    SQLAlchemy resolves ``relationship("SomeClass")`` targets by class name the
    first time any mapper is used. If the module defining that class was never
    imported in THIS process, configure_mappers() raises — and the failure is
    cached, so every subsequent ORM query in the process raises
    "One or more mappers failed to initialize" forever. The worker keeps
    running and keeps answering calls; it simply has no database.

    That is exactly what shipped on 2026-08-10. ``Doctor`` gained
    ``availability_windows -> DoctorAvailability``; the API process registers
    every model through ``init_db() -> _import_all_models()``, but the agent
    worker imported five model modules by name and DoctorAvailability was not
    one of them. Consequences, all silent:

      * _load_tenant_and_config fell into its except branch, so the agent ran
        on room metadata only — no doctors, no clinic_info working hours, no
        knowledge base, no DB provider keys, no credit gate;
      * the prompt then told every caller "no doctors have been added to this
        clinic yet", while the chat channel answered the same question with
        real names off the same tables;
      * BookingProcessor could not match a doctor, so no voice call could book
        anything;
      * CallLoggerProcessor could not write, so transcripts and turn counts
        stopped persisting (verified in production: every call before
        2026-08-10 has a transcript, the calls after it have none).

    A worker with no database is strictly worse than a worker that refuses to
    boot: a dead worker is visible in seconds and fails over, whereas this one
    quietly told patients their clinic had no doctors. So: die.
    """
    try:
        from sqlalchemy.orm import configure_mappers

        import backend.db as _db

        _db._import_all_models()
        configure_mappers()
    except Exception as exc:
        log.critical(
            "FATAL: the ORM model registry is incomplete in this process — %s: %s. "
            "Every database read and write in this worker would fail (SQLAlchemy "
            "caches a failed mapper configuration), so the agent would answer calls "
            "with no doctors, no clinic details and no ability to book. This almost "
            "always means a model gained a relationship to a class whose module is "
            "not listed in backend/db.py::_import_all_models. Add it there and "
            "restart.",
            type(exc).__name__, exc,
        )
        raise SystemExit(1)
    log.info("Preflight OK — ORM model registry complete and all mappers configured.")


def _preflight_or_die() -> None:
    """Fail LOUDLY before the worker starts if it can't possibly register with
    LiveKit (audit FIX 1.4 — never start silently and never pick up calls).

    A missing/placeholder LiveKit URL/key/secret is a fatal misconfiguration:
    the worker would otherwise appear to boot but never register, so every
    dispatched call would connect to a room no agent ever joins.
    """
    missing = [
        name for name, val in (
            ("LIVEKIT_URL", settings.livekit_url),
            ("LIVEKIT_API_KEY", settings.livekit_api_key),
            ("LIVEKIT_API_SECRET", settings.livekit_api_secret),
        )
        if not (val or "").strip()
    ]
    placeholder = settings.livekit_url.strip() == _PLACEHOLDER_LK_URL
    if missing or placeholder:
        reason = (
            f"placeholder LIVEKIT_URL ({_PLACEHOLDER_LK_URL})" if placeholder
            else f"missing {', '.join(missing)}"
        )
        log.critical(
            "FATAL: agent worker cannot register with LiveKit — %s. Refusing to start "
            "(a silently-started worker would never pick up any call). Set the LiveKit "
            "credentials and restart.", reason,
        )
        raise SystemExit(1)
    log.info("Preflight OK — LiveKit creds present; registering worker as agent_name=%s", AGENT_NAME)
    # Checked here too, not only in prewarm(): prewarm is best-effort by design
    # (a warm-up failure must not stop a worker booting), so it can only warn.
    # An unusable ORM is not a warm-up problem — it is a broken worker.
    _verify_orm_registry_or_die()


if __name__ == "__main__":
    import os as _os
    from livekit.agents import WorkerOptions, JobExecutorType, cli

    _preflight_or_die()
    _port = int(_os.environ.get("PORT") or 8081)
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=AGENT_NAME,
            ws_url=settings.livekit_url or None,
            api_key=settings.livekit_api_key or None,
            api_secret=settings.livekit_api_secret or None,
            host="0.0.0.0",
            port=_port,
            job_executor_type=JobExecutorType.THREAD,
            initialize_process_timeout=60.0,
            num_idle_processes=0,
            load_threshold=float("inf"),
        )
    )
