"""
backend/services/agent_defaults.py — the ONE language field, the locked LLM
provider, and the whitelist of STT/TTS providers a dropdown may offer.

Why this module exists
----------------------
Two separate stakeholder-visible failures, both caused by offering a choice
instead of making one.

**1. An agent had four disagreeing languages at once.**

Verified against the live database on 2026-08-03 for agent
``f367e0e2-4e31-41fd-8a4a-df0f6ebbd8d7`` (clinic ``kmct``, agent
``Receptionist``) — the agent in the stakeholder's screenshot::

    tts_language = 'ml-IN'      -> "SELECTED VOICE" header AND the "LANGUAGE" field
    stt_language = 'ta-IN'      -> the Test Agent widget's own language tag
    tts_voice    = 'shruti'     -> the "VOICE" field, whose label came from the
                                   voice CATALOG's static per-voice language: hi-IN

So the header said Malayalam, the Voice field said Hindi, the Language field said
Malayalam, and the live widget said Tamil. Nothing was out of sync in the UI
sense — every one of those four was faithfully rendering a *different* stored (or
catalog-derived) value. This was a DATA MODEL bug, not a display bug:
``agent_configs`` had two independently editable language columns, and the voice
catalog contributed a third de-facto source.

It was not cosmetic. ``backend/agent/pipeline.py`` pinned STT to ``stt_language``
and TTS to ``tts_language``, so that row transcribed the caller as **Tamil** and
answered in **Malayalam**. A Malayalam caller could not be understood.

**2. Provider + model were free-form pairs, so invalid pairs got stored.**

The same row had ``llm_provider='groq'`` with ``llm_model='gemini-2.5-flash-8b'``
— a Gemini model name pointed at Groq. Probed live on 2026-08-03: Groq answers
``HTTP 404 model_not_found`` for it. That agent's LLM was simply broken. The
wizard's own defaults were ``llm_provider='openai'`` / ``llm_model='gpt-4o'``
while no ``OPENAI_API_KEY`` is configured at all, so every *new* agent was born
with a dead LLM too.

The fix for the first is one language field. The fix for the second is narrower
than it first looks, and the difference matters:

* **The LLM PROVIDER choice is removed outright.** It is locked to Groq here,
  applied by the API to every agent, and shown in no dropdown. The LLM *model*
  remains a per-agent choice, but from a list fetched LIVE from Groq's own API
  (``backend/services/groq_catalog.py``) rather than a hardcoded one — a hardcoded
  list is how the product came to offer four models Groq had decommissioned.

* **STT and TTS keep their provider/model choice** — but the *option list* is now
  a whitelist of providers that are genuinely configured AND genuinely buildable,
  instead of the aspirational catalogue the dropdowns used to render. That is what
  actually caused the invalid-pair bug: not the existence of a choice, but
  offering options that could not work.

  This is a deliberate reversal of an earlier revision of this module, which
  locked all three. The stakeholder's final decision was LLM-only, because
  switching STT/TTS provider is the product's fallback story when one vendor
  degrades — losing that is a reliability regression, not a simplification.
  Removing an *invalid* option and removing the *ability to choose* are different
  fixes for different problems.

The provider choices, and the evidence for each
----------------------------------------------
All were probed live against the keys in ``.env`` on 2026-08-03.

* **LLM — PROVIDER locked to groq; MODEL selectable, defaulting to
  llama-3.3-70b-versatile.** Groq per explicit stakeholder preference and prior
  Gemini reliability problems in this project.

  The default is the model this project has actually run on throughout its history.
  An earlier version of this note justified it as "booking is a tool-calling task
  and reliability there matters" — **that reasoning was wrong** and is recorded here
  so it is not repeated: verified 2026-08-04, there is no ``register_function``, no
  ``tools=`` and no ``FunctionSchema`` anywhere in ``backend/agent/``. Booking runs
  on ``BookingProcessor``'s regex matching plus an injected ``[BOOKING_RESULT ...]``
  system message; the LLM only ever emits text. The default stands on track record.

  The offered list is live — see ``groq_catalog``, which filters Groq's 15-model
  response down to the ones that can actually hold a conversation using Groq's own
  per-model metadata (Whisper is audio→transcription, Orpheus is text→audio, and the
  prompt-guard entries are 512-token classifiers).

  (Groq's API rejects a ``Python-urllib`` User-Agent with Cloudflare error 1010 —
  that is a client artifact, not an auth failure. Send a normal User-Agent.)

* **STT — DEFAULT deepgram / nova-3; Sarvam AI also selectable.** The product's own UI warned that Sarvam
  "transcribes only after you pause". **That warning is accurate.** Confirmed
  against the installed pipecat-ai 1.5.0 source: ``SarvamSTTService`` never
  constructs an ``InterimTranscriptionFrame`` and exposes no ``interim_results``
  setting, so it emits one final transcript per utterance — pause-triggered — even
  though its transport is a websocket. ``DeepgramSTTService`` constructs
  ``InterimTranscriptionFrame`` and does expose ``interim_results``. Deepgram is
  therefore the only *configured* provider that is genuinely real-time.

  ⚠️ **Deepgram cannot do Malayalam, Punjabi or Odia on any tier.** Probed:
  ``GET /v1/listen?model=nova-3&language=ml`` answers
  ``HTTP 400 "No such model/language/tier combination found."`` ``pipeline.py``
  auto-selects Sarvam for exactly the languages Deepgram refuses (see the guard
  that consults ``stt_catalog.is_supported``), so such a call still works — but
  that substitution is now also **surfaced in the UI** by
  ``language_support()`` below, because a silent substitution is how an operator
  ends up believing they configured something they did not. Sarvam serves all
  three natively, so choosing Sarvam explicitly is the honest configuration for a
  Malayalam clinic.

  The honest consequence, which is a provider limitation and not a bug:
  **a Malayalam agent cannot have real-time interim transcription today.** Its
  only options are Sarvam (pause-triggered) or ElevenLabs (also no interim frames
  in pipecat 1.5.0 — the ``elevenlabs`` entry in the old ``_STT_REALTIME`` set was
  wrong). Languages Deepgram serves natively — English, Hindi, Kannada, Tamil,
  Telugu, Marathi, Bengali, Gujarati — do get real-time.

* **TTS — DEFAULT sarvam / bulbul:v3, and currently the only selectable one.** The only configured TTS with real Indic language
  coverage, and what the Voice Library is built on. Probed: ``ml-IN`` with speaker
  ``shruti`` returns 158 KB of valid audio.

  This also fixes the "no Malayalam voices" complaint: Sarvam's bulbul speakers
  are **language-agnostic**. ``shruti`` spoke Malayalam correctly in that probe
  despite the catalog labelling it ``(hi-IN)``. A per-voice language label was
  never a real constraint — see ``backend/routers/platform.py``.

What is deliberately NOT locked
-------------------------------
* The **voice/speaker** choice and the Voice Library stay exactly as they are.
  The stakeholder preserved them explicitly ("let it be there. no problem.").
* The **STT and TTS provider + model** stay selectable, from the whitelists below.
* The **LLM model** stays selectable, from Groq's live catalogue.

Why ElevenLabs is not on those whitelists even though its key IS set
-------------------------------------------------------------------
``ELEVENLABS_API_KEY`` is present in ``.env``, so a "is a key configured?" check
would offer it. It is excluded anyway, on the stakeholder's explicit instruction
to remove it from these dropdowns, and the exclusion is defensible on its own
merits for STT: verified against the installed pipecat-ai 1.5.0 source,
``ElevenLabsRealtimeSTTService`` constructs no ``InterimTranscriptionFrame``, so
selecting it silently costs real-time transcription. No existing agent uses it for
either function, so removing the option strands nothing.

The honest cost, stated rather than hidden: TTS therefore has exactly ONE
selectable provider, so its dropdown offers a single option. Sarvam is the only
configured TTS with real Indic coverage and the Voice Library is built on it, so
there is no second *good* option to offer — but there is also, consequently, no
TTS fallback provider today. If a TTS fallback is wanted, ElevenLabs is the
configured candidate and belongs back on ``SELECTABLE_TTS_PROVIDERS``.
"""
from __future__ import annotations

