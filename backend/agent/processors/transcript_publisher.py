"""
backend/agent/processors/transcript_publisher.py

Mirrors BOTH the agent's spoken text AND the user's transcribed speech into the
LiveKit room so the browser widget can render a live dual-sided transcript.

Two channels:
  1. Agent speech (TTSTextFrame) → room.local_participant.publish_transcription()
     → received as RoomEvent.TranscriptionReceived on the frontend from the
       agent participant identity
  2. User speech (TranscriptionFrame from STT) → room.local_participant.publish_data()
     → JSON payload {"role":"user","text":"..."} on topic "lifodial-transcript"
     → received as RoomEvent.DataReceived on the frontend

SAFETY:
  • Transparent passthrough — EVERY frame is pushed downstream unchanged.
  • Every LiveKit publish is wrapped in try/except and can never raise into the
    pipeline, so a failure here cannot stall, mangle, or drop a live call.
  • If the room / agent audio track can't be resolved, it simply no-ops.
"""
import asyncio
import json
import logging

from pipecat.frames.frames import Frame, TTSTextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger(__name__)

# Data channel topic for user transcript messages (listened to in frontend)
_TRANSCRIPT_TOPIC = "lifodial-transcript"


class LiveKitTranscriptPublisher(FrameProcessor):
    """Publishes both TTSTextFrame (agent) and TranscriptionFrame (user) to the
    LiveKit room so the frontend can display a live dual-sided transcript."""

    def __init__(self, transport):
        super().__init__()
        self._transport = transport
        self._counter = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSTextFrame):
            # Agent speech: publish via LiveKit transcription protocol
            # (shows up as RoomEvent.TranscriptionReceived attributed to agent participant)
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                asyncio.create_task(self._safe_publish_agent(text))

        elif isinstance(frame, TranscriptionFrame):
            # User speech from STT: publish as data channel message
            # (shows up as RoomEvent.DataReceived in the frontend)
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                asyncio.create_task(self._safe_publish_user(text))

        # Always forward the frame unchanged, without waiting on the publish.
        await self.push_frame(frame, direction)

    async def _safe_publish_agent(self, text: str) -> None:
        try:
            await self._publish_agent_transcription(text)
        except Exception as e:
            log.warning("Agent transcript publish failed (non-fatal): %s", e)

    async def _safe_publish_user(self, text: str) -> None:
        try:
            await self._publish_user_data(text)
        except Exception as e:
            log.warning("User transcript publish failed (non-fatal): %s", e)

    def _resolve_room(self):
        """Best-effort access to the LiveKit room across pipecat's transport
        input/output split — all guarded, returns None if unavailable."""
        t = self._transport
        for getter in (
            lambda: t._client.room,          # some builds expose the client here
            lambda: t.output()._client.room,  # output transport holds the client
            lambda: t.input()._client.room,   # input transport holds the client
        ):
            try:
                room = getter()
                if room is not None:
                    return room
            except Exception:
                continue
        return None

    @staticmethod
    def _agent_audio_track_sid(room) -> str:
        try:
            from livekit import rtc
            for pub in room.local_participant.track_publications.values():
                if getattr(pub, "kind", None) == rtc.TrackKind.KIND_AUDIO:
                    return pub.sid or ""
        except Exception:
            pass
        return ""

    async def _publish_agent_transcription(self, text: str):
        """Publish agent speech via LiveKit's transcription protocol.
        Frontend receives this as RoomEvent.TranscriptionReceived."""
        from livekit import rtc

        room = self._resolve_room()
        if room is None or getattr(room, "local_participant", None) is None:
            return
        self._counter += 1
        segment = rtc.TranscriptionSegment(
            id=f"agent-{self._counter}",
            text=text,
            start_time=0,
            end_time=0,
            language="",
            final=True,
        )
        transcription = rtc.Transcription(
            participant_identity=room.local_participant.identity,
            track_sid=self._agent_audio_track_sid(room),
            segments=[segment],
        )
        await room.local_participant.publish_transcription(transcription)

    async def _publish_user_data(self, text: str):
        """Publish user speech via LiveKit data channel as JSON.
        Frontend receives this as RoomEvent.DataReceived with topic 'lifodial-transcript'."""
        room = self._resolve_room()
        if room is None or getattr(room, "local_participant", None) is None:
            return
        payload = json.dumps({"role": "user", "text": text}).encode("utf-8")
        await room.local_participant.publish_data(
            payload,
            topic=_TRANSCRIPT_TOPIC,
            reliable=True,
        )
