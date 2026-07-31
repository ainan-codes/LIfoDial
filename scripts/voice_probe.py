#!/usr/bin/env python
"""
scripts/voice_probe.py — a headless synthetic caller for the LiveKit/Pipecat path.

Why this exists
---------------
Every voice regression in this repo so far has been a SILENT one: the deploy is
green, the worker logs "registered", and the caller hears nothing / stutter /
a four-second gap. Code review has repeatedly signed off on fixes that did not
hold under real audio. The only thing that settles it is a real call — but a
real call has historically meant a human with a headset, which is not
repeatable, not measurable in milliseconds, and not runnable before a deploy.

This script IS a real call. It creates a real LiveKit room, dispatches the real
Pipecat worker, joins as a real participant publishing real speech audio, and
records the agent's real audio track. Nothing is mocked. The difference from a
human tester is that it timestamps everything and writes a machine-readable
report, so "is it faster?" and "does it stutter?" become numbers instead of
opinions.

What it measures (all wall-clock, all from the CALLER's side of the wire)
------------------------------------------------------------------------
  dispatch_to_agent_joined_ms   room created -> agent participant present
  agent_joined_to_first_audio_ms  agent present -> first audible frame (greeting)
  turn_latency_ms               caller's last speech sample on the wire ->
                                first audible agent frame. THIS is the number
                                the product is judged on.
  stutter                       silent gaps INSIDE an agent utterance, which is
                                what "choppy" actually is. Reported as a gap
                                histogram, not a yes/no.
  interruption_stop_ms          caller starts talking over the agent -> agent
                                audio stops. Barge-in responsiveness.

Design notes that matter
------------------------
* The caller's track behaves like a real microphone: a capture loop runs for the
  whole call at a fixed frame cadence, emitting silence when there is nothing to
  say. Publishing speech and then going dead-air would starve the agent's VAD of
  the trailing silence it needs to endpoint on, and every turn-latency number
  would be wrong (or infinite).
* Speech-end is taken from `AudioSource.wait_for_playout()`, not from the last
  `capture_frame()` return. capture_frame only queues; playout is when the audio
  is actually gone. The difference is up to a full buffer (~1s) and would
  silently flatter every measurement.
* "Agent is speaking" is decided by RMS energy, not by frame arrival. WebRTC's
  receiver-side jitter buffer hands us a steady frame cadence regardless of what
  the sender did, filling underruns with silence/PLC — so arrival timing cannot
  see a stutter, but energy can.
* Caller utterances are synthesized once and cached on disk. Before/after
  comparisons must use byte-identical input audio or the STT stage alone will
  move the numbers.
* Room config is passed via room METADATA, so a probe call needs no DB row and
  can exercise an arbitrary provider combination on demand
  (`_load_tenant_and_config` in backend/agent/pipeline.py falls back to metadata
  defaults). This is what makes per-provider latency tables possible.

Usage
-----
  # simplest: one greeting + two turns against whatever the metadata defaults to
  python scripts/voice_probe.py --label baseline

  # pin a specific provider combination
  python scripts/voice_probe.py --stt deepgram --tts sarvam --llm groq \
      --llm-model llama-3.3-70b-versatile --label deepgram-sarvam-groq

  # barge-in test
  python scripts/voice_probe.py --interrupt --label bargein

  # against a real agent row instead of ad-hoc metadata
  python scripts/voice_probe.py --agent-id <uuid> --tenant-id <uuid>

Reports are written to scripts/voice_probe_runs/<label>-<timestamp>/ as
report.json + agent.wav + caller.wav, so a run can be listened to as well as
read.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import statistics
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ── Audio constants ───────────────────────────────────────────────────────────
# 24 kHz mono is a middle ground: above every STT's internal working rate (so we
# never make the agent's job easier than a real call would), below 48 kHz (so the
# capture loop is cheap on a laptop). The frame is 20 ms because that is WebRTC's
# native packetisation — using 10 ms doubles the number of awaits in the capture
# loop for no extra fidelity.
SAMPLE_RATE = 24_000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000

#: RMS (int16 scale) above which a received frame counts as "the agent is
#: speaking". Real TTS output sits in the thousands; WebRTC comfort noise and
#: PLC filler sit in the low tens. 250 is comfortably between the two and was
#: chosen so that a normal inter-word pause does NOT cross it (see
#: _SPEECH_GAP_MS below for how pauses are separated from stutter).
SPEAKING_RMS = 250.0

#: A low-energy run shorter than this inside an utterance is ordinary prosody
#: (between words, before a comma). Longer than this and the caller perceives a
#: break. 180 ms is deliberately generous — the point is to avoid crying stutter
#: over natural speech rhythm, so a gap that clears this bar is a real one.
_STUTTER_GAP_MS = 180

#: Silence this long ends an agent utterance (i.e. "the agent has finished, it
#: is my turn"). Must be comfortably above _STUTTER_GAP_MS or a stuttering agent
#: would be mistaken for a finished one and the probe would talk over itself.
_UTTERANCE_END_SILENCE_MS = 900


# ── Report structures ─────────────────────────────────────────────────────────
@dataclass
class Utterance:
    """One continuous stretch of agent speech, in seconds relative to call start."""
    index: int
    start_s: float
    end_s: float
    duration_s: float
    gaps_ms: list[float] = field(default_factory=list)

    @property
    def worst_gap_ms(self) -> float:
        return max(self.gaps_ms) if self.gaps_ms else 0.0


@dataclass
class Turn:
    index: int
    text: str
    caller_speech_start_s: float
    caller_speech_end_s: float
    agent_first_audio_s: float | None
    turn_latency_ms: float | None
    agent_utterance_index: int | None


@dataclass
class Report:
    label: str
    started_at: str
    room: str
    config: dict[str, Any]
    dispatch_to_agent_joined_ms: float | None = None
    agent_joined_to_first_audio_ms: float | None = None
    dispatch_to_first_audio_ms: float | None = None
    turns: list[dict] = field(default_factory=list)
    utterances: list[dict] = field(default_factory=list)
    interruption_stop_ms: float | None = None
    agent_audio_seconds: float = 0.0
    frames_received: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    transcript_of_agent_audio: str | None = None

    def summary(self) -> dict:
        lat = [t["turn_latency_ms"] for t in self.turns if t.get("turn_latency_ms")]
        gaps = [g for u in self.utterances for g in u["gaps_ms"]]
        return {
            "label": self.label,
            "dispatch_to_agent_joined_ms": _r(self.dispatch_to_agent_joined_ms),
            "dispatch_to_first_audio_ms": _r(self.dispatch_to_first_audio_ms),
            "turn_latency_ms_min": _r(min(lat)) if lat else None,
            "turn_latency_ms_median": _r(statistics.median(lat)) if lat else None,
            "turn_latency_ms_max": _r(max(lat)) if lat else None,
            "turns_measured": len(lat),
            "stutter_gaps_over_180ms": len(gaps),
            "worst_stutter_gap_ms": _r(max(gaps)) if gaps else 0.0,
            "interruption_stop_ms": _r(self.interruption_stop_ms),
            "agent_audio_seconds": _r(self.agent_audio_seconds, 2),
            "errors": self.errors,
        }


def _r(v, nd=1):
    return None if v is None else round(float(v), nd)


# ── Caller speech synthesis (cached) ──────────────────────────────────────────
class CallerVoice:
    """Synthesizes the caller's utterances and caches them on disk.

    Cached because a before/after latency comparison is only honest if the STT
    stage receives byte-identical audio in both runs; re-synthesizing would let
    TTS nondeterminism leak into the numbers we attribute to our own changes.
    """

    def __init__(self, cache_dir: Path, provider: str | None = None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider or self._auto_provider()

    @staticmethod
    def _auto_provider() -> str:
        # ElevenLabs first: using a DIFFERENT vendor for the caller than the
        # agent's own TTS keeps the test honest — otherwise a Sarvam-vs-Sarvam
        # run could flatter Sarvam's STT on its own synthesis artifacts.
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        if os.environ.get("SARVAM_API_KEY"):
            return "sarvam"
        raise RuntimeError(
            "No TTS key available to synthesize the caller's voice. Set "
            "ELEVENLABS_API_KEY or SARVAM_API_KEY (scripts/voice_probe.py reads .env)."
        )

    def _cache_path(self, text: str) -> Path:
        import hashlib
        h = hashlib.sha256(f"{self.provider}:{text}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{self.provider}-{h}.wav"

    async def utterance(self, text: str) -> np.ndarray:
        """int16 mono @ SAMPLE_RATE for `text`, with trailing silence trimmed.

        Trailing silence is trimmed because the probe adds its own controlled
        silence afterwards — leaving the TTS vendor's arbitrary tail in would
        make 'caller stopped speaking' mean something different per utterance.
        """
        path = self._cache_path(text)
        if not path.exists():
            raw = await self._synthesize(text)
            path.write_bytes(raw)
        return _load_wav_as_int16(path, SAMPLE_RATE)

    async def _synthesize(self, text: str) -> bytes:
        import httpx
        if self.provider == "elevenlabs":
            key = os.environ["ELEVENLABS_API_KEY"]
            voice = os.environ.get("PROBE_ELEVENLABS_VOICE", "21m00Tcm4TlvDq8ikWAM")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    url,
                    headers={"xi-api-key": key, "accept": "audio/wav"},
                    params={"output_format": "pcm_24000"},
                    json={"text": text, "model_id": "eleven_turbo_v2_5"},
                )
                r.raise_for_status()
                # pcm_24000 returns headerless PCM; wrap it so the cache file is
                # a real, listenable wav.
                return _pcm_to_wav_bytes(r.content, 24_000)
        if self.provider == "sarvam":
            key = os.environ["SARVAM_API_KEY"]
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={"api-subscription-key": key},
                    json={
                        "inputs": [text],
                        "target_language_code": os.environ.get("PROBE_SARVAM_LANG", "en-IN"),
                        "speaker": os.environ.get("PROBE_SARVAM_SPEAKER", "anushka"),
                        "model": "bulbul:v2",
                        "speech_sample_rate": 22050,
                    },
                )
                r.raise_for_status()
                import base64
                return base64.b64decode(r.json()["audios"][0])
        raise RuntimeError(f"unknown caller-voice provider {self.provider!r}")


def _pcm_to_wav_bytes(pcm: bytes, rate: int) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _load_wav_as_int16(path: Path, target_rate: int) -> np.ndarray:
    import soundfile as sf
    data, rate = sf.read(str(path), dtype="int16", always_2d=True)
    mono = data[:, 0]
    if rate != target_rate:
        from scipy.signal import resample_poly
        g = math.gcd(int(rate), int(target_rate))
        mono = resample_poly(mono.astype(np.float32), target_rate // g, rate // g)
        mono = np.clip(mono, -32768, 32767).astype(np.int16)
    return _trim_trailing_silence(mono)


def _trim_trailing_silence(x: np.ndarray, thresh: int = 200) -> np.ndarray:
    idx = np.nonzero(np.abs(x) > thresh)[0]
    if idx.size == 0:
        return x
    # keep 60ms of natural decay so the cut is not audible as a click
    end = min(len(x), idx[-1] + SAMPLE_RATE * 60 // 1000)
    return x[: int(end)]


# ── The probe ─────────────────────────────────────────────────────────────────
class VoiceProbe:
    def __init__(self, args):
        self.args = args
        self.t0 = 0.0                      # monotonic zero == room created
        self.report: Report | None = None

        self._speech_queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
        self._playout_done = asyncio.Event()
        self._playout_done.set()

        # Received-audio timeline: (t_rel_seconds, rms) per 20ms frame.
        self._energy: list[tuple[float, float]] = []
        self._agent_pcm: list[np.ndarray] = []
        self._first_audio_s: float | None = None
        self._agent_joined_s: float | None = None
        self._audio_lock = asyncio.Lock()

    # -- timing helper ---------------------------------------------------------
    def now(self) -> float:
        return time.perf_counter() - self.t0

    # -- room lifecycle --------------------------------------------------------
    def _metadata(self) -> str:
        a = self.args
        md = {
            "clinic_name": a.clinic_name,
            "first_message": a.first_message,
            "call_type": "test",
            "test_mode": True,
            # Keep probe calls short and self-terminating so a crashed run can
            # never leave a room burning provider credits.
            "silence_timeout_seconds": a.silence_timeout,
            "max_duration_seconds": a.max_duration,
        }
        if a.agent_id:
            md["agent_id"] = a.agent_id
        if a.tenant_id:
            md["tenant_id"] = a.tenant_id
        if a.system_prompt:
            md["system_prompt"] = a.system_prompt
        for k, v in (
            ("stt_provider", a.stt), ("stt_model", a.stt_model), ("stt_language", a.stt_language),
            ("tts_provider", a.tts), ("tts_model", a.tts_model), ("tts_language", a.tts_language),
            ("tts_voice", a.tts_voice),
            ("llm_provider", a.llm), ("llm_model", a.llm_model),
        ):
            if v:
                md[k] = v
        return json.dumps(md)

    async def run(self) -> Report:
        from livekit import api as lkapi
        from livekit import rtc

        url = os.environ["LIVEKIT_URL"]
        key = os.environ["LIVEKIT_API_KEY"]
        secret = os.environ["LIVEKIT_API_SECRET"]

        from backend.agent.agent_name import AGENT_NAME

        room_name = f"probe-{self.args.label[:12]}-{uuid.uuid4().hex[:8]}"
        metadata = self._metadata()

        self.report = Report(
            label=self.args.label,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            room=room_name,
            config=json.loads(metadata),
        )

        if self.args.wake_worker:
            await self._wake_worker()

        # ── create room + dispatch, exactly as backend/routers/web_calls.py does
        self.t0 = time.perf_counter()
        async with lkapi.LiveKitAPI(url, key, secret) as lk:
            await lk.room.create_room(
                lkapi.CreateRoomRequest(
                    name=room_name,
                    metadata=metadata,
                    empty_timeout=15,
                    max_participants=3,
                    agents=[lkapi.RoomAgentDispatch(agent_name=AGENT_NAME)],
                )
            )

        token = lkapi.AccessToken(key, secret)
        token.with_identity(f"user-probe-{uuid.uuid4().hex[:6]}")
        token.with_name("Voice Probe")
        token.with_grants(
            lkapi.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True)
        )
        token.with_room_config(
            lkapi.RoomConfiguration(agents=[lkapi.RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
        from datetime import timedelta
        token.with_ttl(timedelta(seconds=600))

        room = rtc.Room()
        agent_joined = asyncio.Event()
        recorders: list[asyncio.Task] = []

        @room.on("participant_connected")
        def _on_join(p):
            if not (p.identity or "").startswith("user-"):
                if self._agent_joined_s is None:
                    self._agent_joined_s = self.now()
                    agent_joined.set()

        @room.on("track_subscribed")
        def _on_track(track, pub, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO and not (participant.identity or "").startswith("user-"):
                recorders.append(asyncio.create_task(self._record(rtc.AudioStream(track))))

        @room.on("disconnected")
        def _on_disc(reason):
            self.report.notes.append(f"room disconnected at {self.now():.2f}s: {reason}")

        await room.connect(url, token.to_jwt())

        # Publish a microphone-like track for the whole call.
        source = rtc.AudioSource(SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("caller", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        capture = asyncio.create_task(self._capture_loop(source))

        try:
            # Some participants may already be in the room before our handler
            # was attached; check once rather than waiting on an event that has
            # already fired.
            for p in list(room.remote_participants.values()):
                if not (p.identity or "").startswith("user-"):
                    self._agent_joined_s = self._agent_joined_s or self.now()
                    agent_joined.set()

            try:
                await asyncio.wait_for(agent_joined.wait(), timeout=self.args.join_timeout)
            except asyncio.TimeoutError:
                self.report.errors.append(
                    f"NO AGENT JOINED within {self.args.join_timeout}s — this is the "
                    f"dead-air failure mode. Room {room_name}."
                )
                return self._finalise()

            await self._converse(source)
        finally:
            capture.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await capture
            for t in recorders:
                t.cancel()
            with contextlib.suppress(Exception):
                await room.disconnect()
            if self.args.delete_room:
                with contextlib.suppress(Exception):
                    async with lkapi.LiveKitAPI(url, key, secret) as lk:
                        await lk.room.delete_room(lkapi.DeleteRoomRequest(room=room_name))

        return self._finalise()

    async def _wake_worker(self) -> None:
        """HTTP-ping the worker so a free-tier cold start isn't billed to the
        first measurement. A raw LiveKit dispatch bypasses the backend's
        ensure_worker_awake(), and a dispatch to a sleeping worker is silently
        lost (see scripts/… and the DISPATCH-ALARM path in web_calls.py)."""
        import httpx
        base = (os.environ.get("AGENT_WORKER_URL") or self.args.worker_url or "").rstrip("/")
        if not base:
            self.report.notes.append("no AGENT_WORKER_URL — skipped wake (fine for local runs)")
            return
        t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.get(base + "/")
            took = time.perf_counter() - t
            self.report.notes.append(f"worker wake: HTTP {r.status_code} in {took:.1f}s")
            if took > 5:
                # It was asleep. Registration with LiveKit lags the HTTP 200.
                await asyncio.sleep(self.args.post_wake_settle)
                self.report.notes.append(
                    f"cold start detected — waited {self.args.post_wake_settle}s for LiveKit registration"
                )
        except Exception as e:
            self.report.notes.append(f"worker wake failed: {e}")

    # -- audio in --------------------------------------------------------------
    async def _record(self, stream) -> None:
        async for ev in stream:
            f = ev.frame
            pcm = np.frombuffer(f.data, dtype=np.int16)
            if f.num_channels > 1:
                pcm = pcm[:: f.num_channels]
            rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float64))))) if pcm.size else 0.0
            t = self.now()
            async with self._audio_lock:
                self._energy.append((t, rms))
                self._agent_pcm.append(pcm.copy())
                self.report.frames_received += 1
                if rms >= SPEAKING_RMS and self._first_audio_s is None:
                    self._first_audio_s = t

    # -- audio out -------------------------------------------------------------
    async def _capture_loop(self, source) -> None:
        """Behave like a real microphone for the life of the call.

        Emits silence continuously and splices in queued speech. The continuous
        silence is not padding — the agent's VAD endpoints on it, so without it
        every turn would either never complete or complete at an arbitrary time.
        """
        silence = np.zeros(SAMPLES_PER_FRAME, dtype=np.int16)
        from livekit import rtc

        pending = np.zeros(0, dtype=np.int16)
        while True:
            if pending.size == 0 and not self._speech_queue.empty():
                pending = self._speech_queue.get_nowait()
            if pending.size >= SAMPLES_PER_FRAME:
                chunk, pending = pending[:SAMPLES_PER_FRAME], pending[SAMPLES_PER_FRAME:]
            elif pending.size > 0:
                chunk = np.concatenate([pending, silence[: SAMPLES_PER_FRAME - pending.size]])
                pending = np.zeros(0, dtype=np.int16)
                self._playout_done.set()
            else:
                chunk = silence
            await source.capture_frame(
                rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=SAMPLES_PER_FRAME,
                )
            )

    async def _say(self, source, pcm: np.ndarray) -> tuple[float, float]:
        """Speak `pcm`; return (start_s, end_s) where end_s is when the last
        sample was actually on the wire, not merely queued."""
        start = self.now()
        self._playout_done.clear()
        await self._speech_queue.put(pcm)
        await self._playout_done.wait()
        # capture_frame only enqueues into the source's ~1s buffer; the audio is
        # not gone until playout drains. Measuring speech-end before this would
        # under-report every turn latency by up to a second.
        with contextlib.suppress(Exception):
            await source.wait_for_playout()
        return start, self.now()

    # -- conversation ----------------------------------------------------------
    async def _wait_for_agent_silence(self, since: float, timeout: float) -> bool:
        """Wait until the agent has been quiet for _UTTERANCE_END_SILENCE_MS."""
        deadline = self.now() + timeout
        last_loud = since
        spoke = False
        while self.now() < deadline:
            async with self._audio_lock:
                recent = [(t, r) for t, r in self._energy if t >= since]
            for t, r in recent:
                if r >= SPEAKING_RMS:
                    last_loud = max(last_loud, t)
                    spoke = True
            if spoke and (self.now() - last_loud) * 1000 >= _UTTERANCE_END_SILENCE_MS:
                return True
            await asyncio.sleep(0.05)
        return spoke

    async def _first_audio_after(self, since: float, timeout: float) -> float | None:
        deadline = self.now() + timeout
        while self.now() < deadline:
            async with self._audio_lock:
                for t, r in self._energy:
                    if t > since and r >= SPEAKING_RMS:
                        return t
            await asyncio.sleep(0.01)
        return None

    async def _converse(self, source) -> None:
        rep = self.report
        rep.dispatch_to_agent_joined_ms = (self._agent_joined_s or 0) * 1000

        # ── greeting ───────────────────────────────────────────────────────────
        greet_at = await self._first_audio_after(0.0, self.args.greeting_timeout)
        if greet_at is None:
            rep.errors.append(
                f"agent joined but produced NO AUDIBLE AUDIO within "
                f"{self.args.greeting_timeout}s — dead air with a present agent."
            )
        else:
            rep.dispatch_to_first_audio_ms = greet_at * 1000
            rep.agent_joined_to_first_audio_ms = (greet_at - (self._agent_joined_s or 0)) * 1000
            await self._wait_for_agent_silence(greet_at, timeout=self.args.utterance_timeout)

        voice = CallerVoice(Path(self.args.cache_dir), self.args.caller_voice)

        # ── barge-in test (optional, done first so it can't be polluted) ───────
        if self.args.interrupt:
            await self._barge_in(source, voice)

        # ── turns ──────────────────────────────────────────────────────────────
        for i, text in enumerate(self.args.turns):
            pcm = await voice.utterance(text)
            # Small gap before speaking so the agent's own trailing audio and the
            # caller's opening syllable are never adjacent in the same VAD window.
            await asyncio.sleep(0.4)
            start, end = await self._say(source, pcm)
            first = await self._first_audio_after(end, self.args.turn_timeout)
            rep.turns.append(
                asdict(Turn(
                    index=i,
                    text=text,
                    caller_speech_start_s=round(start, 3),
                    caller_speech_end_s=round(end, 3),
                    agent_first_audio_s=round(first, 3) if first else None,
                    turn_latency_ms=round((first - end) * 1000, 1) if first else None,
                    agent_utterance_index=None,
                ))
            )
            if first is None:
                rep.errors.append(
                    f"turn {i} ({text[:40]!r}): NO REPLY within {self.args.turn_timeout}s"
                )
                break
            await self._wait_for_agent_silence(first, timeout=self.args.utterance_timeout)

    async def _barge_in(self, source, voice: CallerVoice) -> None:
        """Talk over the agent and measure how quickly its audio stops."""
        rep = self.report
        prompt = await voice.utterance(self.args.interrupt_prompt)
        interjection = await voice.utterance(self.args.interrupt_text)

        await asyncio.sleep(0.4)
        _, end = await self._say(source, prompt)
        first = await self._first_audio_after(end, self.args.turn_timeout)
        if first is None:
            rep.notes.append("barge-in skipped: agent never started speaking to interrupt")
            return

        # Let it get properly going, then cut in.
        await asyncio.sleep(self.args.interrupt_after)
        cut_at = self.now()
        await self._say(source, interjection)

        # Agent audio should stop. Find the last loud frame after the cut.
        await asyncio.sleep(2.0)
        async with self._audio_lock:
            loud_after = [t for t, r in self._energy if t >= cut_at and r >= SPEAKING_RMS]
        if not loud_after:
            rep.interruption_stop_ms = 0.0
            rep.notes.append("barge-in: agent was already silent at cut-in")
            return
        # The agent kept talking until this point despite us speaking over it.
        # Contiguity matters: its REPLY to the interjection is also loud audio,
        # so only count the run that begins immediately at the cut.
        stop = cut_at
        for t in sorted(loud_after):
            if (t - stop) * 1000 > _UTTERANCE_END_SILENCE_MS:
                break
            stop = t
        rep.interruption_stop_ms = (stop - cut_at) * 1000
        await self._wait_for_agent_silence(stop, timeout=self.args.utterance_timeout)

    # -- analysis --------------------------------------------------------------
    def _finalise(self) -> Report:
        rep = self.report
        e = self._energy
        if e:
            rep.agent_audio_seconds = sum(1 for _, r in e if r >= SPEAKING_RMS) * FRAME_MS / 1000

            # Segment into utterances, and find silent gaps INSIDE each one.
            # A gap inside an utterance is what a listener calls a stutter; the
            # silence between utterances is just the agent having finished.
            utts: list[Utterance] = []
            cur_start = None
            last_loud = None
            gaps: list[float] = []
            for t, r in e:
                if r >= SPEAKING_RMS:
                    if cur_start is None:
                        cur_start, last_loud, gaps = t, t, []
                    else:
                        gap_ms = (t - last_loud) * 1000
                        if gap_ms >= _UTTERANCE_END_SILENCE_MS:
                            utts.append(Utterance(len(utts), cur_start, last_loud,
                                                  last_loud - cur_start, gaps))
                            cur_start, gaps = t, []
                        elif gap_ms >= _STUTTER_GAP_MS:
                            gaps.append(gap_ms)
                        last_loud = t
            if cur_start is not None and last_loud is not None:
                utts.append(Utterance(len(utts), cur_start, last_loud, last_loud - cur_start, gaps))
            rep.utterances = [asdict(u) for u in utts]

        out = Path(self.args.out_dir) / f"{self.args.label}-{time.strftime('%Y%m%d-%H%M%S')}"
        out.mkdir(parents=True, exist_ok=True)
        if self._agent_pcm:
            _write_wav(out / "agent.wav", np.concatenate(self._agent_pcm), SAMPLE_RATE)
        (out / "report.json").write_text(json.dumps(asdict(rep), indent=2), encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(rep.summary(), indent=2), encoding="utf-8")
        rep.notes.append(f"artifacts: {out}")
        return rep


def _write_wav(path: Path, pcm: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ── CLI ───────────────────────────────────────────────────────────────────────
DEFAULT_TURNS = [
    "Hello, I would like to book an appointment with a doctor please.",
    "Tomorrow morning would work well for me. What times do you have?",
]


def _load_dotenv() -> None:
    p = REPO_ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="probe")
    ap.add_argument("--turns", nargs="*", default=DEFAULT_TURNS)
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "scripts" / "voice_probe_runs"))
    ap.add_argument("--cache-dir", default=str(REPO_ROOT / "scripts" / "voice_probe_runs" / "_voice_cache"))

    ap.add_argument("--stt"); ap.add_argument("--stt-model"); ap.add_argument("--stt-language")
    ap.add_argument("--tts"); ap.add_argument("--tts-model"); ap.add_argument("--tts-language")
    ap.add_argument("--tts-voice")
    ap.add_argument("--llm"); ap.add_argument("--llm-model")
    ap.add_argument("--agent-id"); ap.add_argument("--tenant-id")
    ap.add_argument("--system-prompt")
    ap.add_argument("--clinic-name", default="Probe Clinic")
    ap.add_argument("--first-message", default="Hello, thank you for calling. How can I help you today?")
    ap.add_argument("--caller-voice", choices=["elevenlabs", "sarvam"], default=None)

    ap.add_argument("--interrupt", action="store_true", help="run a barge-in test")
    ap.add_argument("--interrupt-prompt", default="Please tell me in detail about all the services your clinic offers.")
    ap.add_argument("--interrupt-text", default="Sorry, actually I just need the opening hours.")
    ap.add_argument("--interrupt-after", type=float, default=1.5)

    ap.add_argument("--join-timeout", type=float, default=45.0)
    ap.add_argument("--greeting-timeout", type=float, default=30.0)
    ap.add_argument("--turn-timeout", type=float, default=30.0)
    ap.add_argument("--utterance-timeout", type=float, default=45.0)
    ap.add_argument("--silence-timeout", type=int, default=25)
    ap.add_argument("--max-duration", type=int, default=180)

    ap.add_argument("--wake-worker", action="store_true", default=True)
    ap.add_argument("--no-wake-worker", dest="wake_worker", action="store_false")
    ap.add_argument("--worker-url", default=None)
    ap.add_argument("--post-wake-settle", type=float, default=25.0)
    ap.add_argument("--delete-room", action="store_true", default=True)
    ap.add_argument("--keep-room", dest="delete_room", action="store_false")

    args = ap.parse_args()

    probe = VoiceProbe(args)
    rep = asyncio.run(probe.run())
    print(json.dumps(rep.summary(), indent=2))
    for n in rep.notes:
        print(f"  note: {n}", file=sys.stderr)
    for e in rep.errors:
        print(f"  ERROR: {e}", file=sys.stderr)
    return 1 if rep.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
