"""
backend/services/indic_text.py

Script-agnostic text helpers for matching what a caller actually said.

The problem this exists to solve: the voice booking state machine
(agent/processors/booking_processor.py) matched caller speech against
lowercase ASCII keyword sets — doctor names, "yes", "my name is", "3 pm".
Sarvam's STT does not return ASCII for an Indic call, it returns the caller's
script. So on a Hindi call:

    caller says   "सलमान"        FSM looks for  "salman"     -> no doctor match
    caller says   "हाँ"          FSM looks for  "haan"       -> no confirmation
    caller says   "मेरा नाम"      FSM looks for  "mera naam"  -> no patient name
    caller says   "ग्यारह बजे"    FSM looks for  r'\\d+ ?(am|pm|baje)' -> no slot

Every one of those is a hard stop in the booking flow, so a voice booking could
only ever complete in romanised/English speech — for a product whose whole
premise is Indian-language reception. The agent config that shipped to
production is a HINDI receptionist.

Two mechanisms here:

1. ``consonant_skeleton`` — collapses a word, in any of the eleven scripts this
   product serves, to a coarse consonant class string. "सलमान" and "Salman"
   both become "SLMN", so a roster stored in Latin matches speech transcribed
   in Devanagari, Malayalam, Kannada and the rest. Vowels are dropped
   (transliteration never agrees on them) and aspiration/place distinctions are
   folded (kh/g/gh all K), because those are exactly what varies between a
   name's spelling and its pronunciation.

   The Unicode trick that makes this compact: the Brahmi-derived blocks
   (Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Telugu, Kannada, Malayalam)
   place consonants at IDENTICAL offsets from their block base. So a single
   Devanagari table serves all of them after subtracting the block offset.
   Tamil is the exception — it has no voiced/aspirated series and its own
   layout — so it gets an explicit table.

2. ``normalise_spoken_numbers`` — rewrites spoken clock numbers and the
   o'clock marker into the ASCII digits the existing time regex already
   understands, in each supported language. "ग्यारह बजे" becomes "11 baje".
"""

from __future__ import annotations

import re
import unicodedata

# ── 1. Consonant skeletons ───────────────────────────────────────────────────

# Base code point of each Brahmi-derived block that shares Devanagari's layout.
_DEVANAGARI = 0x0900
_ALIGNED_BLOCK_BASES = (
    0x0900,  # Devanagari  — Hindi, Marathi
    0x0980,  # Bengali     — Bengali
    0x0A00,  # Gurmukhi    — Punjabi
    0x0A80,  # Gujarati    — Gujarati
    0x0B00,  # Odia        — Odia
    0x0C00,  # Telugu      — Telugu
    0x0C80,  # Kannada     — Kannada
    0x0D00,  # Malayalam   — Malayalam
)
_TAMIL_BASE = 0x0B80

# Devanagari consonant -> coarse class. Deliberately lossy: the classes are
# "same sound to an Indian ear, and the same letter a transliterator might
# pick". K covers ka/kha/ga/gha, T covers BOTH the retroflex and dental series
# (transliteration picks between t and d inconsistently), and so on.
_DEVANAGARI_CLASS: dict[int, str] = {
    0x915: "K", 0x916: "K", 0x917: "K", 0x918: "K", 0x919: "N",   # k kh g gh ng
    0x91A: "C", 0x91B: "C", 0x91C: "C", 0x91D: "C", 0x91E: "N",   # c ch j jh ny
    0x91F: "T", 0x920: "T", 0x921: "T", 0x922: "T", 0x923: "N",   # T Th D Dh N
    0x924: "T", 0x925: "T", 0x926: "T", 0x927: "T", 0x928: "N",   # t th d dh n
    0x929: "N",
    0x92A: "P", 0x92B: "P", 0x92C: "P", 0x92D: "P", 0x92E: "M",   # p ph b bh m
    0x92F: "Y", 0x930: "R", 0x931: "R", 0x932: "L", 0x933: "L",
    0x934: "L", 0x935: "V",
    0x936: "S", 0x937: "S", 0x938: "S", 0x939: "H",               # sh ss s h
    0x958: "K", 0x959: "K", 0x95A: "K", 0x95B: "C", 0x95C: "T",   # nukta forms
    0x95D: "T", 0x95E: "P", 0x95F: "Y",
}

