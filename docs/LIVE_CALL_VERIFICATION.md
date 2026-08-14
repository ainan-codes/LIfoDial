# Live-call verification — the silence bug

Hand this to whoever runs the live test. **Nothing below has been verified.** The
development sandbox cannot publish or subscribe WebRTC audio (UDP is blocked), so
every fix behind this checklist is proven only against a simulated pipeline. Five
previous rounds of this bug shipped with passing tests and a green deploy, and the
caller still heard silence — that is exactly what this checklist exists to catch.

## Before you start

1. **Confirm what is actually deployed.** A green deploy is not evidence.
   Verify by commit hash, and for the voice worker wait for a `track_published`
   named `pipecat-audio` in the logs — the agent "joining" proves nothing.
2. Note the deploy time. You will need it for the DB check at the end.
3. Have the worker logs open and greppable. Each booking attempt logs a
   `trace_id=…`; one grep reconstructs the whole attempt
   (`backend/services/booking_trace.py`).
4. **The one rule that makes this worth doing:** if at ANY point the agent goes
   quiet for more than ~15 seconds, that is a FAIL — write down the call id, what
   you had just said, and how long you waited. Do not help it along by repeating
   yourself before noting the time.

## The calls

### ☐ 1. Hindi CANCEL — the reported bug, end to end
*Validates: per-intent grammar (#5), the tag-only recovery, the backstop.*

1. Book an appointment first (any channel) for the number you will call from.
2. Call, and in **Hindi**, ask to cancel: "मेरी अपॉइंटमेंट रद्द कर दीजिए."
3. Give your name and number when asked. Do **not** volunteer a date or doctor.

**Expected:** the agent confirms the cancellation in Hindi within a few seconds.

**Specifically watch for:**
- It must **not** ask which doctor, which date or which time the appointment was
  for. That is the old padded-tag behaviour; a CANCEL is found by name + phone.
- No silence after your confirmation. This is where the reported hang happened.
- The appointment's status is `cancelled` in the dashboard afterwards.

**FAIL if:** any silence over ~15s; it asks for a date/doctor; it says the
appointment was cancelled but the row is still `confirmed`.

---

### ☐ 2. A second `[ACTION:]` tag in one turn — the scrubbed-frame case
*Validates: fix #1/Bug B — the caller must hear the backstop, not silence.*

This one cannot be triggered on demand: it depends on the model emitting two tags
in a single turn. Provoke it by giving everything at once and then immediately
changing your mind **in the same breath**, so one utterance contains two actions:

> "Book me with Dr <name> tomorrow at 2 PM, I'm <name>, <number> — actually no,
> make it 4 PM."

**Expected:** the agent replies with *something* — either a confirmation for one
of the times, or the flat fallback sentence ("आपकी अपॉइंटमेंट पक्की हो गई है" /
"Your appointment is confirmed", with no details).

**The fallback sentence is a PASS here**, not a failure: it is the backstop doing
its job. Note in your report if you hear it, and grab the call id — it means the
scrubbed-tag path was exercised for real, which is the thing we could not test.

**FAIL if:** silence. Also FAIL if you hear the words "ACTION" or "BOOK|" read
aloud — that is a machine tag reaching TTS.

Repeat up to 3 times if the first attempt produces an ordinary single-tag turn.

---

### ☐ 3. BOOK with no time — validation must still refuse
*Validates: the downstream gates were not loosened by the new grammar (#5).*

1. Call and say: "I'd like to see Dr <name> tomorrow." Give your name and number.
2. **Never say a time.** If asked for one, answer "whenever" or "any time".

**Expected:** the agent asks for a specific time and keeps asking. It must
**never** confirm a booking.

**FAIL if:** it confirms anything, or a row appears in the dashboard — especially
one at midnight (00:00), which is what an unvalidated empty time used to produce.

---

### ☐ 4. One call per installed LLM provider — no false watchdog trips
*Validates: the busy-timeout watchdog (#4) does not fire under normal conditions.*

The new watchdog force-releases the silence shield after **12 seconds** and speaks
a flat fallback sentence. Under healthy conditions it must never fire.

For **each** provider configured on the clinic (check the AI Platform dashboard —
typically Groq, and Gemini if configured), switch the agent to it and place one
ordinary booking call:

| Provider | Call outcome | Flat fallback heard? |
|---|---|---|
| Groq | ☐ | ☐ |
| Gemini | ☐ | ☐ |
| *(others configured)* | ☐ | ☐ |

**Expected:** a normal booking, confirmed in the agent's own words with the doctor
and time in the sentence.

**FAIL if** you hear the flat, detail-free fallback ("Your appointment is
confirmed. Thank you for calling." with no doctor and no time) — that means the
watchdog fired on a healthy call and 12s is too tight for that provider. Report
which provider and roughly how long the pause was; the fix is
`BUSY_TIMEOUT_SECONDS` in `backend/agent/processors/voice_action.py`.

Grep the worker log for `in progress` to confirm — a false trip logs
`the caller's request has been 'in progress' for 12s with no reply starting`.

---

## After the calls — the DB check

Run this against production. It checks every call you just placed for the
invariant the bug violates: **every caller turn must be followed by an agent
turn.** No audio needed.

```bash
python scripts/verify_no_silent_turns.py --hours 2
```

- Exit code **0** and `N/N calls clean` — the invariant held on real calls.
- Exit code **1** — read the flagged turns. `WORST CASE` means an appointment row
  was written and the caller was never told.
- `--call-id <id> --verbose` prints every turn of one call.
- "No call records matched" is **not** a pass — it means nothing was verified.

## Reporting back

For each call: the call id, which checklist item, pass/fail, and for any failure
the transcript around it plus the `trace_id` from the log. A failure with a
`trace_id` is diagnosable without a recording; one without is not.