# ── LLM: the one locked combination ───────────────────────────────────────────
# Changing a value here changes it for every agent, existing and new, on the next
# write. There is deliberately no per-agent override and no UI that exposes one.

#: UNLOCKED 2026-08-13 on the stakeholder's explicit instruction ("I want to
#: choose gemini models"). Retained as the name of the DEFAULT provider — a new
#: agent still starts on Groq — but it is no longer enforced on write.
#:
#: Both entries in SELECTABLE_LLM_PROVIDERS satisfy the same two conditions the
#: STT/TTS whitelists do: a real build branch exists
#: (provider_registry.BUILDABLE_LLM, and the ``elif`` chain in
#: agent/resilience.py), and a key is genuinely configured. Gemini was already
#: buildable and already first in resilience.PROVIDER_ORDER as the failover — the
#: only thing missing was the ability to choose it deliberately.
LOCKED_LLM_PROVIDER = "groq"

#: Providers the LLM dropdown may offer.
SELECTABLE_LLM_PROVIDERS: tuple[str, ...] = ("groq", "gemini")

#: The starting model per provider. A model is meaningless without its provider —
#: ``llm_provider='groq'`` next to ``llm_model='gemini-2.5-flash-8b'`` is exactly
#: the pair that left a live agent's LLM answering 404, and it is what
#: normalize_provider_choice exists to prevent.
#:
#: Gemini's entry is an ALIAS, not a pinned snapshot. Verified 2026-08-13:
#: ``gemini-2.5-flash`` and ``gemini-2.0-flash`` both still LIST but return 404
#: "no longer available to new users" when called. Google retires dated snapshots
#: on a rolling basis, so a pinned id here is a scheduled outage.
DEFAULT_LLM_MODEL_BY_PROVIDER: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-flash-latest",
}