# Tamil has a reduced consonant inventory and its own ordering.
_TAMIL_CLASS: dict[int, str] = {
    0xB95: "K", 0xB99: "N", 0xB9A: "C", 0xB9C: "C", 0xB9E: "N",
    0xB9F: "T", 0xBA3: "N", 0xBA4: "T", 0xBA8: "N", 0xBAA: "P",
    0xBAE: "M", 0xBAF: "Y", 0xBB0: "R", 0xBB1: "R", 0xBB2: "L",
    0xBB3: "L", 0xBB4: "L", 0xBB5: "V", 0xBB6: "S", 0xBB7: "S",
    0xBB8: "S", 0xBB9: "H",
}

# Latin digraphs first — "sh" must not be read as s + h, and "ch" not c + h.
_LATIN_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("chh", "C"), ("ch", "C"), ("sh", "S"), ("kh", "K"), ("gh", "K"),
    ("th", "T"), ("dh", "T"), ("ph", "P"), ("bh", "P"), ("jh", "C"),
    ("zh", "L"), ("ng", "N"), ("ny", "N"), ("ck", "K"), ("qu", "K"),
)
_LATIN_CLASS: dict[str, str] = {
    "k": "K", "c": "K", "g": "K", "q": "K", "x": "K",
    "j": "C", "z": "C",
    "t": "T", "d": "T",
    "p": "P", "b": "P", "f": "P",
    "n": "N", "m": "M",
    "y": "Y", "r": "R", "l": "L",
    "v": "V", "w": "V",
    "s": "S",
    "h": "H",
}


# Malayalam "chillu" letters — word-final consonant forms that sit OUTSIDE the
# block's regular consonant range, so the aligned-offset trick above misses
# them. They are extremely common at the end of Malayalam names: without these,
# "സൽമാൻ" (Salman) reduced to "SM" and matched nothing.
_MALAYALAM_CHILLU: dict[int, str] = {
    0x0D7A: "N", 0x0D7B: "N", 0x0D7C: "R", 0x0D7D: "L", 0x0D7E: "L",
    0x0D7F: "K",
}


def _indic_class(ch: str) -> str | None:
    """Coarse consonant class for one Indic character, or None."""
    cp = ord(ch)
    if cp in _MALAYALAM_CHILLU:
        return _MALAYALAM_CHILLU[cp]
    if _TAMIL_BASE <= cp < _TAMIL_BASE + 0x80:
        return _TAMIL_CLASS.get(cp)
    for base in _ALIGNED_BLOCK_BASES:
        if base <= cp < base + 0x80:
            offset = cp - base
            # Candrabindu (x01) and anusvara (x02) are nasal marks, not
            # consonants, but a romanised spelling writes them as an "n" or "m".
            # Dropping them cost real matches: "कैंसिल" reduced to K-S-L while
            # "cancel" gave K-N-S-L, and "हाँ" lost its only consonant pair.
            if offset in (0x01, 0x02):
                return "N"
            return _DEVANAGARI_CLASS.get(offset + _DEVANAGARI)
    return None


def consonant_skeleton(text: str) -> str:
    """Coarse consonant skeleton of ``text``, script-independent.

    >>> consonant_skeleton("Salman") == consonant_skeleton("सलमान")
    True
    >>> consonant_skeleton("Rajesh") == consonant_skeleton("രാജേഷ്")
    True

    Non-letters and vowels are dropped. Returns "" for text with no usable
    consonants, which callers must treat as "do not match on this".
    """
    if not text:
        return ""

    out: list[str] = []
    # Strip Latin accents so "José" behaves like "Jose".
    lowered = unicodedata.normalize("NFD", text.lower())
    i = 0
    while i < len(lowered):
        ch = lowered[i]
        if ch.isascii() and ch.isalpha():
            for digraph, cls in _LATIN_DIGRAPHS:
                if lowered.startswith(digraph, i):
                    out.append(cls)
                    i += len(digraph)
                    break
            else:
                # Soft "c" is a sibilant, not a velar: "cancel" is k-a-n-s-e-l.
                # Reading it as K left the skeleton unable to match the same
                # word written phonetically in any Indic script.
                if ch == "c" and lowered[i + 1:i + 2] in ("e", "i", "y"):
                    out.append("S")
                else:
                    cls = _LATIN_CLASS.get(ch)
                    if cls:
                        out.append(cls)
                i += 1
            continue
        cls = _indic_class(ch)
        if cls:
            out.append(cls)
        i += 1

    # Collapse runs ("Nazzima" -> NCM, matching "नज़ीमा"), since gemination is
    # written inconsistently across scripts and spellings.
    collapsed: list[str] = []
    for cls in out:
        if not collapsed or collapsed[-1] != cls:
            collapsed.append(cls)
    return "".join(collapsed)


