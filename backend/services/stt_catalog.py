"""
backend/services/stt_catalog.py — the single source of truth for which languages
each STT provider can ACTUALLY transcribe, and for translating our stored code
into the value that provider's API expects.

Why this module exists
----------------------
The Transcriber Language dropdown in the agent editor was a hardcoded eight-item
array in frontend/src/pages/superadmin/AgentDetail.tsx::1425::

    ['en-IN','hi-IN','ta-IN','te-IN','ar-SA','en-US',
     'Multilingual (English/Hindi/Regional)','auto-detect']

Two separate, production-visible bugs lived in that one line:

1. **A description label was the stored value.** Selecting "Multilingual
   (English/Hindi/Regional)" tried to write 37 characters into
   ``agent_configs.stt_language``, which is ``varchar(20)``, so every save raised
   ``asyncpg.exceptions.StringDataRightTruncationError: value too long for type
   character varying(20)``. That option could never have worked — the column is
   not too small, the value was never a language code.

2. **Half the remaining options silently transcribed the wrong language.** Every
   code went through ``pipeline._safe_lang``, whose ``_LANG_TO_SARVAM`` table is
   *Sarvam's* eleven codes and whose fallback is ``"hi-IN"``. So ``ar-SA``,
   ``en-US``, ``auto-detect`` and ``od-IN`` all became Hindi, for every provider
   — including Deepgram, which supports ``ar-SA`` and ``en-US`` natively. The
   dropdown said Arabic; the agent listened in Hindi; nothing logged a warning.

Meanwhile the TTS side had already been fixed to serve a real, provider-specific
language catalogue, so the Voice Library could speak Kannada/Malayalam/Marathi/
Bengali/Gujarati/Punjabi/Odia that the transcriber could not be configured to
*hear*. This module closes that gap for STT.

Adding a provider
-----------------
Add one entry to ``STT_PROVIDERS`` below. Nothing in the frontend, the router or
the pipeline needs to change — the dropdown, the write-time validator and the
pipeline's language translation all read from here. That is the same
"one-place-change" contract as backend/services/provider_registry.py.

How each provider's list was established
----------------------------------------
Everything below was verified by live probes on 2026-08-03 against the keys in
.env — not from documentation and not from memory. The probes are reproduced as
tests in backend/tests/test_stt_catalog.py.

* **Deepgram — a real live endpoint exists.** ``GET https://api.deepgram.com/v1/models``
  returns every STT model with its own ``languages`` array, plus a top-level
  ``languages`` dict mapping 179 codes to display names. That is fetched live (see
  ``backend/routers/platform.py::stt_languages``) so the list stays correct if
  Deepgram adds or drops a language; the table here is only the offline fallback.
  Probe results, aggregated per ``canonical_name``::

      nova-3-general   119 codes — incl. hi ta te kn mr bn gu ur ar-SA en-US
                                   NOT ml, NOT pa, NOT or/od, NOT as
      nova-2-general    71 codes — hi and en-* only among Indic
      nova-general      10 codes — English/Spanish only
      enhanced-general  21 codes — adds ta, no other Indic

  ``multi`` is a valid *runtime* value for nova-3 but is deliberately absent from
  the advertised catalogue, so it is added by us rather than discovered.

* **Sarvam — no list endpoint; the validation error enumerates.** ``POST
  https://api.sarvam.ai/speech-to-text`` with a bogus ``language_code`` answers
  with the 24 schema-valid values. Unlike Sarvam TTS (where 12 of 23 codes are
  gated behind "request beta access"), every STT code that the *selected model*
  accepts really answers 200. Support is **model-dependent**, which is the fact
  the old dropdown could not express::

      saarika:v2.5  → unknown + 11 languages
                      hi bn kn ml mr od pa ta te en gu   (all -IN)
      saaras:v3     → unknown + all 23 languages
                      adds as ur ne kok ks sd sa sat mni brx mai doi

  Two traps this encodes:
    - Odia is ``od-IN`` for Sarvam, **not** the ISO ``or-IN`` that
      backend/routers/providers.py::STT_MODELS and
      backend/routers/platform.py::sarvam_languages still claim. ``or-IN`` is not
      one of the 24 accepted values, so those entries never worked.
    - ``ar-SA`` is **not** a Sarvam language at all. STT_MODELS lists it for
      saaras:v3; Sarvam rejects it. It was fabricated.

* **ElevenLabs — no list endpoint; the validation error enumerates.**
  ``GET /v1/speech-to-text/models`` is a 404, and ``GET /v1/models`` returns only
  TTS/STS models. A bogus ``language_code`` on ``POST /v1/speech-to-text`` lists
  ~150 **ISO-639-3 three-letter** codes (``hin``, ``kan``, ``tam``…). This matters:
  the pipeline was sending two-letter codes (``code.split("-")[0]`` → ``hi``,
  ``kn``), which is not a value ElevenLabs accepts. ElevenLabs is also the only
  configured provider that can do Malayalam **and** Punjabi **and** Odia, the
  three Deepgram refuses.

* **OpenAI Whisper — language selection is a no-op, so we do not offer one.**
  ``backend/agent/pipeline.py`` builds ``OpenAISTTService(model="whisper-1")`` and
  passes no ``language`` at all. Offering a picker would be offering a control
  that provably does nothing, so this provider reports auto-detect only.

* **AssemblyAI — auto-detect only, by design.** Its streaming v3 API takes no
  language hint at connect time; see
  ``backend/agent/providers.py::build_assemblyai_stt``.
"""
from __future__ import annotations

