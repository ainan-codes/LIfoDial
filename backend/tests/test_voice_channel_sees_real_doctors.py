"""The voice channel must see the same real doctors the chat channel sees.

Regression test for the divergence observed live on 2026-08-11 at Indiana
Hospital Mangalore. Same clinic, same Hindi receptionist, same question:

  chat  — "we have Dr. Salman available for cardiology"        (correct)
  voice — "हमारे हॉस्पिटल में अभी कोई डॉक्टर उपलब्ध नहीं हैं जिनकी
           जानकारी मुझे दी गई है"  ("no doctors available, no
           information given to me")                           (false — the
           clinic had three doctors, one of them a cardiologist)

The cause was NOT two doctor-lookup implementations disagreeing. It was that
the agent-worker process could not execute a single ORM query:

  * 2026-08-10's availability-engine commit gave Doctor a relationship
    ``availability_windows -> "DoctorAvailability"``;
  * SQLAlchemy resolves that target BY CLASS NAME when mappers are first
    configured;
  * the API process imports every model (init_db -> _import_all_models), but
    the worker imported five model modules by name and doctor_availability was
    not among them, and nothing else in the worker imported it;
  * so the first query raised, SQLAlchemy CACHED the failed configuration, and
    every subsequent query in the process raised too — permanently, for the
    life of the worker.

The worker then ran on room metadata alone: no roster, no clinic hours, no
knowledge base, no bookings, no transcript persistence.

The four guards below, in the order the bug happened:

  1. the worker's own import set must leave the ORM usable;
  2. prewarm must register EVERY model, not a hand-picked list;
  3. a failed clinic-data load must never be spoken as "this clinic has no
     doctors";
  4. voice and chat must call the same roster/availability builder.

Run: python -m pytest backend/tests/test_voice_channel_sees_real_doctors.py -v
"""
import os
import subprocess
import sys
import textwrap

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 1. The root cause, reproduced exactly ─────────────────────────────────────

def _run_in_fresh_process(body: str) -> subprocess.CompletedProcess:
    """Run `body` in a NEW interpreter.

    A fresh process is the whole point: this bug only exists in a process whose
    import graph is the worker's. Inside pytest, conftest and sibling tests have
    already imported the API's models, so the registry is complete and the bug
    is invisible — which is precisely why it reached production.
    """
    script = textwrap.dedent(body)
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "PYTHONPATH": REPO_ROOT,
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=300,
        # pipecat's start-up banner is not cp1252-decodable on Windows, and a
        # decode error in the reader thread would mask the actual assertion.
        encoding="utf-8", errors="replace",
    )


