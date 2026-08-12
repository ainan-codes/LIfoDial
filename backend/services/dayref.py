"""
backend/services/dayref.py

Which DAY the caller meant — resolved in code, never by the model.

Why this exists
---------------
Measured on a live call, 2026-08-12: the caller said "कल दोपहर 3 बजे" (tomorrow,
3 PM) with "Today is Wednesday, 12/08/2026" sitting in the system prompt, and the
model wrote ``15/08/2026`` into its ``[ACTION: BOOK…]`` tag. A real appointment was
created three days from the day the caller asked for. Nothing downstream could
catch it: 15/08 is a valid future date, the doctor was open at 3 PM on it, so the
availability gate correctly said yes.

The lesson is not "prompt it harder". Date arithmetic is arithmetic — a language
model is the wrong component for it, and every calendar word a caller can say has
a single, computable answer. So:

  * the relative-day vocabulary of every language this product speaks lives here,
    in ONE map (it was previously split between availability_prompt.py, which had
    English + Devanagari only, and booking_processor.py, which had all ten scripts
    but only to translate them into English words for a parser that then only
    understood English);
  * ``parse_day_string`` resolves whatever the model put in the tag;
  * ``reconcile_requested_date`` compares that against the days the CALLER actually
    said, and the caller wins.

Everything is IST, like every other date in this product (see timeutil).
"""

import datetime as _dt
import logging
import re

logger = logging.getLogger(__name__)

#: Explicit date formats a model or a patient may write. Lives here rather than in
#: his.py so this module can be imported by his.py without a cycle; his.py
#: re-exports it as ``_DATE_FORMATS`` for its existing callers.
DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y",
    # Two-digit years: models write "13/08/26" often enough to matter, and
    # strptime's %y maps 26 -> 2026.
    "%d/%m/%y", "%d-%m-%y",
)

#: Relative-day words -> offset in days from today (IST).
#:
#: Every language the product's STT supports is here, in the script the transcript
#: actually arrives in. This is the whole point: a Malayalam caller says "നാളെ",
#: never "kal", and a map that only knows the romanisation silently resolves their
#: booking to today. The romanised forms stay because callers code-switch.
RELATIVE_DAYS: dict[str, int] = {
    # English / romanised
    "today": 0, "tonight": 0, "this evening": 0, "aaj": 0, "aj": 0,
    "tomorrow": 1, "tmrw": 1, "tomorow": 1, "kal": 1, "kaal": 1,
    "day after tomorrow": 2, "day after": 2, "parso": 2, "parson": 2,
    # "the day after tomorrow", said the long way round. Matched before the bare
    # "kal"/"कल" because the loops below run longest-phrase-first, which is the
    # only thing that stops "kal ke baad" resolving to tomorrow.
    "kal ke baad": 2, "कल के बाद": 2, "कल के बाद का दिन": 2,
    # Hindi / Marathi
    "आज": 0, "आजच": 0,
    "कल": 1, "उद्या": 1,
    "परसों": 2, "परवा": 2,
    # Bengali
    "আজ": 0, "আগামীকাল": 1, "কাল": 1, "পরশু": 2,
    # Gujarati
    "આજે": 0, "આજ": 0, "કાલે": 1, "આવતીકાલે": 1, "પરમદિવસે": 2,
    # Punjabi
    "ਅੱਜ": 0, "ਕੱਲ੍ਹ": 1, "ਕੱਲ": 1, "ਪਰਸੋਂ": 2,
    # Odia
    "ଆଜି": 0, "କାଲି": 1, "ଆସନ୍ତାକାଲି": 1, "ପରଦିନ": 2,
    # Tamil
    "இன்று": 0, "இன்னிக்கு": 0, "நாளை": 1, "நாளைக்கு": 1, "நாளன்று": 2,
    # Telugu
    "ఈరోజు": 0, "ఇవాళ": 0, "రేపు": 1, "ఎల్లుండి": 2,
    # Kannada
    "ಇಂದು": 0, "ಇವತ್ತು": 0, "ನಾಳೆ": 1, "ನಾಡಿದ್ದು": 2,
    # Malayalam
    "ഇന്ന്": 0, "ഇന്ന": 0, "നാളെ": 1, "മറ്റന്നാൾ": 2,
}

#: Weekday names -> the NEXT occurrence of that weekday (never today, never past).
#: Native-script forms included for the same reason as the relative days above.
WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "somvar": 0, "mangalvar": 1, "budhvar": 2, "guruvar": 3,
    "shukravar": 4, "shanivar": 5, "ravivar": 6,
    "सोमवार": 0, "मंगलवार": 1, "बुधवार": 2, "गुरुवार": 3,
    "शुक्रवार": 4, "शनिवार": 5, "रविवार": 6,
    "തിങ്കൾ": 0, "ചൊവ്വ": 1, "ബുധൻ": 2, "വ്യാഴം": 3,
    "വെള്ളി": 4, "ശനി": 5, "ഞായർ": 6,
    "ಸೋಮವಾರ": 0, "ಮಂಗಳವಾರ": 1, "ಬುಧವಾರ": 2, "ಗುರುವಾರ": 3,
    "ಶುಕ್ರವಾರ": 4, "ಶನಿವಾರ": 5, "ಭಾನುವಾರ": 6,
}

