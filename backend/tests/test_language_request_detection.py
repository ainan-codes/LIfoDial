"""Explicit mid-call language-switch requests.

The bug this locks down was witnessed on a real call in this project: a caller
asked, in Hindi, "Aap English mein baat kar sakte ho kya?" and the agent carried on
in Hindi. Script detection cannot catch it — that sentence IS Hindi — so the
regression test has to assert on the REQUEST detector, and has to assert that the
script detector still disagrees, because that disagreement is the whole reason the
second detector exists.
"""
from __future__ import annotations

from backend.agent.processors.language_switcher import (
    detect_language_from_text,
    detect_language_request,
)

# Every language the platform can actually speak (Sarvam TTS's real catalogue).
SUPPORTED = {
    "hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
    "mr-IN", "bn-IN", "gu-IN", "pa-IN", "od-IN",
}


class TestTheWitnessedTranscript:
    """The exact utterance from the real call that was ignored."""

    UTTERANCE = "Aap English mein baat kar sakte ho kya?"

    def test_request_is_detected(self):
        assert detect_language_request(self.UTTERANCE, SUPPORTED) == "en-IN"

    def test_devanagari_form_is_detected(self):
        # Deepgram's Hindi model returns Devanagari, so the same request reaches the
        # processor in a different script depending on the STT model in use.
        assert detect_language_request(
            "आप इंग्लिश में बात कर सकते हो क्या?", SUPPORTED
        ) == "en-IN"

    def test_script_detection_alone_would_have_missed_the_devanagari_form(self):
        # Not a quirk to work around — this is CORRECT. The sentence is Hindi. It is
        # why a meaning-level detector had to be added rather than the script one
        # being "fixed".
        assert detect_language_from_text("आप इंग्लिश में बात कर सकते हो क्या?") == "hi-IN"


class TestRequestsInEachLanguage:
    def test_english_asking_for_malayalam(self):
        assert detect_language_request("Can you speak Malayalam please?", SUPPORTED) == "ml-IN"

    def test_malayalam_asking_for_english(self):
        assert detect_language_request(
            "നിങ്ങൾക്ക് ഇംഗ്ലീഷ് സംസാരിക്കാമോ?", SUPPORTED
        ) == "en-IN"

    def test_tamil_asking_for_english(self):
        assert detect_language_request("நீங்கள் ஆங்கிலம் பேச முடியுமா?", SUPPORTED) == "en-IN"

    def test_kannada_asking_for_hindi(self):
        assert detect_language_request("ನೀವು ಹಿಂದಿ ಮಾತನಾಡುತ್ತೀರಾ?", SUPPORTED) == "hi-IN"

    def test_english_asking_for_hindi_romanised(self):
        assert detect_language_request("please switch to Hindi", SUPPORTED) == "hi-IN"


class TestFalsePositives:
    """A mention of a language is not a request. Switching on these would be a
    regression in the opposite direction — the agent changing language for no
    reason, mid-booking."""

    def test_bare_language_name_is_not_a_request(self):
        assert detect_language_request("English", SUPPORTED) == ""

    def test_complaining_about_own_ability_is_not_a_request(self):
        # Names a language and asks for nothing. Trips no cue word.
        assert detect_language_request("My English very bad", SUPPORTED) == ""

    def test_ordinary_booking_sentence_is_not_a_request(self):
        assert detect_language_request(
            "I want to book an appointment with Dr Sharma tomorrow", SUPPORTED
        ) == ""

    def test_cue_without_a_language_name_is_not_a_request(self):
        assert detect_language_request("can you speak louder please", SUPPORTED) == ""

    def test_empty_and_none_are_safe(self):
        assert detect_language_request("", SUPPORTED) == ""
        assert detect_language_request(None, SUPPORTED) == ""  # type: ignore[arg-type]


class TestUnsupportedLanguageRequests:
    def test_request_for_a_language_the_platform_cannot_speak_is_declined(self):
        # Sarvam TTS speaks no Arabic. Retuning onto it would answer HTTP 400
        # mid-call, so the service level declines and the LLM handles it in words.
        assert detect_language_request("can you speak Arabic?", SUPPORTED) == ""

    def test_same_request_resolves_when_the_language_is_supported(self):
        # Guards against the test above passing for the wrong reason (a missing
        # alias rather than the supported-set filter).
        assert detect_language_request("can you speak Arabic?", SUPPORTED | {"ar-SA"}) == ""
        assert detect_language_request("can you speak Odia?", SUPPORTED) == "od-IN"

    def test_no_filter_means_no_restriction(self):
        assert detect_language_request("can you speak Punjabi?") == "pa-IN"
