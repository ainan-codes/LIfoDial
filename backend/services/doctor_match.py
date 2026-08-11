"""
backend/services/doctor_match.py

THE way this codebase decides which doctor a person meant.

There were two implementations, and they disagreed:

  * ``his.find_doctor_for_booking`` — used by the chat channel (via the
    ``[ACTION: BOOK|…|Doctor|…]`` tag) and by the voice channel's
    cancel/reschedule path. Lowercase ASCII substring + word overlap +
    specialization fallback.
  * ``BookingProcessor._try_match_doctor`` — used by the voice channel to arm a
    NEW booking. Its own, different lowercase ASCII loop.

Both were ASCII-only, which is the bug this module exists to close: STT returns
a Hindi call's words in Devanagari, so "सलमान" never matched a roster row named
"Salman" and no voice caller speaking an Indian language could ever be matched
to a doctor. Matching now runs on script-independent consonant skeletons
(services/indic_text.py).

``match_doctor`` takes plain dicts so it can serve the voice FSM (which holds
the roster as dicts from his.get_doctors) and the ORM path (which passes
Doctor rows through ``as_dict``) without either caring how the other stores a
doctor.
"""

from __future__ import annotations

import logging
import re

from backend.services.indic_text import (
    MIN_SKELETON,
    consonant_skeleton,
    skeleton_contains,
)

logger = logging.getLogger(__name__)

#: Words that carry no identifying information, so they must never be the thing
#: a match is made on. "doctor" is the important one: every caller says it.
_STOPWORDS: frozenset[str] = frozenset({
    "doctor", "dr", "drs", "doc", "the", "and", "for", "with", "sir", "madam",
    "specialist", "consultant", "senior", "junior", "prof", "professor",
})


def _significant_words(text: str) -> list[str]:
    """Identifying words of a name or specialization, stopwords removed."""
    return [
        w for w in re.split(r"[\s,.;:!?()\[\]/\\-]+", (text or "").lower())
        if w and w not in _STOPWORDS
    ]


def _matches_text(utterance: str, phrase: str, loose: bool = False) -> bool:
    """Does ``phrase`` (a doctor's name or specialization) occur in ``utterance``?

    Word by word, so "Sharma" finds "Dr. Anjali Sharma" and a caller naming one
    half of a two-part name still matches. Skeleton-based, so the script the
    utterance arrived in does not matter.

    ``loose`` is passed for specialization text only — see
    indic_text._fold_loanword for why, and why never for a name.
    """
    low = (utterance or "").lower()
    for word in _significant_words(phrase):
        # Literal, on word boundaries, first. This is what carries a name too
        # short for the skeleton to touch: "Ravi" and "Ram" reduce to two
        # consonant classes, which indic_text refuses as unidentifiable, but a
        # caller who types or says them in the SAME script as the roster is
        # unambiguous and must still match.
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", low):
            return True
        if skeleton_contains(utterance, word, loose=loose):
            return True
    return False


def match_doctor(utterance: str, doctors: list[dict]) -> tuple[dict | None, str]:
    """Resolve which doctor ``utterance`` refers to.

    Returns ``(doctor_or_None, how)`` where ``how`` is one of:

      ``"name"``                  matched a doctor's name; doctor is available
      ``"specialization"``        matched a speciality; doctor is available
      ``"name_unavailable"``      matched by name, but that doctor is ON LEAVE
      ``"specialization_unavailable"``  only on-leave doctors have that speciality
      ``""``                      no match

    The caller decides what to do with an on-leave match — the voice FSM must
    not arm a booking, but it also must not silently pretend the caller said
    nothing, because "Dr X is on leave" is the honest reply.

    Name matches beat speciality matches, and available doctors beat on-leave
    ones, so "I want the cardiologist" prefers a working cardiologist over one
    on leave with the same speciality.
    """
    if not utterance or not doctors:
        return None, ""

    by_name_unavailable: dict | None = None
    by_spec: dict | None = None
    by_spec_unavailable: dict | None = None

    for doc in doctors:
        available = doc.get("is_available", True)

        if _matches_text(utterance, doc.get("name") or ""):
            if available:
                return doc, "name"
            by_name_unavailable = by_name_unavailable or doc
            continue

        if _matches_text(utterance, doc.get("specialization") or "", loose=True):
            if available:
                by_spec = by_spec or doc
            else:
                by_spec_unavailable = by_spec_unavailable or doc

    if by_name_unavailable is not None:
        return by_name_unavailable, "name_unavailable"
    if by_spec is not None:
        return by_spec, "specialization"
    if by_spec_unavailable is not None:
        return by_spec_unavailable, "specialization_unavailable"
    return None, ""


def match_doctor_name(query: str, doctors: list[dict]) -> dict | None:
    """Resolve a doctor from a name the LLM wrote into an ``[ACTION:]`` tag.

    Stricter than ``match_doctor``: the input is a name field, not a whole
    utterance, so an exact-ish comparison is available and preferred. Falls
    back to the same skeleton matching, then to speciality, mirroring what
    his.find_doctor_for_booking always did — the difference is only that every
    step is now script-independent.
    """
    q = (query or "").strip()
    if not q:
        return None

    # 1. Plain substring either direction, same script ("sharma" ~ "Dr Sharma").
    low = q.lower()
    for doc in doctors:
        name = (doc.get("name") or "").lower()
        if name and (low in name or name in low):
            return doc

    # 2. Whole-name skeleton either direction — catches a fully transliterated
    #    name ("सलमान" ~ "Salman") before any per-word matching widens things.
    qs = consonant_skeleton(q)
    if len(qs) >= MIN_SKELETON:
        for doc in doctors:
            ns = consonant_skeleton(doc.get("name") or "")
            if ns and (qs in ns or ns in qs):
                return doc

    # 3. Significant-word overlap, then speciality — same order as before.
    doc, _how = match_doctor(q, doctors)
    return doc