#: The model a NEW agent starts on, and the repair value for a row holding a model
#: Groq no longer serves. This is a starting value, not a lock: the agent editor has
#: a Model dropdown populated LIVE from Groq's own API
#: (backend/services/groq_catalog.py), and a chosen model is honoured.
#:
#: Only the PROVIDER is locked. Nothing about a clinic makes one vendor the right
#: answer, so that is a platform decision; but call volume and triage complexity do
#: differ per clinic, so the model is theirs to choose.
#:
#: 70b-versatile as the default because it is the model this project has actually
#: run on throughout its history. Note the earlier justification recorded here —
#: "booking is a tool-calling task and reliability there matters" — was WRONG:
#: verified 2026-08-04, nothing in backend/agent/ registers a single LLM tool.
#: Booking is regex + injected system messages. The default stands on track record,
#: not on that reasoning.
DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"

#: Retained alias — several call sites and tests import LOCKED_LLM_MODEL. It is a
#: DEFAULT now, not a lock. Delete once no caller reads it.
LOCKED_LLM_MODEL = DEFAULT_LLM_MODEL

# ── STT / TTS: selectable, from a whitelist ───────────────────────────────────
# These are DEFAULTS for a new agent and the repair value for a row naming a
# provider that is not selectable — not locks.

DEFAULT_STT_PROVIDER = "deepgram"
DEFAULT_STT_MODEL = "nova-3"

DEFAULT_TTS_PROVIDER = "sarvam"
DEFAULT_TTS_MODEL = "bulbul:v3"

#: Kept for one release as aliases: the previous revision of this module exported
#: LOCKED_STT_* / LOCKED_TTS_*, and the frontend mirror + tests import those names.
#: They are DEFAULTS now, not locks. Delete once no caller reads them.
LOCKED_STT_PROVIDER = DEFAULT_STT_PROVIDER
LOCKED_STT_MODEL = DEFAULT_STT_MODEL
LOCKED_TTS_PROVIDER = DEFAULT_TTS_PROVIDER
LOCKED_TTS_MODEL = DEFAULT_TTS_MODEL

#: Providers the STT dropdown may offer. Every entry must satisfy BOTH:
#:   1. ``provider_registry.BUILDABLE_STT`` — the pipeline has a real build branch;
#:   2. a key is genuinely configured.
#: Offering anything else is what let an agent be saved onto a provider that
#: could not be constructed, which surfaced as dead air on the call.
SELECTABLE_STT_PROVIDERS: tuple[str, ...] = ("deepgram", "sarvam")

#: Providers the TTS dropdown may offer. See the module docstring for why this is
#: a one-element tuple rather than an oversight.
SELECTABLE_TTS_PROVIDERS: tuple[str, ...] = ("sarvam",)

#: Display names, so no UI has to hardcode a vendor's spelling. "Sarvam AI" is
#: the stakeholder's own wording.
PROVIDER_LABELS: dict[str, str] = {
    "sarvam": "Sarvam AI",
    "deepgram": "Deepgram",
}