from backend.services.provider_registry import (
    DEEPGRAM_LANG_MAP,
    DEEPGRAM_NOVA2_UNSUPPORTED_LANGS,
    DEEPGRAM_NOVA3_MULTI_LANGS,
    DEEPGRAM_UNSUPPORTED_LANGS,
)

# ── The canonical stored code ─────────────────────────────────────────────────
#: What ``agent_configs.stt_language`` holds for "let the provider decide".
#:
#: Deliberately ONE provider-neutral token rather than each provider's own magic
#: string (Sarvam's ``unknown``, Deepgram's ``multi``): switching provider must
#: never invalidate the stored value, and a four-character token can never
#: reintroduce the varchar(20) truncation. Translation to the provider's own
#: value happens in ``to_provider_code`` at pipeline build time.
AUTO = "auto"

#: Legacy/provider-native spellings accepted on read so old rows and the existing
#: /platform/sarvam/languages payload keep working. ``auto-detect`` is what the
#: old dropdown offered; the rest are provider-native auto values.
AUTO_ALIASES: frozenset[str] = frozenset({AUTO, "auto-detect", "unknown", "multi", ""})

#: Hard ceiling for anything written to ``agent_configs.stt_language``. Matches
#: the real column width. The point is NOT to be generous — a value that needs
#: more than this is a label, not a language code, which is exactly the bug that
#: took production down. Longest real code in this module is 7 (``saarika`` codes
#: like ``sat-IN``, Deepgram's ``zh-Hant``).
MAX_CODE_LEN = 20


# ── Display names ─────────────────────────────────────────────────────────────
#: Names for every code this product can store. Deepgram's live payload carries
#: its own names for its 179 codes and those win when present; this table is what
#: labels Sarvam's codes (Sarvam publishes none) and the offline fallback.
LANGUAGE_NAMES: dict[str, str] = {
    "hi-IN": "Hindi",
    "en-IN": "English (India)",
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
    "as-IN": "Assamese",
    "ur-IN": "Urdu",
    "ne-IN": "Nepali",
    "kok-IN": "Konkani",
    "ks-IN": "Kashmiri",
    "sd-IN": "Sindhi",
    "sa-IN": "Sanskrit",
    "sat-IN": "Santali",
    "mni-IN": "Manipuri",
    "brx-IN": "Bodo",
    "mai-IN": "Maithili",
    "doi-IN": "Dogri",
    "ar-SA": "Arabic (Saudi Arabia)",
}


def language_name(code: str) -> str:
    """Display name for a stored code; the code itself when we have no name."""
    return LANGUAGE_NAMES.get(code, code)


