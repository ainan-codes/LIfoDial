"""
backend/agent/spoken_fallback.py

The sentence the caller hears when the LLM could not produce one.

Why this exists. On a phone call the failure mode of last resort must be SPEECH,
never silence — a caller with a dead line has no way to tell a crashed system
from a thinking one, and they will sit there saying "हेलो? हेलो?" until they hang
up. Measured live on 2026-08-13 (call 7b775fc9): the caller said "जी ठीक है", the
appointment was written to the database at 08:29:00, and the agent never spoke
again. The row exists; the caller does not know it exists.

The mechanism that failed is the one that normally speaks: every recovery path in
VoiceActionProcessor._on_response_end resolves either into text the model has
already streamed, or into ANOTHER LLM generation. When the model twice produces a
reply with nothing speakable in it (a malformed or token-truncated [ACTION:] tag),
the second attempt deliberately stops rather than loop — and stopping meant
saying nothing at all.

So the sentences here are deliberately NOT generated. They are constant strings
pushed straight to TTS, which is what makes them available exactly when the LLM
is the broken part.

**They carry no names, times, doctors or numbers.** That is a deliberate
constraint, not an oversight: interpolating a time into a sentence correctly
across these languages needs per-language number and date formatting, and this
module's whole value is that it cannot fail. Its job is to break the silence and
state the outcome truthfully — the model's own reply, when it works, is what
gives the pretty confirmation with the details in it.

Languages: the ones a real clinic on this platform is configured for today, with
English as the fallback. An unlisted language gets an English sentence spoken by
a voice tuned for that language, which is imperfect — and still strictly better
than a dead line, which is the only alternative.
"""
from __future__ import annotations

#: Outcome keys. Kept as plain strings rather than an enum so a caller that
#: passes an unknown one degrades to NOT_UNDERSTOOD instead of raising — this
#: module runs on the path that exists to stop a call dying, and it must never
#: be the thing that throws.
BOOKED = "booked"
CANCELLED = "cancelled"
RESCHEDULED = "rescheduled"
ACTION_FAILED = "action_failed"
NOT_UNDERSTOOD = "not_understood"

#: The action names VoiceActionProcessor uses -> the success key here.
_SUCCESS_KEY = {
    "BOOK": BOOKED,
    "CANCEL": CANCELLED,
    "RESCHEDULE": RESCHEDULED,
}

_EN = {
    BOOKED: "Your appointment is confirmed. Thank you for calling.",
    CANCELLED: "Your appointment has been cancelled. Thank you for calling.",
    RESCHEDULED: "Your appointment has been moved. Thank you for calling.",
    ACTION_FAILED: (
        "I'm sorry, I could not complete that just now. Please call the clinic "
        "directly and they will help you."
    ),
    NOT_UNDERSTOOD: "Sorry, I did not catch that. Could you say it again?",
}