#: A skeleton shorter than this is too generic to match as a substring — "NL"
#: ("Anil") occurs inside plenty of unrelated speech. Skeletons this short are
#: still matched, but only against a WHOLE word (see skeleton_contains).
MIN_SKELETON = 3


def word_skeletons(text: str) -> list[str]:
    """Skeleton of each whitespace/punctuation-separated word in ``text``."""
    return [s for s in (consonant_skeleton(w) for w in re.split(r"[\s,.;:!?()\[\]/\\-]+", text or "")) if s]


def _fold_sibilants(skeleton: str) -> str:
    """Merge the C class into S.

    Indian-language transliteration cannot agree on /z/: "Nazima" is written
    नज़ीमा in Devanagari (ज़ -> C) and നസീമ in Malayalam (സ -> S), because
    Malayalam has no /z/ at all. Folding the two classes together rescues that
    case. Applied only as a SECOND attempt, after the precise comparison fails,
    so it never costs precision where the scripts do agree.
    """
    return skeleton.replace("C", "S")


def _fold_loanword(skeleton: str) -> str:
    """Fold the distinctions an English loanword loses in Indic script.

    An English medical word spoken inside an Indian-language sentence comes back
    from STT transcribed phonetically, and two things happen to it:

      * a written glide appears where English only had adjacent vowels —
        "cardio" becomes कार्डियो, adding a Y;
      * the /g/ of "-logist" is written ज (the C class), because that is how the
        sound is heard: "kaardiyolojist".

    So "Cardiologist" (K R T L K S T) and "कार्डियोलॉजिस्ट" (K R T Y L C S T)
    describe the same word and share no common substring. Dropping Y and
    merging C into K makes both K R T L K S T.

    Lossy enough that it is used ONLY for specialization matching, never for
    names: matching the wrong speciality still yields a real doctor at this
    clinic whose name the caller then confirms, whereas matching the wrong NAME
    would book the wrong person.
    """
    return skeleton.replace("Y", "").replace("C", "K")


def skeleton_contains(haystack: str, needle: str, loose: bool = False) -> bool:
    """True when ``needle``'s skeleton appears inside ``haystack``'s.

    Both arguments are raw text in ANY script; the comparison happens on
    skeletons. A needle of two consonant classes ("Anil", "Ram") is matched
    only against a whole word, never as a substring.

    ``loose`` additionally applies the loanword fold described above. Callers
    pass it for specialization text only.
    """
    n = consonant_skeleton(needle)
    if len(n) < MIN_SKELETON:
        # Two consonant classes is not enough to identify anything. Measured
        # against the live roster: "khan" reduces to K-N, and so does the
        # ordinary Malayalam word "കാണണം" ("to see") — so "എനിക്ക് സൽമാൻ
        # ഡോക്ടറെ കാണണം" ("I want to see Dr Salman") matched the doctor
        # "Nazima khan". Booking the wrong doctor is the worst thing this
        # module can do, so short needles are refused outright; callers with a
        # same-script literal to try should try it separately.
        return False

    # Every comparison is scoped to a SINGLE spoken word — the needle must sit
    # inside one word of the haystack, not stretch across a word boundary.
    # Whole-utterance matching produced exactly the false positive that matters
    # most: "जनरल फिजिशियन से मिलना है" ("I want the general physician") spans
    # ...शियन से मिलना..., whose classes read N-S-M, and that matched the
    # sibilant-folded skeleton of the doctor name "Nazima". Booking the wrong
    # doctor is the worst outcome this module can produce, so the loose passes
    # must never be able to invent a match out of two adjacent words.
    words = word_skeletons(haystack)

    for word in words:
        if n == word or (len(n) >= MIN_SKELETON and n in word):
            return True

    folded = _fold_sibilants(n)
    for word in words:
        fw = _fold_sibilants(word)
        if folded == fw or (len(folded) >= MIN_SKELETON and folded in fw):
            return True

    if loose and _loanword_match(words, n):
        return True
    return False


