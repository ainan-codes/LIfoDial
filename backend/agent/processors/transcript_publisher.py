"""
backend/agent/processors/transcript_publisher.py

Mirrors the agent's spoken text into the LiveKit room as transcription segments
so the browser Test Agent widget (useVoiceAssistant().agentTranscriptions) can
render a LIVE transcript. Pipecat 1.5.0's LiveKit transport does not publish any
transcriptions itself, so we bridge it here.

SAFETY (this touches the real-time voice pipeline):
  • Transparent passthrough — EVERY frame is pushed downstream unchanged.
  • Every LiveKit publish is wrapped in try/except and can never raise into the
    pipeline, so a failure here cannot stall, mangle, or drop a live call.
  • If the room / agent audio track can't be resolved, it simply no-ops.

Only the AGENT's speech is published (attributed to the worker's local
participant). That is what the Test widget renders. Caller-side transcript would
need the user's mic track and is a separate follow-up.
"""
import asyncio
import logging

from pipecat.frames.frames import Frame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger(__name__)


class LiveKitTranscriptPublisher(FrameProcessor):
    """Publishes TTSTextFrame text to the room as agent transcription segments."""

    def __init__(self, transport):
        super().__init__()
        self._transport = transport
        self._counter = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                # FIRE-AND-FORGET: publishing is a LiveKit round trip and is purely
                # cosmetic (drives the browser test transcript). It must NEVER gate
                # audio — awaiting it here stalled every TTS text frame (and the
                # audio queued behind it) by one publish RTT, delaying the first
                # spoken word and stuttering every turn. Schedule it in the
                # background and forward the frame immediately.
                asyncio.create_task(self._safe_publish(text))
        # Always forward the frame unchanged, without waiting on the publish.
        await self.push_frame(frame, direction)

    async def _safe_publish(self, text: str) -> None:
        try:
            await self._publish(text)
        except Exception as e:  # never propagate into the call
            log.warning("Transcript publish failed (non-fatal): %s", e)

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

    async def _publish(self, text: str):
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