#: Applied when an agent has no resolvable language at all. en-IN rather than
#: hi-IN: it is the one language every configured provider serves natively, so a
#: brand-new agent is never born on a provider/language combination that needs
#: the Sarvam fallback before anyone has chosen anything.
DEFAULT_LANGUAGE = "en-IN"

#: Locked PROVIDER by category. LLM only, and only the provider half — the LLM
#: model is selectable (live from groq_catalog), and STT/TTS have both halves
#: selectable from their whitelists.
LOCKED_BY_CATEGORY: dict[str, tuple[str, str]] = {
    "llm": (LOCKED_LLM_PROVIDER, DEFAULT_LLM_MODEL),
}

#: Selectable providers and the default model per category, for the write-time
#: validator. Kept as data so the router, the dropdown endpoint and the tests all
#: read one definition.
SELECTABLE_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "stt": SELECTABLE_STT_PROVIDERS,
    "tts": SELECTABLE_TTS_PROVIDERS,
    "llm": SELECTABLE_LLM_PROVIDERS,
}

#: Fields the API must never accept from a client any more. They are still
#: COLUMNS (see ``resolve_language``), but they are derived, so honouring a
#: client-supplied value is what allowed the four-way mismatch in the first place.
#: Kept as data rather than scattered ``if`` statements so the create path, the
#: patch path and the tests all read the same list.
#:
#: ``stt_provider``/``stt_model``/``tts_provider``/``tts_model`` are deliberately
#: NOT here: they are selectable again, and are instead *validated* on write by
#: ``normalize_provider_choice`` so an unbuildable or unconfigured value cannot
#: land in the row. Only the two language MIRRORS and the locked LLM pair are
#: computed rather than accepted.
#: ``llm_model`` is NOT here: it is a real choice made in the editor's Model
#: dropdown, so it must be accepted and persisted.
#:
#: ``llm_provider`` was here until 2026-08-13, when the provider was unlocked so
#: Gemini could be chosen. It is now validated on write by
#: ``normalize_provider_choice`` exactly like the STT/TTS pairs, rather than
#: overwritten — same mechanism, so an unbuildable or unconfigured LLM provider
#: still cannot land in the row.
DERIVED_FIELDS: frozenset[str] = frozenset({
    "stt_language", "tts_language",
})

#: What a CREATE additionally refuses to take from the client: ``llm_model``.
#:
#: The wizard has no model dropdown — the Model picker lives in the agent editor —
#: so an ``llm_model`` in a create payload is never a choice anyone made. It is a
#: stale frontend build echoing a field it used to send, and the values observed
#: doing so are exactly the dangerous ones (``gpt-4o`` on a Groq-locked provider).
#: Scrubbing it to DEFAULT_LLM_MODEL is therefore not information loss.
#:
#: This is also why create scrubs where the editor's PATCH refuses: refusing here
#: would let one stale browser tab, or a Groq outage, block onboarding a clinic —
#: and there is no operator intent to honour by refusing.
DERIVED_ON_CREATE: frozenset[str] = DERIVED_FIELDS | {"llm_model"}


def normalize_provider_choice(category: str, provider: str | None, model: str | None) -> tuple[str, str]:
    """Coerce a client-supplied ``(provider, model)`` to something that can run.

    Returns the pair to store. A provider that is not on this category's
    whitelist is replaced by the category default rather than rejected, because
    this also runs over LEGACY rows on their next save: an agent sitting on
    ``elevenlabs`` or ``openai`` must self-heal onto something buildable, not
    start throwing 422s at whoever opens the editor next.

    A blank model gets the default for the chosen provider. Never the *other*
    provider's default: ``deepgram`` + ``bulbul:v3`` is the class of invalid pair
    that put ``llm_provider='groq'`` next to ``llm_model='gemini-2.5-flash-8b'``
    on a live agent and left its LLM answering 404.
    """
    cat = (category or "").strip().lower()
    allowed = SELECTABLE_BY_CATEGORY.get(cat, ())
    prov = (provider or "").strip().lower()
    mdl = (model or "").strip()

    if prov not in allowed:
        prov = allowed[0] if allowed else prov
        mdl = ""  # a model chosen for the rejected provider cannot be kept

    valid = models_for(cat, prov)
    if mdl not in valid:
        mdl = valid[0] if valid else mdl
    return prov, mdl