#: Shared leading consonant classes required for a loanword match. Five is
#: specific enough that unrelated words do not reach it, while still letting the
#: ending vary.
_LOAN_MIN_PREFIX = 5

#: How much the two words may diverge at the END. English medical words differ
#: exactly there between how a clinic writes the speciality and how a caller
#: says it — "Cardiology" / "Cardiologist", "Paediatrician" / "Paediatrics" —
#: and Indic transcription adds its own ending ("...ജിസ്റ്റ്" giving R where
#: English has T).
_LOAN_MAX_SUFFIX_DRIFT = 2


def _collapse_runs(skeleton: str) -> str:
    out: list[str] = []
    for cls in skeleton:
        if not out or out[-1] != cls:
            out.append(cls)
    return "".join(out)


def _loanword_match(haystack_words: list[str], needle_skeleton: str) -> bool:
    """Loose word-level match for an English loanword across scripts.

    Compares the folded forms word by word and requires a long shared PREFIX
    rather than containment, because it is the ending that varies:

        Cardiologist    KRTLKST  }  shared prefix KRTLKS
        कार्डियोलॉजी      KRTLK    }  -> the same speciality

    Word-level (not whole-utterance) so the shared prefix has to belong to one
    spoken word, which is what keeps a five-class overlap from being accidental.
    """
    ln = _collapse_runs(_fold_loanword(needle_skeleton))
    if len(ln) < _LOAN_MIN_PREFIX:
        return False
    for word in haystack_words:
        lw = _collapse_runs(_fold_loanword(word))
        shared = 0
        for a, b in zip(ln, lw):
            if a != b:
                break
            shared += 1
        if shared >= _LOAN_MIN_PREFIX and shared >= min(len(ln), len(lw)) - _LOAN_MAX_SUFFIX_DRIFT:
            return True
    return False


def contains_any(text: str, phrases) -> bool:
    """True when any of ``phrases`` occurs in ``text``, matching across scripts.

    A literal test first (cheap, and exact for same-script text), then a
    skeleton test so a Latin phrase list matches Indic speech.

    A purely ASCII phrase is matched on WORD BOUNDARIES, not as a bare
    substring. The keyword sets that use this contain very short romanised
    words — "ha", "ok", "no" — and a bare substring test finds "ha" inside
    "what happened", which in the booking FSM reads as the caller confirming.
    Indic-script phrases keep the substring test, because Python's \\b means
    nothing against those scripts and their words are agglutinated anyway.
    """
    low = (text or "").lower()
    skeleton = consonant_skeleton(text)
    for phrase in phrases:
        p = (phrase or "").strip().lower()
        if not p:
            continue
        if p.isascii():
            if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", low):
                return True
        elif p in low:
            return True
        ps = consonant_skeleton(p)
        if len(ps) >= MIN_SKELETON and ps in skeleton:
            return True
    return False


# ── 2. Spoken numbers -> ASCII digits ────────────────────────────────────────

#: Clock numbers 1-12 plus 15/30/45 (for "half past"-style phrasing) in every
#: language this product serves. Only what a caller says about a TIME is here —
#: this is not a general number parser.
_NUMBER_WORDS: dict[str, int] = {}


def _add_numbers(mapping: dict[str, int]) -> None:
    _NUMBER_WORDS.update(mapping)


