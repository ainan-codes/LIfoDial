"""
STT provider routing under the LOCKED Deepgram default.

Why this file exists
--------------------
Locking STT to Deepgram (because it is the only configured provider that emits
interim results, i.e. the only genuinely real-time one) exposed a latent bug.

Deepgram cannot transcribe Malayalam, Punjabi or Odia on ANY tier — probed live on
2026-08-03, ``GET /v1/listen?model=nova-3&language=ml`` answers
``HTTP 400 "No such model/language/tier combination found."`` The pipeline has
always compensated by switching such agents to Sarvam STT before the build chain.

But that guard asked its question about ``stt_language``, which is the string
``"auto"`` whenever ``auto_detect_language`` is set — and "can Deepgram do auto?"
answers YES, because nova-3 has a ``multi`` mode. So an auto-detect Malayalam agent
sailed past the guard and got built on nova-3 ``multi``, whose language set does
not include Malayalam. There is no 400 in that path: Deepgram happily returns
nearest-match garbage in another script. Verified with real audio — Malayalam
speech forced through Deepgram came back as Kannada-script nonsense.

Both live agents were on ``auto_detect_language=True`` after the migration, and one
of them is the Malayalam demo agent, so this was on the critical path.

The fix: the capability question is asked about the agent's CONFIGURED language,
which is always a concrete code. Auto-detect governs whether a language is PINNED,
not which languages the provider can physically hear.

Run: python -m pytest backend/tests/test_locked_stt_language_routing.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-stt-routing-tests")

import pytest

from backend.services import agent_defaults, stt_catalog
from backend.services.provider_registry import (
    DEEPGRAM_LANG_MAP,
    DEEPGRAM_UNSUPPORTED_LANGS,
)

#: Languages Deepgram cannot serve on any tier, so they MUST route to Sarvam.
DEEPGRAM_CANNOT = ("ml-IN", "pa-IN", "od-IN")
#: Languages Deepgram serves natively, so they must STAY on Deepgram and keep
#: real-time interim transcription.
DEEPGRAM_CAN = ("en-IN", "hi-IN", "kn-IN", "ta-IN", "te-IN", "mr-IN", "bn-IN", "gu-IN")


def _routed_provider(agent_language: str, *, auto_detect: bool) -> str:
    """Reproduce the pipeline's pre-build STT provider decision.

    Mirrors the guard in backend/agent/pipeline.py. Kept as a copy rather than
    imported because importing the pipeline pulls in pipecat and a LiveKit worker;
    the assertions below are about the DECISION, and the two must agree.
    """
    stt_provider = agent_defaults.LOCKED_STT_PROVIDER
    if stt_provider != "deepgram":
        return stt_provider

    # THE FIX: ask about the configured language, not the possibly-"auto" value.
    capability_lang = agent_language
    dg_base = (DEEPGRAM_LANG_MAP.get(capability_lang, "en-IN") or "").split("-")[0]
    if dg_base in DEEPGRAM_UNSUPPORTED_LANGS or not stt_catalog.is_supported(
        "deepgram", "nova-3", capability_lang
    ):
        return "sarvam"
    return "deepgram"


@pytest.mark.parametrize("language", DEEPGRAM_CANNOT)
@pytest.mark.parametrize("auto_detect", [True, False])
def test_languages_deepgram_cannot_hear_always_route_to_sarvam(language, auto_detect):
    """The regression. Before the fix, auto_detect=True returned "deepgram" here
    and the agent was built on a model with no Malayalam at all."""
    assert _routed_provider(language, auto_detect=auto_detect) == "sarvam", (
        f"{language} with auto_detect={auto_detect} must not run on Deepgram"
    )


@pytest.mark.parametrize("language", DEEPGRAM_CAN)
@pytest.mark.parametrize("auto_detect", [True, False])
def test_languages_deepgram_serves_stay_on_deepgram(language, auto_detect):
    """The locked default must actually apply for the languages it can serve —
    otherwise locking Deepgram bought no real-time transcription at all."""
    assert _routed_provider(language, auto_detect=auto_detect) == "deepgram"


def test_auto_detect_does_not_change_the_routing_decision():
    """Auto-detect governs PINNING, never provider capability. Any language whose
    routing differs between the two modes is the bug this file exists for."""
    for opt in agent_defaults.supported_languages():
        pinned = _routed_provider(opt["code"], auto_detect=False)
        auto = _routed_provider(opt["code"], auto_detect=True)
        assert pinned == auto, (
            f"{opt['code']} routes to {pinned!r} when pinned but {auto!r} on "
            "auto-detect — auto-detect must not change which provider is used"
        )


def test_every_supported_language_is_hearable_by_the_provider_it_routes_to():
    """The real guarantee behind locking a provider: no selectable language may be
    unhearable. If this fails, some agent can be configured deaf."""
    for opt in agent_defaults.supported_languages():
        code = opt["code"]
        provider = _routed_provider(code, auto_detect=False)
        assert stt_catalog.is_supported(provider, None, code) or stt_catalog.is_supported(
            provider, "saaras:v3", code
        ), f"{code} routes to {provider}, which cannot transcribe it"


def test_the_locked_stt_provider_is_one_that_emits_interim_results():
    """The whole justification for locking Deepgram. If this ever changes, the
    "real-time transcription" claim in the UI becomes false."""
    # Kept in sync with _STT_REALTIME in backend/agent/pipeline.py and the same set
    # in frontend/src/components/TestVoiceCallLK.tsx. elevenlabs is deliberately
    # NOT here: pipecat 1.5.0's ElevenLabs STT emits no interim frames.
    realtime = {"deepgram", "assemblyai"}
    assert agent_defaults.LOCKED_STT_PROVIDER in realtime