def test_worker_import_set_leaves_every_mapper_configurable():
    """Importing ONLY what the agent worker imports must leave the ORM usable.

    This is the guard that generalises: it does not mention DoctorAvailability,
    so it fails for the NEXT model that gains a relationship without being
    registered, which is the actual class of bug.
    """
    result = _run_in_fresh_process(
        """
        # Exactly what backend/agent/__main__.py imports from the app.
        from backend.agent.pipeline import entrypoint, prewarm  # noqa: F401
        from sqlalchemy.orm import configure_mappers
        configure_mappers()
        print("MAPPERS_OK")
        """
    )
    assert "MAPPERS_OK" in result.stdout, (
        "The agent worker's import set cannot configure its ORM mappers, so every "
        "database read and write in the worker would fail:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_prewarm_registers_every_model_not_a_hand_picked_list():
    """After prewarm(), every table the app defines must be registered.

    prewarm used to import five model modules by name. Any model outside that
    list was missing from the worker's registry — the bug. Comparing against
    the API's own loader keeps the two processes honest about each other.
    """
    result = _run_in_fresh_process(
        """
        from backend.agent.pipeline import prewarm
        prewarm(None)

        import backend.db as db
        after_prewarm = set(db.Base.metadata.tables)

        db._import_all_models()
        canonical = set(db.Base.metadata.tables)

        missing = canonical - after_prewarm
        print("MISSING=" + ",".join(sorted(missing)))
        """
    )
    assert "MISSING=" in result.stdout, f"{result.stdout}\n{result.stderr}"
    missing = result.stdout.split("MISSING=")[1].split("\n")[0].strip()
    assert missing == "", (
        f"prewarm() left these models unregistered in the worker: {missing}. "
        "Every ORM query in the worker fails if any relationship target is "
        "among them."
    )


def test_preflight_refuses_to_boot_on_an_incomplete_registry():
    """A worker with an unusable ORM must die, not answer calls without a database.

    A silently data-less worker told patients their clinic had no doctors for a
    day. A worker that exits is visible immediately.
    """
    result = _run_in_fresh_process(
        """
        import sys
        import backend.db as db
        from backend.agent import pipeline

        # Simulate the failure mode: a model whose relationship target is not
        # registered anywhere in this process.
        def _broken():
            raise RuntimeError("expression 'SomeNewModel' failed to locate a name")
        db._import_all_models = _broken

        try:
            pipeline._verify_orm_registry_or_die()
        except SystemExit as exc:
            print("DIED_WITH=" + str(exc.code))
            sys.exit(0)
        print("SURVIVED")
        """
    )
    assert "DIED_WITH=1" in result.stdout, (
        "The worker booted with a broken ORM instead of refusing to start:\n"
        f"{result.stdout}\n{result.stderr}"
    )


# ── 3. A failed read must not become a spoken falsehood ───────────────────────

from backend.agent.pipeline import _build_system_prompt, _clinic_facts_block

_LOADED_TENANT = {
    "id": "t1",
    "clinic_name": "Indiana Hospital Mangalore",
    "working_hours": "9:00 AM - 7:00 PM, Mon-Sat",
    "doctors": [
        {"id": "d1", "name": "Salman", "specialization": "Cardiologist", "is_available": True},
    ],
    "knowledge_base": [],
}

_FAILED_TENANT = {
    "id": "t1",
    "clinic_name": "Indiana Hospital Mangalore",
    "working_hours": "9 AM – 7 PM, Mon–Sat",   # the hardcoded default, not real
    "doctors": [],                              # empty because the READ failed
    "knowledge_base": [],
    "_facts_unavailable": True,
}


def test_failed_load_never_claims_the_clinic_has_no_doctors():
    block = _clinic_facts_block(_FAILED_TENANT)
    lowered = block.lower()
    assert "no doctors have been added" not in lowered, (
        "A failed database read was rendered as the factual claim that the clinic "
        "has no doctors — the exact false statement this bug produced live."
    )
    assert "could not be read" in lowered or "cannot look up" in lowered
    # And it must not volunteer the fabricated default hours either.
    assert "9 AM" not in block


def test_failed_load_still_tells_the_caller_something_useful():
    block = _clinic_facts_block(_FAILED_TENANT).lower()
    assert "call" in block and ("back" in block or "staff" in block), (
        "The caller must be offered a callback, not left with a bare refusal."
    )


def test_genuinely_empty_roster_still_says_so_plainly():
    """The honest 'no doctors yet' message must survive — a new clinic really
    does have an empty roster, and saying so is what stops the model inventing
    a doctor."""
    empty = {**_LOADED_TENANT, "doctors": []}
    block = _clinic_facts_block(empty).lower()
    assert "no doctors have been added" in block


def test_loaded_roster_reaches_a_custom_prompt():
    """The live agent has a CUSTOM system prompt (precedence #1). The roster has
    to reach that too, or only template-based clinics ever see it."""
    prompt = _build_system_prompt(
        {"system_prompt": "आप एक हिंदी रिसेप्शनिस्ट हैं।", "language": "hi-IN"},
        _LOADED_TENANT,
    )
    assert "Salman" in prompt
    assert "Cardiologist" in prompt


def test_failed_load_taints_a_template_prompt_too():
    prompt = _build_system_prompt(
        {"template": "clinic_receptionist", "language": "en-IN"}, _FAILED_TENANT,
    )
    assert "could not be loaded" in prompt.lower()
    assert "none yet" not in prompt.lower()


# ── 4. One shared roster/availability implementation ──────────────────────────

def test_chat_and_voice_use_the_same_availability_builder():
    """Both channels must resolve to the SAME function object.

    Not a style check: two copies of this logic is what let the channels drift
    into answering the same question differently. If a future change gives
    either channel its own copy, this fails.
    """
    from backend.routers import agent_test as chat
    from backend.services import availability_prompt

    assert chat._real_availability_block is availability_prompt.real_availability_block

    # The voice pipeline reaches it through a local import inside entrypoint(),
    # so assert on the source instead.
    pipeline_src = open(
        os.path.join(REPO_ROOT, "backend", "agent", "pipeline.py"), encoding="utf-8"
    ).read()
    assert "from backend.services.availability_prompt import real_availability_block" in pipeline_src
    assert "_build_system_prompt(agent_config, tenant, availability_block)" in pipeline_src


def test_voice_roster_comes_from_the_shared_doctor_accessor():
    """The voice pipeline must read doctors via his.get_doctors, like chat does,
    not with its own select(Doctor)."""
    pipeline_src = open(
        os.path.join(REPO_ROOT, "backend", "agent", "pipeline.py"), encoding="utf-8"
    ).read()
    assert "from backend.services.his import get_doctors" in pipeline_src
    assert 'tenant["doctors"] = await get_doctors(tenant_id)' in pipeline_src
    assert "select(Doctor)" not in pipeline_src, (
        "The voice pipeline grew its own doctor query again."
    )


@pytest.mark.asyncio
async def test_availability_block_never_returns_empty_on_failure():
    """A lookup failure must produce the 'cannot look it up' block, never "" —
    an empty string leaves the model with no instruction and it invents a
    roster, and never the claim that the clinic has no doctors."""
    from backend.services import availability_prompt

    block = await availability_prompt.real_availability_block("no-such-tenant-id")
    assert block.strip(), "returned an empty block"
    lowered = block.lower()
    assert "never invent" in lowered or "do not name or invent" in lowered