#: Per-language overrides. Anything missing falls through to _EN.
_PHRASES: dict[str, dict[str, str]] = {
    "en-IN": _EN,
    "hi-IN": {
        BOOKED: "आपकी अपॉइंटमेंट पक्की हो गई है। कॉल करने के लिए धन्यवाद।",
        CANCELLED: "आपकी अपॉइंटमेंट रद्द कर दी गई है। कॉल करने के लिए धन्यवाद।",
        RESCHEDULED: "आपकी अपॉइंटमेंट बदल दी गई है। कॉल करने के लिए धन्यवाद।",
        ACTION_FAILED: (
            "माफ़ कीजिए, मैं यह काम अभी पूरा नहीं कर सका। कृपया क्लिनिक को सीधे "
            "फ़ोन कीजिए, वे आपकी मदद करेंगे।"
        ),
        NOT_UNDERSTOOD: "माफ़ कीजिए, मैं समझ नहीं पाया। कृपया दोबारा कहिए।",
    },
    "ml-IN": {
        BOOKED: "നിങ്ങളുടെ അപ്പോയിന്റ്മെന്റ് ഉറപ്പിച്ചു. വിളിച്ചതിന് നന്ദി.",
        CANCELLED: "നിങ്ങളുടെ അപ്പോയിന്റ്മെന്റ് റദ്ദാക്കി. വിളിച്ചതിന് നന്ദി.",
        RESCHEDULED: "നിങ്ങളുടെ അപ്പോയിന്റ്മെന്റ് മാറ്റി. വിളിച്ചതിന് നന്ദി.",
        ACTION_FAILED: (
            "ക്ഷമിക്കണം, എനിക്ക് ഇത് ഇപ്പോൾ പൂർത്തിയാക്കാൻ കഴിഞ്ഞില്ല. ദയവായി "
            "ക്ലിനിക്കിലേക്ക് നേരിട്ട് വിളിക്കുക."
        ),
        NOT_UNDERSTOOD: "ക്ഷമിക്കണം, എനിക്ക് മനസ്സിലായില്ല. ഒന്നു കൂടി പറയാമോ?",
    },
    "ta-IN": {
        BOOKED: "உங்கள் சந்திப்பு உறுதி செய்யப்பட்டது. அழைத்ததற்கு நன்றி.",
        CANCELLED: "உங்கள் சந்திப்பு ரத்து செய்யப்பட்டது. அழைத்ததற்கு நன்றி.",
        RESCHEDULED: "உங்கள் சந்திப்பு மாற்றப்பட்டது. அழைத்ததற்கு நன்றி.",
        ACTION_FAILED: (
            "மன்னிக்கவும், இதை இப்போது என்னால் முடிக்க முடியவில்லை. தயவுசெய்து "
            "மருத்துவமனையை நேரடியாக அழைக்கவும்."
        ),
        NOT_UNDERSTOOD: "மன்னிக்கவும், புரியவில்லை. மீண்டும் சொல்ல முடியுமா?",
    },
    "kn-IN": {
        BOOKED: "ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ದೃಢಪಟ್ಟಿದೆ. ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು.",
        CANCELLED: "ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದಾಗಿದೆ. ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು.",
        RESCHEDULED: "ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬದಲಾಗಿದೆ. ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು.",
        ACTION_FAILED: (
            "ಕ್ಷಮಿಸಿ, ಇದನ್ನು ಈಗ ಪೂರ್ಣಗೊಳಿಸಲು ನನಗೆ ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು "
            "ಕ್ಲಿನಿಕ್‌ಗೆ ನೇರವಾಗಿ ಕರೆ ಮಾಡಿ."
        ),
        NOT_UNDERSTOOD: "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ.",
    },
    "te-IN": {
        BOOKED: "మీ అపాయింట్‌మెంట్ ఖరారైంది. కాల్ చేసినందుకు ధన్యవాదాలు.",
        CANCELLED: "మీ అపాయింట్‌మెంట్ రద్దు చేయబడింది. కాల్ చేసినందుకు ధన్యవాదాలు.",
        RESCHEDULED: "మీ అపాయింట్‌మెంట్ మార్చబడింది. కాల్ చేసినందుకు ధన్యవాదాలు.",
        ACTION_FAILED: (
            "క్షమించండి, నేను ఇప్పుడు దీన్ని పూర్తి చేయలేకపోయాను. దయచేసి "
            "క్లినిక్‌కు నేరుగా కాల్ చేయండి."
        ),
        NOT_UNDERSTOOD: "క్షమించండి, నాకు అర్థం కాలేదు. మళ్ళీ చెప్పగలరా?",
    },
    "mr-IN": {
        BOOKED: "तुमची अपॉइंटमेंट निश्चित झाली आहे. फोन केल्याबद्दल धन्यवाद.",
        CANCELLED: "तुमची अपॉइंटमेंट रद्द केली आहे. फोन केल्याबद्दल धन्यवाद.",
        RESCHEDULED: "तुमची अपॉइंटमेंट बदलली आहे. फोन केल्याबद्दल धन्यवाद.",
        ACTION_FAILED: (
            "क्षमस्व, मला हे आत्ता पूर्ण करता आले नाही. कृपया क्लिनिकला थेट "
            "फोन करा."
        ),
        NOT_UNDERSTOOD: "क्षमस्व, मला समजले नाही. कृपया पुन्हा सांगा.",
    },
}


def outcome_key(action: str | None, success: bool) -> str:
    """Which sentence an executed action's result deserves."""
    if not success:
        return ACTION_FAILED
    return _SUCCESS_KEY.get((action or "").upper(), ACTION_FAILED)


def sentence(key: str, language: str | None) -> str:
    """The constant sentence for ``key`` in ``language``.

    Never raises and never returns empty: an unknown key becomes
    NOT_UNDERSTOOD, an unknown language becomes English. Both degradations are
    speech, which is the entire point.
    """
    table = _PHRASES.get((language or "").strip(), _EN)
    if key not in _EN:
        key = NOT_UNDERSTOOD
    return table.get(key) or _EN[key]


def supported_languages() -> list[str]:
    """Language codes with real translations, for tests and diagnostics."""
    return sorted(_PHRASES)