def llm_catalog_for(provider: str | None):
    """The catalogue module that owns ``provider``'s model list.

    One place to resolve this, so the dropdown endpoint, the write-time validator
    and the self-healing path cannot end up asking different vendors about the
    same model. Both modules expose the same surface — ``fetch_models``,
    ``check_model``, ``cached_ids``, ``LIVE``/``DEAD``/``UNKNOWN`` and an
    ``*Unavailable`` exception — which is what makes them interchangeable here.

    Unknown providers fall back to the default vendor's catalogue: callers reach
    this only after ``normalize_provider_choice`` has already coerced the provider
    to a selectable one.
    """
    from backend.services import gemini_catalog, groq_catalog

    return gemini_catalog if (provider or "").strip().lower() == "gemini" else groq_catalog


def models_for(category: str, provider: str) -> list[str]:
    """Models this provider really serves for this category, in picker order.

    Sourced from the same catalogues the dropdown endpoints serve, so a model can
    never be valid in the UI and invalid on write (or vice versa).
    """
    if category == "tts" and provider == "sarvam":
        from backend.services.sarvam_catalog import SARVAM_TTS_MODELS

        return list(SARVAM_TTS_MODELS)
    if category == "stt" and provider == "sarvam":
        # saaras:v3 FIRST and deliberately: valid[0] is both the picker's default
        # and the repair value for an unrecognised stored model, so reordering this
        # would silently move every agent onto a different STT engine.
        #
        # saaras:v4 is Sarvam's newer transcribe model (their playground's default),
        # offered as a CHOICE rather than forced. It is wire-identical to v3 on this
        # endpoint — same params, same response keys, verbatim-correct Malayalam on
        # a live A/B — and pipecat needs a shim to build it at all; both are
        # documented at backend/agent/pipeline.py::_register_sarvam_v4_with_pipecat.
        #
        # saaras:v4-multispk / saaras:v3-realtime are absent on purpose: Sarvam's
        # request validator names them but the transcribe endpoint rejects them.
        return ["saaras:v3", "saaras:v4", "saarika:v2.5", "saaras:v2.5"]
    if category == "stt" and provider == "deepgram":
        from backend.routers.platform import DEEPGRAM_STT_MODELS

        return list(DEEPGRAM_STT_MODELS)
    return []


def selectable_providers(category: str) -> list[dict[str, str]]:
    """``[{"id", "name"}]`` for one category's provider dropdown.

    The ONE place a provider dropdown's options come from. The old dropdowns read
    ``backend/routers/platform.py::PROVIDERS``, which is deliberately aspirational
    — it lists providers the product would like to support — so it offered
    ElevenLabs, Whisper, PlayHT and Azure alongside the two that work.
    """
    return [
        {"id": p, "name": PROVIDER_LABELS.get(p, p)}
        for p in SELECTABLE_BY_CATEGORY.get((category or "").strip().lower(), ())
    ]


def tts_languages(provider: str | None = None) -> list[dict[str, str]]:
    """Languages one TTS provider can really SPEAK, as ``[{"code", "name"}]``."""
    prov = (provider or DEFAULT_TTS_PROVIDER).strip().lower()
    if prov == "sarvam":
        from backend.services.sarvam_catalog import SARVAM_TTS_LANGUAGES

        return list(SARVAM_TTS_LANGUAGES)
    # No other TTS provider is selectable. Returning Sarvam's set for an unknown
    # provider would be inventing a capability, so this reports none and
    # ``language_support`` degrades to "we cannot vouch for this" rather than lying.
    return []


def supported_languages(tts_provider: str | None = None) -> list[dict[str, str]]:
    """The languages an agent can be set to, as ``[{"code", "name"}]``.

    Sourced from the selected TTS provider's real catalogue, because what the agent
    can SPEAK is the binding constraint — an agent that can hear a language but
    cannot answer in it is not usable. The STT provider's narrower list is
    deliberately NOT the constraint: ``pipeline.py`` routes the languages Deepgram
    cannot serve (Malayalam, Punjabi, Odia) to Sarvam STT automatically, so they
    remain selectable. That substitution is reported by ``language_support`` so it
    is visible rather than silent.
    """
    return tts_languages(tts_provider)