# ── Sarvam ────────────────────────────────────────────────────────────────────
#: The 11 languages EVERY Sarvam STT model serves — the `saarika:*` set.
#: Live-probed: all answer 200 on saarika:v2.5. Note ``od-IN`` for Odia.
SARVAM_STT_BASE_LANGS: list[str] = [
    "hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
    "mr-IN", "bn-IN", "gu-IN", "pa-IN", "od-IN",
]

#: The 12 further languages only the `saaras:*` models serve. saarika:v2.5
#: answers ``Language '<code>' is not supported by saarika:v2.5 model.`` for each.
SARVAM_STT_SAARAS_EXTRA_LANGS: list[str] = [
    "as-IN", "ur-IN", "ne-IN", "kok-IN", "ks-IN", "sd-IN",
    "sa-IN", "sat-IN", "mni-IN", "brx-IN", "mai-IN", "doi-IN",
]

#: Sarvam's own auto-detect value. Not a language — the literal string it wants.
SARVAM_AUTO_CODE = "unknown"


#: Sarvam models that IGNORE the language parameter entirely. pipecat's
#: ``MODEL_CONFIGS`` (pipecat/services/sarvam/stt.py) marks saaras:v2.5
#: ``supports_language=False`` and raises if one is supplied — it always
#: auto-detects. Offering it a language list would be offering fake options.
SARVAM_AUTODETECT_ONLY_MODELS: frozenset[str] = frozenset({"saaras:v2.5"})


def _sarvam_langs(model: str | None) -> list[str]:
    """Sarvam's real language set for ``model``.

    Support is model-dependent, so this cannot be a flat list — that is precisely
    what the old single hardcoded array could not express. Anything in the
    ``saaras`` family gets the full 23; ``saarika`` and unknown/blank models get
    the conservative 11 that every model serves. Under-promising is safe here;
    over-promising is a 400 in the middle of a live call.
    """
    m = (model or "").strip().lower()
    if m in SARVAM_AUTODETECT_ONLY_MODELS:
        return []
    if m.startswith("saaras"):
        return [*SARVAM_STT_BASE_LANGS, *SARVAM_STT_SAARAS_EXTRA_LANGS]
    return list(SARVAM_STT_BASE_LANGS)


# ── Deepgram ──────────────────────────────────────────────────────────────────
#: Offline fallback for Deepgram, derived from DEEPGRAM_LANG_MAP minus the codes
#: the live probe proved unusable. Built rather than restated so the two cannot
#: drift: provider_registry.py stays the one place that knows Deepgram's facts.
#:
#: Only used when the live /v1/models fetch fails (no key, network, outage).
def _deepgram_fallback_langs(model: str | None) -> list[str]:
    m = (model or "nova-3").strip().lower()
    is_nova3 = m.startswith("nova-3") or not m
    out: list[str] = []
    for ours, native in DEEPGRAM_LANG_MAP.items():
        base = native.split("-")[0]
        if base in DEEPGRAM_UNSUPPORTED_LANGS:
            continue  # ml / pa — Deepgram serves these on no tier
        if not is_nova3 and base in DEEPGRAM_NOVA2_UNSUPPORTED_LANGS:
            continue  # nova-2 and older 400 on these
        out.append(ours)
    # ar-SA and en-US are real nova-3 languages that DEEPGRAM_LANG_MAP happens
    # not to list (it exists to translate, not to enumerate).
    if is_nova3:
        for extra in ("en-US", "en-GB", "ar-SA"):
            if extra not in out:
                out.append(extra)
    return out


# ── ElevenLabs ────────────────────────────────────────────────────────────────
#: Our stored code -> ElevenLabs Scribe's ISO-639-3 code. Scribe rejects the
#: two-letter codes the pipeline used to send, which is why picking a language
#: for ElevenLabs never took effect.
ELEVENLABS_ISO3: dict[str, str] = {
    "hi-IN": "hin", "en-IN": "eng", "en-US": "eng", "en-GB": "eng",
    "ta-IN": "tam", "te-IN": "tel", "kn-IN": "kan", "ml-IN": "mal",
    "mr-IN": "mar", "bn-IN": "ben", "gu-IN": "guj", "pa-IN": "pan",
    "od-IN": "ori", "as-IN": "asm", "ur-IN": "urd", "ne-IN": "nep",
    "ks-IN": "kas", "sd-IN": "snd", "sa-IN": "san", "sat-IN": "sat",
    "ar-SA": "ara",
}


