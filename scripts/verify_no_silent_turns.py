#!/usr/bin/env python
"""
scripts/verify_no_silent_turns.py

Verifies the never-ends-in-silence guarantee against REAL calls, from the
database alone — no audio, no recording, nothing a human has to transcribe.

Why this script exists
----------------------
The silence bug has been reported five times and each fix has been verified with
tests plus a green deploy. Neither proves anything about a call: the sandbox this
code is developed in cannot publish or subscribe WebRTC audio (UDP is blocked), so
"it works" has always meant "it works in a simulated pipeline". This is the piece
that was missing — a check that runs against production data and fails loudly.

What makes it possible: CallLoggerProcessor records an assistant turn from
TTSTextFrames (call_logger_processor.py:327), which the TTS service emits for
everything it actually synthesizes — including the constant backstop phrase, which
travels as a TTSSpeakFrame. So ``call_records.transcript`` is a faithful record of
who spoke when, and the invariant becomes a query:

    EVERY user turn must be followed by an assistant turn.

A violation is exactly the reported symptom: the caller said something, the agent
never answered, and the caller sat listening to an open line.

Three stronger checks ride along:

  * a call that BOOKED something and then ended on a user turn is the worst case
    in the whole family — the appointment exists and the caller was never told;
  * an assistant turn whose text still contains "[ACTION" means a machine tag was
    read aloud to a caller;
  * a user turn answered ONLY by the in-progress filler ("let me book that for
    you now, one moment") is a silent turn wearing a disguise. The filler exists
    to cover the write (agent/spoken_fallback.py), and it makes the plain
    every-user-turn-has-an-answer check pass without the caller having learned
    anything — so it is explicitly not counted as an answer here. Without this,
    adding the filler would have quietly weakened the invariant this script is
    the only real evidence for.

Usage
-----
    # the last 20 calls across all clinics
    python scripts/verify_no_silent_turns.py

    # one call, by id, with every turn printed
    python scripts/verify_no_silent_turns.py --call-id <uuid> --verbose

    # only calls since a deploy, for one clinic
    python scripts/verify_no_silent_turns.py --since 2026-08-14T12:00 --tenant <uuid>

Exit code is 1 if any call violates the invariant, so it can gate a deploy.

IMPORTANT: this reads whatever DATABASE_URL is configured. Run it with the
production environment loaded to check production calls; it only ever SELECTs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Same convention as the other scripts here: run from anywhere, import the backend
# package from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Read-only, but be explicit: nothing here may write.
os.environ.setdefault("ENVIRONMENT", "production")

# Transcripts are Hindi, Malayalam, Tamil… and a Windows console defaults to
# cp1252, which raises UnicodeEncodeError on the first Devanagari character —
# i.e. the tool for reading these calls would crash on exactly the calls worth
# reading. `errors="replace"` keeps a console that genuinely cannot render a
# script printing everything else rather than dying.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  (a stream that cannot be reconfigured)
        pass


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--call-id", help="check exactly one call record")
    p.add_argument("--tenant", help="restrict to one tenant id")
    p.add_argument("--limit", type=int, default=20, help="how many recent calls (default 20)")
    p.add_argument("--since", help="ISO timestamp; only calls started at or after it")
    p.add_argument("--hours", type=float,
                   help="shorthand for --since N hours ago (e.g. --hours 2)")
    p.add_argument("--verbose", action="store_true", help="print every turn")
    return p.parse_args()


# ── The invariant ─────────────────────────────────────────────────────────────

def _filler_sentences() -> set[str]:
    """Every "I'm doing it now" sentence, in every language.

    Sourced from the module that speaks them rather than restated, so a new
    language or a reworded phrase cannot silently start counting as an answer.
    Returns an empty set if the import fails — this script must still run and
    report the checks it CAN make.
    """
    try:
        from backend.agent import spoken_fallback
    except Exception:  # noqa: BLE001
        return set()
    keys = (spoken_fallback.WORKING_BOOK, spoken_fallback.WORKING_CANCEL,
            spoken_fallback.WORKING_RESCHEDULE)
    return {
        spoken_fallback.sentence(key, lang).strip()
        for lang in spoken_fallback.supported_languages()
        for key in keys
    }


def check_transcript(transcript: list) -> list[str]:
    """Every way a transcript can show a turn that ended in silence.

    Returns a list of human-readable failures; empty means this call is clean.
    Deliberately tolerant about shape — a transcript from an older build, or one
    truncated by a crash, must produce a finding rather than a traceback.
    """
    failures: list[str] = []
    turns = [t for t in (transcript or []) if isinstance(t, dict)]

    if not turns:
        # Not a silence failure by itself (a call can be answered and hung up on),
        # but it means this call cannot verify anything — say so rather than pass.
        return ["transcript is empty — this call proves nothing either way"]

    def role(t: dict) -> str:
        return str(t.get("role") or t.get("speaker") or "").lower()

    def text(t: dict) -> str:
        return str(t.get("text") or t.get("content") or "")

    fillers = _filler_sentences()

    for i, turn in enumerate(turns):
        if role(turn) != "user":
            continue
        following = [t for t in turns[i + 1:] if role(t) in ("user", "assistant")]
        if not following:
            failures.append(
                f"turn {turn.get('turn', i + 1)}: the CALLER spoke last and the agent "
                f"never answered — {text(turn)[:80]!r}"
            )
            continue
        if role(following[0]) == "user":
            failures.append(
                f"turn {turn.get('turn', i + 1)}: two caller turns in a row, so the "
                f"agent said nothing to the first — {text(turn)[:80]!r}"
            )
            continue
        # Answered — but by what? Everything the agent said before the caller's
        # next turn. If all of it is "one moment please", the caller was told to
        # wait and then never told the outcome.
        answers = []
        for t in following:
            if role(t) == "user":
                break
            answers.append(text(t).strip())
        if fillers and answers and all(a in fillers for a in answers if a):
            failures.append(
                f"turn {turn.get('turn', i + 1)}: the agent said only that it was "
                f"working on it and never reported the outcome — {answers[-1][:80]!r}"
            )

    for turn in turns:
        if role(turn) == "assistant" and "[ACTION" in text(turn).upper():
            failures.append(
                f"turn {turn.get('turn')}: a machine tag was SPOKEN to the caller — "
                f"{text(turn)[:80]!r}"
            )

    return failures


# ── The query ─────────────────────────────────────────────────────────────────

async def _load(args: argparse.Namespace):
    from sqlalchemy import select

    from backend.db import AsyncSessionLocal
    from backend.models.appointment import Appointment
    from backend.models.call_record import CallRecord

    since = None
    if args.hours:
        since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    elif args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    async with AsyncSessionLocal() as db:
        q = select(CallRecord).order_by(CallRecord.created_at.desc())
        if args.call_id:
            q = q.where(CallRecord.id == args.call_id)
        if args.tenant:
            q = q.where(CallRecord.tenant_id == args.tenant)
        if since is not None:
            q = q.where(CallRecord.started_at >= since)
        calls = list((await db.execute(q.limit(args.limit))).scalars().all())

        ids = [c.id for c in calls]
        booked: dict[str, list] = {}
        if ids:
            rows = (await db.execute(
                select(Appointment).where(Appointment.call_id.in_(ids))
            )).scalars().all()
            for row in rows:
                booked.setdefault(row.call_id, []).append(row)
    return calls, booked


async def main() -> int:
    args = _parse_args()
    calls, booked = await _load(args)

    if not calls:
        print("No call records matched. Nothing verified — this is NOT a pass.")
        return 1

    bad = 0
    for call in calls:
        failures = check_transcript(call.transcript)
        rows = booked.get(call.id) or []
        # The worst case in the family, called out on its own: the row exists and
        # the last thing that happened on the call was the caller speaking.
        if rows and any("spoke last" in f for f in failures):
            failures.append(
                f"WORST CASE: {len(rows)} appointment row(s) were written on this call and "
                "the caller was never told — the booking exists, they do not know it does"
            )

        turns = len([t for t in (call.transcript or []) if isinstance(t, dict)])
        status = "FAIL" if failures else "ok  "
        print(f"[{status}] {call.id}  started={call.started_at}  turns={turns}  "
              f"outcome={call.outcome or '-'}  rows={len(rows)}")
        for f in failures:
            print(f"         ! {f}")
        if args.verbose:
            for t in (call.transcript or []):
                if isinstance(t, dict):
                    who = str(t.get('role') or '?')[:9].ljust(9)
                    print(f"           {t.get('turn'):>3} {who} {str(t.get('text'))[:100]}")
        bad += bool(failures)

    print(f"\n{len(calls) - bad}/{len(calls)} calls clean.")
    if bad:
        print(
            f"{bad} call(s) show a turn that ended in silence.\n"
            "Next step: take one failing call id and grep the worker log for it, then for "
            "the booking trace_id on that call — see services/booking_trace.py. The stage "
            "that is MISSING is the finding."
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