def language_support(
    language: str,
    *,
    stt_provider: str | None = None,
    stt_model: str | None = None,
    tts_provider: str | None = None,
) -> dict:
    """Whether ``language`` genuinely works on the SELECTED providers.

    This is the function behind the honesty requirement: an agent must never
    silently end up in a language its chosen provider cannot handle. It answers
    for both halves separately, because they fail differently:

    * **TTS** — no fallback exists. A language the TTS provider cannot speak means
      the agent literally cannot answer, so this is an error, not a warning.
    * **STT** — ``pipeline.py`` swaps in Sarvam for a language Deepgram refuses, so
      the call still works. That is a real degradation to report (a different
      vendor, and Sarvam is pause-triggered rather than real-time), not a failure.

    Returns a dict the API hands straight to the UI::

        {"language", "name", "tts_ok", "stt_ok", "stt_runtime_provider",
         "realtime", "warnings": [...], "errors": [...]}
    """
    from backend.services import stt_catalog

    code = (language or "").strip() or DEFAULT_LANGUAGE
    stt_prov = (stt_provider or DEFAULT_STT_PROVIDER).strip().lower()
    tts_prov = (tts_provider or DEFAULT_TTS_PROVIDER).strip().lower()
    name = language_name(code)

    warnings: list[str] = []
    errors: list[str] = []

    tts_ok = code in {lang["code"] for lang in tts_languages(tts_prov)}
    if not tts_ok:
        errors.append(
            f"{PROVIDER_LABELS.get(tts_prov, tts_prov)} cannot speak {name} "
            f"({code}), so this agent would be unable to answer callers. Choose a "
            "different language or a different voice provider."
        )

    # Asked against the provider's best tier, matching what the pipeline does: it
    # upgrades a nova-2 row to nova-3 for the languages nova-2 alone refuses, so
    # "can Deepgram do this at all?" is a question about nova-3.
    capability_model = "nova-3" if stt_prov == "deepgram" else (stt_model or None)
    stt_ok = stt_catalog.is_supported(stt_prov, capability_model, code)

    stt_runtime_provider = stt_prov
    if not stt_ok:
        # Mirrors the pipeline's own guard. Keep the two in step.
        if stt_catalog.is_supported("sarvam", "saaras:v3", code):
            stt_runtime_provider = "sarvam"
            warnings.append(
                f"{PROVIDER_LABELS.get(stt_prov, stt_prov)} cannot transcribe {name} "
                f"({code}) on any model, so calls in this language will be "
                f"transcribed by {PROVIDER_LABELS['sarvam']} instead. Select "
                f"{PROVIDER_LABELS['sarvam']} as the transcriber to make that explicit."
            )
        else:
            errors.append(
                f"No configured transcriber supports {name} ({code}), so the agent "
                "would not be able to hear callers."
            )

    # Real-time is a property of the SERVICE, not the pipeline: only these emit
    # interim transcripts. Kept in step with _STT_REALTIME in
    # backend/agent/pipeline.py.
    realtime = stt_runtime_provider in {"deepgram", "assemblyai"}
    if not realtime and not errors:
        warnings.append(
            f"{PROVIDER_LABELS.get(stt_runtime_provider, stt_runtime_provider)} "
            "transcribes only after the caller pauses, so replies will feel slower "
            "than on a real-time transcriber."
        )

    return {
        "language": code,
        "name": name,
        "tts_ok": tts_ok,
        "stt_ok": stt_ok,
        "stt_runtime_provider": stt_runtime_provider,
        "realtime": realtime,
        "warnings": warnings,
        "errors": errors,
    }


def language_name(code: str | None) -> str:
    """Human-readable name for a language code, for prompts and UI labels.

    Falls back to the code itself so an unknown value degrades to something
    readable rather than blank.
    """
    from backend.services.sarvam_catalog import SARVAM_TTS_LANGUAGES

    code = (code or "").strip()
    for lang in SARVAM_TTS_LANGUAGES:
        if lang["code"] == code:
            return lang["name"]

    # stt_catalog covers codes Sarvam TTS does not (en-US, ar-SA, the saaras-only
    # Indic set), which legacy rows may still hold.
    try:
        from backend.services.stt_catalog import LANGUAGE_NAMES

        return LANGUAGE_NAMES.get(code, code) or code
    except Exception:
        return code