#: The English word ``parse_day_string`` and his.parse_slot_datetime speak, for a
#: caller that gave a native-script day. Derived from RELATIVE_DAYS so the two can
#: never disagree.
_OFFSET_TO_ENGLISH = {0: "Today", 1: "Tomorrow", 2: "Day after tomorrow"}


def to_english_day_word(word: str) -> str | None:
    """"कल" -> "Tomorrow". None if `word` is not a relative-day word."""
    offset = RELATIVE_DAYS.get((word or "").strip().lower())
    return _OFFSET_TO_ENGLISH.get(offset) if offset is not None else None


def _says(text: str, word: str) -> bool:
    """Does `text` contain `word` as a WHOLE word, in any script?

    A bare substring test is not safe here and the reason is on the record: Hindi
    "कलम" (pen) contains "कल" (tomorrow), Bengali "কালকে" contains "কাল", and the
    same class of bug once turned "दोपहर के दो बजे" into a 2 AM booking (see
    indic_text._boundaried, which this reuses).

    The asymmetry that makes strictness correct here: a MISSED day word costs
    nothing — the model's own date is then trusted, exactly as before — while a
    FALSE one would overrule the model with a day the caller never said.
    """
    from backend.services.indic_text import _boundaried

    return re.search(_boundaried(word), text or "", flags=re.IGNORECASE) is not None


def _next_weekday(today: _dt.date, weekday: int) -> _dt.date:
    """The next occurrence of `weekday`, never today.

    "Friday" said on a Friday means the Friday coming, not the one that is nearly
    over — and, decisively, this function must never return a past date.
    """
    ahead = (weekday - today.weekday()) % 7 or 7
    return today + _dt.timedelta(days=ahead)


def parse_day_string(text: str, today: _dt.date) -> _dt.date | None:
    """The IST date `text` names, or None if it names no day at all.

    Handles a relative-day word in any supported language, a weekday name, and
    every explicit format in DATE_FORMATS. An empty string is "no day named",
    which callers read as "the caller did not say" — NOT as today.
    """
    s = (text or "").strip()
    if not s:
        return None

    # Longest first, so "day after tomorrow" is never read as "tomorrow".
    for word in sorted(RELATIVE_DAYS, key=len, reverse=True):
        if _says(s, word):
            return today + _dt.timedelta(days=RELATIVE_DAYS[word])

    for name in sorted(WEEKDAY_NAMES, key=len, reverse=True):
        if _says(s, name):
            return _next_weekday(today, WEEKDAY_NAMES[name])

    for fmt in DATE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def dates_in_text(text: str, today: _dt.date) -> list[_dt.date]:
    """Every IST date `text` refers to, in the order the words appear.

    Used two ways: to decide which day's availability to look up, and — the
    important one — to know what day the CALLER actually asked for, so a model
    that miscalculates can be overruled by their own words.
    """
    found: list[_dt.date] = []

    def _add(d: _dt.date) -> None:
        if d not in found:
            found.append(d)

    for word in sorted(RELATIVE_DAYS, key=len, reverse=True):
        if _says(text, word):
            _add(today + _dt.timedelta(days=RELATIVE_DAYS[word]))

    for name in sorted(WEEKDAY_NAMES, key=len, reverse=True):
        if _says(text, name):
            _add(_next_weekday(today, WEEKDAY_NAMES[name]))

    for token in re.findall(r'\b\d{1,4}[/-]\d{1,2}[/-]\d{2,4}\b', text or ""):
        for fmt in DATE_FORMATS:
            try:
                _add(_dt.datetime.strptime(token, fmt).date())
                break
            except ValueError:
                continue

    return found


def note_dates_said(said: list, text: str, today: _dt.date) -> list:
    """Record the days `text` mentions into `said`, most recent LAST.

    A day mentioned again moves to the end rather than being ignored, because
    "the day the caller most recently asked for" is what a correction means: in
    the 2026-08-12 cancel call the caller said "कल", then corrected themselves to
    "पंद्रह तारीख", and the second one is the one that counts.
    """
    for d in dates_in_text(text, today):
        if d in said:
            said.remove(d)
        said.append(d)
    return said


def reconcile_requested_date(
    tag_date: str, said_dates: list, today: _dt.date,
) -> tuple[str, str | None]:
    """Decide the date to actually book, given what the model wrote and what the
    caller said.

    Returns ``(date_string_to_use, correction_note_or_None)``.

    The rules, in order — deliberately conservative, because overruling the model
    on a day the caller never mentioned would be its own kind of invention:

      1. The caller named no day at all -> trust the tag. (They may have given the
         date only in writing, or the model may be carrying it from earlier.)
      2. The tag's date is one the caller DID name -> trust the tag. A caller who
         mentions two days and gets either of them is not being mis-booked.
      3. Otherwise the caller's most recently named day wins, and the disagreement
         is returned so it can be logged.
      4. Never overrule INTO the past.
    """
    if not said_dates:
        return tag_date, None

    parsed = parse_day_string(tag_date, today)
    if parsed is not None and parsed in said_dates:
        return tag_date, None

    chosen = said_dates[-1]
    if chosen < today:
        return tag_date, None

    return (
        chosen.strftime("%d/%m/%Y"),
        f"model wrote {tag_date!r} (={parsed}), caller asked for {chosen}",
    )
