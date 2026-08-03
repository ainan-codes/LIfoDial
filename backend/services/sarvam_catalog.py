"""
backend/services/sarvam_catalog.py — the single source of truth for Sarvam's
Bulbul TTS catalogue: which languages it can speak and which speakers exist.

Why this module exists
----------------------
The same two facts (Sarvam's languages, Sarvam's speakers) were previously
written down in three places that had already drifted apart:

  * ``SARVAM_VOICES`` in backend/routers/providers.py — 38 speakers, one of which
    (``niharika``) Sarvam does not recognise at all, so its "Play Sample" always
    failed with ``Speaker 'niharika' is not recognized``.
  * ``SARVAM_VOICES_DATA`` in backend/services/model_registry.py — a different,
    shorter list (21 for bulbul:v3) that fed the agent-creation wizard, including
    ``sophia``, which is likewise not a real Sarvam speaker.
  * A hardcoded six-option ``<select>`` in the Voice Library page, which omitted
    Kannada and Marathi *even though voices tagged those already existed in the
    catalogue*, and had no Malayalam at all.

The Voice Library and the agent's Voice Configuration must agree about what
Sarvam can do, so they now both read from here.

Everything below was verified live against the Sarvam API on 2026-08-03 with the
key in .env, not taken from documentation or memory. See the module tests in
backend/tests/test_sarvam_catalog.py for the probes that established each fact.

Facts established by that probe
-------------------------------
* ``POST /text-to-speech`` with an invalid ``model`` answers::

      model: Input should be 'bulbul:v2', 'bulbul:v3-beta' or 'bulbul:v3'

* With an invalid ``target_language_code`` it lists 23 schema-valid codes, but
  only ELEVEN of them actually synthesize. The other twelve answer
  ``Please request beta access to <code> by contacting our support team.`` —
  they are gated, not available, so they are deliberately NOT listed here.
  The eleven GA languages are identical for bulbul:v2 and bulbul:v3 (v2 answers
  ``<code> is only supported by bulbul:v3`` for exactly the twelve gated ones).

* With an invalid ``speaker`` it lists 44 names. Seven of them are bulbul:v2
  only and 400 on v3 (``Speaker 'x' is not compatible with model bulbul:v3``);
  the other 37 are v3 only and 400 on v2. There is no overlap.

* **No speaker is tied to a language.** Every one of the 37 bulbul:v3 speakers
  renders every one of the 11 languages — verified by synthesizing all 11 with
  the same speaker. Sarvam's own docs list speakers with no language column.
  This is why ``language`` below is only a *primary display tag* (kept as it was
  so existing voice cards are unchanged) while ``SARVAM_TTS_LANGUAGE_CODES`` is
  what a voice can actually be asked to speak.
"""
from __future__ import annotations

# ── Languages ─────────────────────────────────────────────────────────────────
#: The languages Bulbul actually synthesizes on our key, in the order they
#: should appear in a picker. Codes are Sarvam's own ``target_language_code``
#: values — note Odia is ``od-IN``, NOT the ISO-639-1 ``or-IN`` used elsewhere in
#: this repo for Sarvam STT; sending ``or-IN`` to TTS is a validation error.
SARVAM_TTS_LANGUAGES: list[dict[str, str]] = [
    {"code": "hi-IN", "name": "Hindi"},
    {"code": "en-IN", "name": "English - Indian"},
    {"code": "ta-IN", "name": "Tamil"},
    {"code": "te-IN", "name": "Telugu"},
    {"code": "kn-IN", "name": "Kannada"},
    {"code": "ml-IN", "name": "Malayalam"},
    {"code": "mr-IN", "name": "Marathi"},
    {"code": "bn-IN", "name": "Bengali"},
    {"code": "gu-IN", "name": "Gujarati"},
    {"code": "pa-IN", "name": "Punjabi"},
    {"code": "od-IN", "name": "Odia"},
]

SARVAM_TTS_LANGUAGE_CODES: list[str] = [lang["code"] for lang in SARVAM_TTS_LANGUAGES]