# ── Provider specs ────────────────────────────────────────────────────────────
class SttProviderSpec:
    """How one STT provider's language support behaves.

    ``languages(model)`` returns our stored codes the provider really serves.
    ``auto_code`` is the provider's own auto-detect/multilingual value, or None
    when the provider takes no language hint at all (then only AUTO is offered).
    """

    __slots__ = ("languages", "auto_code", "auto_label", "live", "note")

    def __init__(self, languages, auto_code, auto_label, live=False, note=""):
        self.languages = languages
        self.auto_code = auto_code
        self.auto_label = auto_label
        self.live = live
        self.note = note


STT_PROVIDERS: dict[str, SttProviderSpec] = {
    "sarvam": SttProviderSpec(
        languages=_sarvam_langs,
        auto_code=SARVAM_AUTO_CODE,
        auto_label="Auto-detect",
        note="Language set depends on the model: saaras:* serves 23, saarika:* serves 11.",
    ),
    "deepgram": SttProviderSpec(
        languages=_deepgram_fallback_langs,
        # nova-3's multilingual model code-switches inside ONE socket, which is
        # what the old "Multilingual (English/Hindi/Regional)" option was reaching
        # for. This is the real value behind that label.
        auto_code="multi",
        auto_label="Multilingual (code-switching)",
        live=True,
        note="Fetched live from GET /v1/models. Deepgram supports no Malayalam, Punjabi or Odia on any tier.",
    ),
    "elevenlabs": SttProviderSpec(
        languages=lambda model: list(ELEVENLABS_ISO3.keys()),
        auto_code=None,  # blank language_code == auto-detect for Scribe
        auto_label="Auto-detect",
        note="Scribe takes ISO-639-3 codes. The only provider here that serves Malayalam, Punjabi and Odia.",
    ),
    # Both of these take no language hint at all — see the module docstring.
    # Offering a picker would be offering a control that does nothing.
    "whisper": SttProviderSpec(
        languages=lambda model: [],
        auto_code=None,
        auto_label="Auto-detect (Whisper always detects)",
        note="pipeline.py builds whisper-1 with no language parameter; selection has no effect.",
    ),
    "openai": SttProviderSpec(
        languages=lambda model: [],
        auto_code=None,
        auto_label="Auto-detect (Whisper always detects)",
        note="pipeline.py builds whisper-1 with no language parameter; selection has no effect.",
    ),
    "assemblyai": SttProviderSpec(
        languages=lambda model: [],
        auto_code=None,
        auto_label="Auto-detect (AssemblyAI always detects)",
        note="AssemblyAI streaming v3 takes no language hint at connect time.",
    ),
}

#: What an STT provider we have never heard of gets. A provider added later by
#: pasting an API key must not render an EMPTY dropdown — that would be a dead
#: form field. Auto-detect is the only honest universal option, and the
#: conservative Indic+English set is what this product exists to serve.
UNKNOWN_PROVIDER_SPEC = SttProviderSpec(
    languages=lambda model: list(SARVAM_STT_BASE_LANGS),
    auto_code=None,
    auto_label="Auto-detect",
    note=(
        "This provider is not in backend/services/stt_catalog.py, so this list is a "
        "conservative default rather than its verified capability. Add a spec there "
        "to serve its real languages."
    ),
)


def spec_for(provider: str | None) -> SttProviderSpec:
    return STT_PROVIDERS.get((provider or "").strip().lower(), UNKNOWN_PROVIDER_SPEC)