def is_supported_language(code: str | None, tts_provider: str | None = None) -> bool:
    """Whether ``code`` is a language this platform can actually run an agent in.

    Gated on what the TTS provider can SPEAK, for the reason in
    ``supported_languages``: the STT side has a fallback, the TTS side does not.
    """
    return (code or "").strip() in {lang["code"] for lang in tts_languages(tts_provider)}


def resolve_language(
    *,
    language: str | None = None,
    tts_language: str | None = None,
    stt_language: str | None = None,
) -> tuple[str, bool]:
    """Collapse the historical language fields into the one canonical value.

    Returns ``(language, was_conflicting)``.

    This is the documented conflict-resolution rule, used both by the Alembic
    data migration and by the API when it reads a row written before the
    migration. The precedence is not arbitrary — each step has a reason:

    1. ``language`` — the canonical column. If it is already set, it wins
       outright; nothing else is consulted.

    2. ``tts_language`` — the winner when the two legacy columns disagree, for
       four independent reasons:

       * it is what the operator actually SAW. Both the "SELECTED VOICE" header
         and the field literally labelled "LANGUAGE" rendered ``tts_language``,
         so it is the closest thing on record to configured *intent*. For the
         kmct agent that is ``ml-IN`` — and the stakeholder's own complaint was
         that Malayalam was missing, which corroborates Malayalam as the intent.
       * it is the only one constrained to a concretely speakable language.
         ``stt_language`` also legally holds ``"auto"``, which TTS cannot use.
       * the voice was chosen against it.
       * it is what the caller HEARS, so it dominates their experience of "what
         language is this agent".

    3. ``stt_language`` — only if TTS had nothing usable. Skipped when it holds
       ``"auto"``, which is a detection mode, not a language.

    4. ``DEFAULT_LANGUAGE``.

    ``was_conflicting`` reports that the two legacy columns held two different
    real languages. The caller uses it to switch that agent to STT auto-detect
    (see ``effective_stt_language``): where the record is genuinely ambiguous,
    letting the provider detect is safer than hard-pinning the caller's
    microphone to the value that just lost the tie-break. Silently pinning
    ``ml-IN`` would have made kmct unable to hear the Tamil it had been
    configured to expect.
    """
    def _clean(v: str | None) -> str:
        return (v or "").strip()

    canonical = _clean(language)
    if canonical and canonical.lower() != "auto":
        return canonical, False

    tts = _clean(tts_language)
    stt = _clean(stt_language)

    # "auto" is a detection mode, never a language.
    tts_real = tts if tts.lower() != "auto" else ""
    stt_real = stt if stt.lower() != "auto" else ""

    conflicting = bool(tts_real and stt_real and tts_real != stt_real)

    return (tts_real or stt_real or DEFAULT_LANGUAGE), conflicting


def effective_stt_language(language: str, *, auto_detect: bool) -> str:
    """The value to store in the derived ``stt_language`` mirror.

    ``"auto"`` when the agent is set to auto-detect, otherwise the one language.
    This is the ONLY place the two concepts are allowed to meet: the agent has one
    *language*, and separately a boolean for whether the transcriber pins to it or
    lets the provider detect. That boolean is the pre-existing
    ``agent_configs.auto_detect_language`` — no new knob was introduced.
    """
    return "auto" if auto_detect else (language or DEFAULT_LANGUAGE)