#: Schema-valid but gated behind "request beta access" — kept only so an error
#: message can tell the difference between "typo" and "not enabled on this key".
SARVAM_TTS_BETA_LANGUAGE_CODES: frozenset[str] = frozenset({
    "as-IN", "brx-IN", "doi-IN", "kok-IN", "ks-IN", "mai-IN",
    "mni-IN", "ne-IN", "sa-IN", "sat-IN", "sd-IN", "ur-IN",
})


# ── Speakers ──────────────────────────────────────────────────────────────────
# The `language` field is the voice's primary display tag only (see module
# docstring): it drives the card's accent chip when no language filter is
# active, and it is what this catalogue has always claimed. It does NOT
# restrict what the voice can speak — that is SARVAM_TTS_LANGUAGE_CODES.
#
# Genders below match Sarvam's published male/female speaker split exactly.

BULBUL_V3_VOICES: list[dict] = [
    {"id": "priya",    "name": "Priya",    "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Soft Hindi female (Recommended)", "recommended": True},
    {"id": "ritu",     "name": "Ritu",     "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Premium Hindi female"},
    {"id": "neha",     "name": "Neha",     "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Young Hindi female"},
    {"id": "simran",   "name": "Simran",   "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Hindi / Punjabi female"},
    {"id": "kavya",    "name": "Kavya",    "model": "bulbul:v3", "language": "kn-IN", "gender": "female", "description": "Kannada / Hindi female"},
    {"id": "ishita",   "name": "Ishita",   "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Polite Hindi female"},
    {"id": "shreya",   "name": "Shreya",   "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Warm professional Hindi"},
    {"id": "tanya",    "name": "Tanya",    "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Modern Hindi female"},
    {"id": "pooja",    "name": "Pooja",    "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Sweet Hindi female"},
    {"id": "roopa",    "name": "Roopa",    "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Kannada / Hindi female"},
    {"id": "kavitha",  "name": "Kavitha",  "model": "bulbul:v3", "language": "ta-IN", "gender": "female", "description": "Tamil / Telugu female"},
    {"id": "suhani",   "name": "Suhani",   "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Soft Hindi female"},
    {"id": "shruti",   "name": "Shruti",   "model": "bulbul:v3", "language": "hi-IN", "gender": "female", "description": "Clear Hindi female"},
    {"id": "rupali",   "name": "Rupali",   "model": "bulbul:v3", "language": "mr-IN", "gender": "female", "description": "Marathi / Hindi female"},
    {"id": "rahul",    "name": "Rahul",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Hindi / English male"},
    {"id": "aditya",   "name": "Aditya",   "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Hindi male"},
    {"id": "ashutosh", "name": "Ashutosh", "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Deep resonant Hindi male"},
    {"id": "rohan",    "name": "Rohan",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Hindi male"},
    {"id": "amit",     "name": "Amit",     "model": "bulbul:v3", "language": "en-IN", "gender": "male",   "description": "Neutral Indian English male"},
    {"id": "dev",      "name": "Dev",      "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Hindi / English male"},
    {"id": "ratan",    "name": "Ratan",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Mature Hindi male"},
    {"id": "varun",    "name": "Varun",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Confident Hindi male"},
    {"id": "manan",    "name": "Manan",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Calm Hindi male"},
    {"id": "sumit",    "name": "Sumit",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Friendly Hindi male"},
    {"id": "kabir",    "name": "Kabir",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Authoritative Hindi male"},
    {"id": "aayan",    "name": "Aayan",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Youthful Hindi male"},
    {"id": "shubh",    "name": "Shubh",    "model": "bulbul:v3", "language": "en-IN", "gender": "male",   "description": "Professional English male", "default": True},
    {"id": "advait",   "name": "Advait",   "model": "bulbul:v3", "language": "mr-IN", "gender": "male",   "description": "Marathi male"},
    {"id": "anand",    "name": "Anand",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Classic Hindi male"},
    {"id": "tarun",    "name": "Tarun",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Energetic Hindi male"},
    {"id": "sunny",    "name": "Sunny",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Casual Hindi male"},
    {"id": "mani",     "name": "Mani",     "model": "bulbul:v3", "language": "ta-IN", "gender": "male",   "description": "Tamil / Hindi male"},
    {"id": "gokul",    "name": "Gokul",    "model": "bulbul:v3", "language": "ta-IN", "gender": "male",   "description": "Tamil / Kannada male"},
    {"id": "vijay",    "name": "Vijay",    "model": "bulbul:v3", "language": "te-IN", "gender": "male",   "description": "Tamil / Telugu male"},
    {"id": "mohit",    "name": "Mohit",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Standard Hindi male"},
    {"id": "rehan",    "name": "Rehan",    "model": "bulbul:v3", "language": "hi-IN", "gender": "male",   "description": "Hindi / Urdu male"},
    {"id": "soham",    "name": "Soham",    "model": "bulbul:v3", "language": "bn-IN", "gender": "male",   "description": "Bengali / Hindi male"},
]

#: bulbul:v2's seven speakers. Disjoint from v3 — sending a v3 speaker to v2 (or
#: vice versa) is a 400, which is exactly how the agent wizard shipped a default
#: of ``anushka`` against a default model of ``bulbul:v3``.
BULBUL_V2_VOICES: list[dict] = [
    {"id": "anushka",  "name": "Anushka",  "model": "bulbul:v2", "language": "hi-IN", "gender": "female", "description": "Warm, natural", "default": True},
    {"id": "manisha",  "name": "Manisha",  "model": "bulbul:v2", "language": "hi-IN", "gender": "female", "description": "Professional"},
    {"id": "vidya",    "name": "Vidya",    "model": "bulbul:v2", "language": "hi-IN", "gender": "female", "description": "Clear, authoritative"},
    {"id": "arya",     "name": "Arya",     "model": "bulbul:v2", "language": "hi-IN", "gender": "female", "description": "Youthful, friendly"},
    {"id": "abhilash", "name": "Abhilash", "model": "bulbul:v2", "language": "hi-IN", "gender": "male",   "description": "Professional"},
    {"id": "karun",    "name": "Karun",    "model": "bulbul:v2", "language": "hi-IN", "gender": "male",   "description": "Warm, deep"},
    {"id": "hitesh",   "name": "Hitesh",   "model": "bulbul:v2", "language": "hi-IN", "gender": "male",   "description": "Energetic"},
]

#: Every real Sarvam speaker, across models.
SARVAM_ALL_VOICES: list[dict] = [*BULBUL_V3_VOICES, *BULBUL_V2_VOICES]

#: What the Voice Library serves when no model filter is applied. bulbul:v3 is
#: the model the product ships on, and this is what the page has always shown.
SARVAM_VOICES: list[dict] = BULBUL_V3_VOICES

#: Model ids Sarvam accepts, from the invalid-model validation error.
SARVAM_TTS_MODELS: list[str] = ["bulbul:v3", "bulbul:v3-beta", "bulbul:v2"]

#: The default speaker per model. Picking a model without also fixing the
#: speaker is a guaranteed 400, so callers that change one must consult this.
SARVAM_DEFAULT_VOICE_BY_MODEL: dict[str, str] = {
    "bulbul:v3": "shubh",
    "bulbul:v3-beta": "shubh",
    "bulbul:v2": "anushka",
}


def voices_for_model(model: str | None) -> list[dict]:
    """Speakers valid for ``model``; the bulbul:v3 roster when unfiltered."""
    if not model:
        return list(SARVAM_VOICES)
    return [v for v in SARVAM_ALL_VOICES if v["model"] == model]


def is_valid_voice_for_model(voice_id: str | None, model: str | None) -> bool:
    """Would Sarvam accept this (speaker, model) pair?"""
    if not voice_id:
        return False
    return any(v["id"] == voice_id for v in voices_for_model(model))


def default_voice_for_model(model: str | None) -> str:
    return SARVAM_DEFAULT_VOICE_BY_MODEL.get(model or "bulbul:v3", "shubh")


def normalize_language(language: str | None, fallback: str = "hi-IN") -> str:
    """Coerce a language code to one Bulbul will actually accept.

    Callers pass through user/DB-supplied codes (``ar-SA`` from the STT picker,
    ``or-IN`` from the STT catalogue, ``ar-AE`` from the clinic form), any of
    which is a 400 from Sarvam. Falling back is what the preview path has always
    done; this just makes every path agree on the list.
    """
    if language in SARVAM_TTS_LANGUAGE_CODES:
        return language
    # `or-IN` is the same language as Sarvam TTS's `od-IN`; accept both spellings.
    if language == "or-IN":
        return "od-IN"
    return fallback