# ── The public API ────────────────────────────────────────────────────────────
def stt_language_options(provider: str | None, model: str | None = None) -> list[dict]:
    """Options for the Transcriber Language dropdown, auto-detect first.

    Each entry is ``{"code", "name"}`` where ``code`` is what gets STORED — always
    short, always a real code, never a description.
    """
    spec = spec_for(provider)
    options = [{"code": AUTO, "name": spec.auto_label}]
    for code in spec.languages(model):
        options.append({"code": code, "name": language_name(code)})
    return options


def supported_codes(provider: str | None, model: str | None = None) -> set[str]:
    """Every value that may legitimately be STORED for this provider/model."""
    return {AUTO, *spec_for(provider).languages(model)}


def is_supported(provider: str | None, model: str | None, code: str | None) -> bool:
    """Would selecting ``code`` really work on this provider/model?

    Canonicalizes FIRST, so the legacy spellings callers genuinely produce are
    judged on what they mean rather than how they are written: ``or-IN`` (which
    LanguageSwitchProcessor emits for Odia script) is the same language as Sarvam's
    ``od-IN``, and every auto-detect spelling is AUTO. Validating the raw string
    rejected those as unsupported.
    """
    return canonicalize(code) in supported_codes(provider, model)


def canonicalize(code: str | None) -> str:
    """Coerce a stored/legacy value to the canonical form we keep in the column.

    Folds every auto-detect spelling to ``AUTO`` and repairs the ``or-IN``/``od-IN``
    Odia split. Anything longer than the column can hold is a label, not a code,
    so it degrades to AUTO rather than raising — a bad row must not break a call.
    """
    c = (code or "").strip()
    if c in AUTO_ALIASES:
        return AUTO
    if c == "or-IN":
        return "od-IN"  # ISO spelling of Sarvam's od-IN; same language
    if len(c) > MAX_CODE_LEN:
        return AUTO
    return c


def to_provider_code(
    provider: str | None, model: str | None, code: str | None
) -> str | None:
    """Translate a stored code into what this provider's API actually wants.

    Returns ``None`` when the provider should be given no language at all (its
    auto-detect). This is the function that replaces the old blanket
    ``pipeline._safe_lang``, which coerced every provider's language through
    Sarvam's eleven codes and defaulted the rest to Hindi.
    """
    prov = (provider or "").strip().lower()
    canon = canonicalize(code)
    spec = spec_for(prov)

    # Models that reject a language parameter outright must be given None, not
    # their provider's auto token — pipecat raises for saaras:v2.5 + any language.
    if prov == "sarvam" and (model or "").strip().lower() in SARVAM_AUTODETECT_ONLY_MODELS:
        return None

    if canon == AUTO:
        # "multi" exists only on nova-3. Handing it to nova-2 or an older tier is
        # an HTTP 400, which pipecat's Deepgram handler retries forever without
        # ever transcribing — so those tiers get None (no language parameter),
        # which is Deepgram's own auto-detect.
        if prov == "deepgram" and not (model or "nova-3").strip().lower().startswith("nova-3"):
            return None
        return spec.auto_code

    # A language this provider cannot serve: fall back to its auto-detect rather
    # than to a hardcoded Hindi. Routed back through the AUTO branch above so the
    # per-model rules (nova-2 has no "multi") apply here too. Callers that can
    # switch provider entirely — the pipeline does, for Deepgram + ml/pa — should
    # check is_supported() first and do that instead.
    if canon not in spec.languages(model):
        return to_provider_code(prov, model, AUTO)

    if prov == "deepgram":
        native = DEEPGRAM_LANG_MAP.get(canon, canon)
        base = native.split("-")[0]
        m = (model or "nova-3").strip().lower()
        if m.startswith("nova-3") or not m:
            # "multi" code-switches in one socket; prefer it where nova-3 offers
            # it so a caller moving English → Hindi mid-call costs no reconnect.
            return "multi" if base in DEEPGRAM_NOVA3_MULTI_LANGS else base
        return native
    if prov == "elevenlabs":
        return ELEVENLABS_ISO3.get(canon)
    if prov == "sarvam":
        return canon
    # Unknown provider: hand back the canonical code unchanged. Better than
    # silently substituting a different language.
    return canon