def apply_locked_defaults(
    target, *, language: str | None = None, llm_model_ok: bool | None = None
) -> str:
    """Settle language + providers on an AgentConfig-shaped object (an ORM instance
    or anything with attributes).

    Returns the resolved canonical language.

    Three different jobs, deliberately in one function so there is exactly one code
    path that can decide any of them and a client cannot write around it:

    1. **Language** — collapse the historical fields to one value and write the two
       derived mirrors. Never accepted from a client.
    2. **LLM** — the PROVIDER is overwritten with the locked value and never
       accepted from a client. The MODEL is kept, because it is a real choice from
       the editor's live Groq dropdown; it is replaced only when absent, or when the
       caller has established that Groq no longer serves it (``llm_model_ok``).
    3. **STT/TTS provider+model** — *kept* if selectable and coherent, repaired if
       not. This is the part that changed when the decision narrowed to LLM-only:
       these used to be overwritten like the LLM pair, which silently discarded a
       deliberate operator choice on every save.

    Called on both create and update, so a legacy row self-heals the first time
    anything about the agent is saved.

    ``llm_model_ok`` is the liveness verdict for whatever model ``target`` is
    carrying, and it must come from the caller: this function is sync and does no
    I/O, while confirming a model means asking Groq. The API resolves it (see
    ``groq_catalog.check_model``) and passes it down. Tri-state on purpose:

    * ``True`` — Groq serves it. Keep it.
    * ``False`` — Groq answered and does not serve it. Repair to
      DEFAULT_LLM_MODEL. This is the self-heal that rescues a row like the real
      kmct one, stuck on ``gemini-2.5-flash-8b`` and answering HTTP 404 on every
      call until someone noticed.
    * ``None`` — not checked, or unverifiable because Groq could not be reached.
      Leave it alone. Blanking a working model because a network call failed would
      manufacture the outage it was trying to avoid.
    """
    resolved, conflicting = resolve_language(
        language=language if language is not None else getattr(target, "language", None),
        tts_language=getattr(target, "tts_language", None),
        stt_language=getattr(target, "stt_language", None),
    )

    # A genuinely ambiguous legacy row goes to auto-detect rather than being
    # pinned to the tie-break winner. See resolve_language.
    if conflicting:
        target.auto_detect_language = True

    target.language = resolved

    # The PROVIDER was locked to Groq until 2026-08-13. It is now a real choice,
    # so it gets the same treatment as STT/TTS: validated against the whitelist,
    # not overwritten. A row naming a provider that is not selectable falls back
    # to the first entry, and its model goes with it — a model is meaningless
    # without its provider.
    #
    # The MODEL is still only replaced when absent, or when the caller has
    # established that the vendor no longer serves it (``llm_model_ok``).
    # Overwriting it unconditionally is what made the dropdown cosmetic in an
    # earlier draft; NEVER overwriting it is what left a dead model in the row
    # forever.
    llm_provider = (getattr(target, "llm_provider", None) or "").strip().lower()
    if llm_provider not in SELECTABLE_LLM_PROVIDERS:
        llm_provider = LOCKED_LLM_PROVIDER
        target.llm_model = ""   # chosen for the rejected provider; cannot be kept
    target.llm_provider = llm_provider

    if not (getattr(target, "llm_model", None) or "").strip() or llm_model_ok is False:
        target.llm_model = DEFAULT_LLM_MODEL_BY_PROVIDER.get(
            llm_provider, DEFAULT_LLM_MODEL,
        )

    # Validated, not overwritten. A row naming a provider that was dropped from
    # the whitelist (elevenlabs, whisper, openai) falls back to the default pair.
    target.stt_provider, target.stt_model = normalize_provider_choice(
        "stt", getattr(target, "stt_provider", None), getattr(target, "stt_model", None)
    )
    target.tts_provider, target.tts_model = normalize_provider_choice(
        "tts", getattr(target, "tts_provider", None), getattr(target, "tts_model", None)
    )

    # Restoring the TTS MODEL dropdown re-opens a pair the model/speaker rosters do
    # not share: bulbul:v2's 7 speakers and bulbul:v3's 37 are disjoint, and an
    # unmatched (speaker, model) pair is a guaranteed Sarvam 400 — i.e. an agent
    # that answers with silence.
    #
    # Repaired ONLY when the speaker is a real Sarvam speaker belonging to a
    # DIFFERENT model. A speaker the catalogue has never heard of is left exactly as
    # it is: it may be a manually-added or cloned voice id (see
    # AgentConfig.add_voice_manually), and overwriting one of those would destroy a
    # real configuration to fix a problem it does not have. The voice choice is the
    # stakeholder-protected field — "let it be there. no problem." — so the bar for
    # touching it is proof that it is broken, not absence of proof that it works.
    if target.tts_provider == "sarvam":
        from backend.services.sarvam_catalog import (
            SARVAM_ALL_VOICES,
            default_voice_for_model,
            is_valid_voice_for_model,
        )

        _voice = (getattr(target, "tts_voice", None) or "").strip()
        _known_to_sarvam = any(v["id"] == _voice for v in SARVAM_ALL_VOICES)
        if _voice and _known_to_sarvam and not is_valid_voice_for_model(_voice, target.tts_model):
            target.tts_voice = default_voice_for_model(target.tts_model)

    # Derived mirrors. Written here and nowhere else.
    target.tts_language = resolved
    target.stt_language = effective_stt_language(
        resolved, auto_detect=bool(getattr(target, "auto_detect_language", True))
    )

    return resolved
