"""Smoke-test Deepgram pre-recorded STT (POST /v1/listen) end to end.

Exercises both bodies of backend.routers.agent_test.deepgram_transcribe:

  1. url    — the exact call from Deepgram's console example
  2. bytes  — raw audio, which is what the test-agent WebSocket path feeds in

Usage (needs DEEPGRAM_API_KEY in .env or the environment):

    python scripts/deepgram_smoke.py
"""
import asyncio
import os
import sys

# Never touch prod Supabase just to import the backend package.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./deepgram_smoke.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = "https://static.deepgram.com/examples/Bueller-Life-moves-pretty-fast.wav"


async def main() -> int:
    import httpx
    from backend.config import settings
    from backend.routers.agent_test import deepgram_transcribe

    key = settings.deepgram_api_key or os.getenv("DEEPGRAM_API_KEY", "")
    if not key:
        print("FAIL: DEEPGRAM_API_KEY not set")
        return 1
    print(f"key: {key[:6]}…{key[-4:]} ({len(key)} chars)")

    text, lang = await deepgram_transcribe(key, None, "nova-2", audio_url=SAMPLE)
    print(f"\n[url]   lang={lang!r}\n        {text!r}")
    ok_url = bool(text)

    async with httpx.AsyncClient(timeout=30.0) as c:
        audio = (await c.get(SAMPLE)).content
    print(f"\ndownloaded {len(audio)} bytes")

    text2, lang2 = await deepgram_transcribe(key, audio, "nova-2")
    print(f"\n[bytes] lang={lang2!r}\n        {text2!r}")
    ok_bytes = bool(text2)

    # Pinned language (what force_language=True triggers in transcribe_audio).
    text3, lang3 = await deepgram_transcribe(key, audio, "nova-2", language="en-IN")
    print(f"\n[en-IN] lang={lang3!r}\n        {text3!r}")
    ok_lang = bool(text3)

    print(f"\nurl={ok_url} bytes={ok_bytes} pinned-language={ok_lang}")
    return 0 if (ok_url and ok_bytes and ok_lang) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