# Hindi / Marathi (Devanagari)
_add_numbers({
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12,
    "पंद्रह": 15, "तीस": 30,
    # Marathi differs on a few
    "दोन": 2, "पाच": 5, "सहा": 6, "नऊ": 9, "अकरा": 11, "बारा": 12,
})
# Bengali
_add_numbers({
    "এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "ছয়": 6, "সাত": 7,
    "আট": 8, "নয়": 9, "দশ": 10, "এগারো": 11, "বারো": 12,
})
# Gujarati
_add_numbers({
    "એક": 1, "બે": 2, "ત્રણ": 3, "ચાર": 4, "પાંચ": 5, "છ": 6, "સાત": 7,
    "આઠ": 8, "નવ": 9, "દસ": 10, "અગિયાર": 11, "બાર": 12,
})
# Punjabi (Gurmukhi)
_add_numbers({
    "ਇੱਕ": 1, "ਦੋ": 2, "ਤਿੰਨ": 3, "ਚਾਰ": 4, "ਪੰਜ": 5, "ਛੇ": 6, "ਸੱਤ": 7,
    "ਅੱਠ": 8, "ਨੌਂ": 9, "ਦਸ": 10, "ਗਿਆਰਾਂ": 11, "ਬਾਰਾਂ": 12,
})
# Odia
_add_numbers({
    "ଏକ": 1, "ଦୁଇ": 2, "ତିନି": 3, "ଚାରି": 4, "ପାଞ୍ଚ": 5, "ଛଅ": 6, "ସାତ": 7,
    "ଆଠ": 8, "ନଅ": 9, "ଦଶ": 10, "ଏଗାର": 11, "ବାର": 12,
})
# Tamil
_add_numbers({
    "ஒன்று": 1, "ஒரு": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
    "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10,
    "பதினொன்று": 11, "பன்னிரண்டு": 12,
})
# Telugu
_add_numbers({
    "ఒకటి": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5, "ఆరు": 6,
    "ఏడు": 7, "ఎనిమిది": 8, "తొమ్మిది": 9, "పది": 10, "పదకొండు": 11,
    "పన్నెండు": 12,
})
# Kannada
_add_numbers({
    "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3, "ನಾಲ್ಕು": 4, "ಐದು": 5, "ಆರು": 6,
    "ಏಳು": 7, "ಎಂಟು": 8, "ಒಂಬತ್ತು": 9, "ಹತ್ತು": 10, "ಹನ್ನೊಂದು": 11,
    "ಹನ್ನೆರಡು": 12,
})
# Malayalam
_add_numbers({
    "ഒന്ന്": 1, "ഒരു": 1, "രണ്ട്": 2, "മൂന്ന്": 3, "നാല്": 4, "അഞ്ച്": 5,
    "ആറ്": 6, "ഏഴ്": 7, "എട്ട്": 8, "ഒൻപത്": 9, "ഒമ്പത്": 9, "പത്ത്": 10,
    "പതിനൊന്ന്": 11, "പന്ത്രണ്ട്": 12,
})
# English number words — the STT returns these for an English call, and the
# clock regex only understands digits.
_add_numbers({
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirty": 30, "fifteen": 15, "forty five": 45, "forty-five": 45,
})

#: "o'clock" in each language — normalised to "baje", which the existing slot
#: regex in booking_processor already accepts as a time marker.
_OCLOCK_WORDS: tuple[str, ...] = (
    "बजे", "वाजता",                      # Hindi, Marathi
    "টার", "টা",                          # Bengali
    "વાગ્યે",                             # Gujarati
    "ਵਜੇ",                                # Punjabi
    "ଟା",                                 # Odia
    "மணி",                                # Tamil
    "గంటలకు", "గంట",                      # Telugu
    "ಗಂಟೆಗೆ", "ಗಂಟೆ",                     # Kannada
    "മണി",                                # Malayalam
    "o'clock", "oclock",                  # English
)

#: Digit shapes for every script above, mapped to ASCII. Sarvam sometimes
#: returns native digits ("११ बजे") rather than "11".
_NATIVE_DIGIT_BASES = (
    0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66,
)


def _ascii_digits(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        for base in _NATIVE_DIGIT_BASES:
            if base <= cp <= base + 9:
                out.append(str(cp - base))
                break
        else:
            out.append(ch)
    return "".join(out)


def normalise_spoken_numbers(text: str) -> str:
    """Rewrite spoken clock words into ASCII digits + a "baje" marker.

    Leaves everything else alone, so it is safe to run over any utterance
    before the existing time/day regexes:

        "ग्यारह बजे"            -> "11 baje"
        "പതിനൊന്ന് മണി"          -> "11 baje"
        "eleven o'clock"        -> "11 baje"
        "3:30 pm"               -> unchanged

    The returned string is only ever used for MATCHING — never shown to a
    caller and never stored.
    """
    if not text:
        return text

    out = _ascii_digits(text)

    # Longest first so "पदकొండు"-style multiword forms win over their prefixes.
    for word in sorted(_NUMBER_WORDS, key=len, reverse=True):
        if word in out.lower():
            # Case-insensitive replace, preserving the rest of the string.
            out = re.sub(re.escape(word), f" {_NUMBER_WORDS[word]} ", out,
                         flags=re.IGNORECASE)

    for marker in _OCLOCK_WORDS:
        if marker in out.lower():
            out = re.sub(re.escape(marker), " baje ", out, flags=re.IGNORECASE)

    return re.sub(r"\s+", " ", out).strip()
