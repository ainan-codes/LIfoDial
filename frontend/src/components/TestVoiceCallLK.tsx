/**
 * TestVoiceCallLK.tsx — LiveKit voice call widget with live transcription
 *
 * Features:
 *  - Live dual-sided transcript (agent via RoomEvent.TranscriptionReceived,
 *    user via RoomEvent.DataReceived on topic 'lifodial-transcript')
 *  - BarVisualizer animation — pulses when agent speaks
 *  - Active speaker detection using RoomEvent.ActiveSpeakersChanged
 *  - Proper agent detection (by identity prefix 'lifodial-agent-*')
 *  - iOS Safari compatible (StartAudio for autoplay unlock)
 *  - Deepgram STT: real-time streaming (~200ms TTFB vs 800ms Sarvam batch)
 *
 * Pipeline flow:
 *  Microphone → VAD → Deepgram STT → TranscriptionFrame → LLM → TTSTextFrame
 *                                          ↓                          ↓
 *                             LiveKitTranscriptPublisher (user)   (agent)
 *                                          ↓                          ↓
 *                             DataReceived (topic: lifodial-transcript)
 *                             TranscriptionReceived (LiveKit protocol)
 */
import { Headphones, Mic, PhoneOff, RotateCcw } from 'lucide-react';
import { DEFAULT_STT_PROVIDER } from '../api/lockedDefaults';
import { useEffect, useRef, useState, useCallback } from 'react';
import {
  LiveKitRoom,
  BarVisualizer,
  RoomAudioRenderer,
  StartAudio,
  useRemoteParticipants,
  useTracks,
  useConnectionState,
  useRoomContext,
} from '@livekit/components-react';
import { Track, ConnectionState, RoomEvent, type Participant, type TranscriptionSegment } from 'livekit-client';
import '@livekit/components-styles';
import fetchWithAuth from '../api/client';

const SLOW_MS = 12_000;
// Must exceed the backend's worker pre-warm budget (WARM_TIMEOUT_SECONDS = 150s
// in backend/services/agent_worker.py). /web-call-token blocks while a spun-down
// free-tier agent worker cold-starts (measured ~55s) so the room is never created
// before the worker can join it. If the browser gives up first the user sees a
// timeout for a call that was about to succeed — which is why this is deliberately
// well above the server's own budget, not equal to it.
const TIMEOUT_MS = 200_000;
const AGENT_WAIT_MS = 45_000;
const TRANSCRIPT_TOPIC = 'lifodial-transcript';
// Stable id for the single in-progress user bubble. Interim STT results overwrite
// this entry in place; the final result promotes it to a committed message.
const LIVE_USER_ID = 'user-live';

interface TranscriptEntry {
  id: string;
  role: 'agent' | 'user';
  text: string;
  final: boolean;
  ts: number;
}

// ─── Transcript panel ────────────────────────────────────────────────────────
// Extracted so the live call and the post-call review screen render the SAME
// transcript markup. Two copies would drift, and the ended view is the one an
// evaluating clinic actually reads.
//
// Deliberately takes no LiveKit context: it must render after the room is gone.
function TranscriptPanel({
  transcript,
  agentName,
  title,
  dotColor,
  emptyText,
}: {
  transcript: TranscriptEntry[];
  agentName?: string;
  title: string;
  dotColor: string;
  emptyText: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the newest line while the call is live. Harmless once ended:
  // the transcript stops changing, so this stops firing.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcript]);

  return (
    <div style={{
      flex: 1, minHeight: 0,
      background: '#0d0d0d', border: '1px solid #1f1f1f',
      borderRadius: 10, overflow: 'hidden',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        padding: '8px 12px', borderBottom: '1px solid #1a1a1a',
        fontSize: 11, color: '#444', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor }} />
        {title}
      </div>

      <div
        ref={scrollRef}
        style={{
          flex: 1, overflowY: 'auto', padding: '10px 12px',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}
      >
        {transcript.length === 0 ? (
          <div style={{ fontSize: 12, color: '#333', textAlign: 'center', marginTop: 16 }}>
            {emptyText}
          </div>
        ) : (
          transcript.map((entry) => (
            <TranscriptBubble key={entry.id} entry={entry} agentName={agentName} />
          ))
        )}
      </div>
    </div>
  );
}

// ─── Inner component — must be inside <LiveKitRoom> ─────────────────────────

function TestCallUI({
  agentName,
  avatarUrl,
  onDisconnect,
  onRetry,
  transcript,
  setTranscript,
}: {
  agentName?: string;
  avatarUrl?: string;
  onDisconnect: () => void;
  onRetry?: () => void;
  // Owned by the PARENT, not by this component. This subtree unmounts when the
  // room closes, so transcript state held here died with the call — which is why
  // ending a call used to wipe the transcript.
  transcript: TranscriptEntry[];
  setTranscript: React.Dispatch<React.SetStateAction<TranscriptEntry[]>>;
}) {
  // NOTE: mic state is read from the room itself (below) rather than taken from
  // the parent's `micAvailable` pre-flight — the pre-flight only proves
  // getUserMedia *could* open a device, not that LiveKitRoom actually published
  // the track. The publication is the only thing the agent can hear.
  const room = useRoomContext();
  const connState = useConnectionState();
  const remoteParticipants = useRemoteParticipants();

  // ── Audio unlock ──────────────────────────────────────────────────────────
  const [audioUnlocked, setAudioUnlocked] = useState(false);
  useEffect(() => {
    if (connState === ConnectionState.Connected && room) {
      room.startAudio().then(() => setAudioUnlocked(true)).catch(() => {});
    }
  }, [connState, room]);

  // ── Track subscription ────────────────────────────────────────────────────
  // Do NOT pass updateOnlyOn — let the SDK use its defaults (all relevant events)
  const allTracks = useTracks(
    [Track.Source.Microphone, Track.Source.ScreenShareAudio, Track.Source.Unknown],
    { onlySubscribed: false }
  );

  // Find agent audio track
  const agentTrackRef = allTracks.find(
    (t) =>
      t.participant &&
      t.participant.identity !== room?.localParticipant?.identity &&
      t.participant.identity.startsWith('lifodial-agent')
  ) ?? allTracks.find(
    (t) =>
      t.participant &&
      t.participant.identity !== room?.localParticipant?.identity
  ) ?? null;

  const agentParticipant: Participant | null =
    agentTrackRef?.participant ??
    remoteParticipants.find((p) => p.identity.startsWith('lifodial-agent')) ??
    remoteParticipants[0] ??
    null;

  // ── Agent state (attributes from livekit-agents framework) ───────────────
  const [agentState, setAgentState] = useState<string>('connecting');
  useEffect(() => {
    if (!agentParticipant) { setAgentState('connecting'); return; }
    const attrs = (agentParticipant as any).attributes || {};
    setAgentState(attrs['lk.agent.state'] || 'idle');
    const handler = () => {
      const updated = (agentParticipant as any).attributes || {};
      setAgentState(updated['lk.agent.state'] || 'idle');
    };
    agentParticipant.on('attributesChanged', handler);
    return () => { agentParticipant.off('attributesChanged', handler); };
  }, [agentParticipant]);

  // ── Active speaker: is agent currently speaking? ──────────────────────────
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  useEffect(() => {
    if (!room) return;
    const handler = (speakers: Participant[]) => {
      const speaking = speakers.some(
        (s) => s.identity === agentParticipant?.identity
      );
      setAgentSpeaking(speaking);
    };
    room.on(RoomEvent.ActiveSpeakersChanged, handler);
    return () => { room.off(RoomEvent.ActiveSpeakersChanged, handler); };
  }, [room, agentParticipant]);

  // ── Live transcript ────────────────────────────────────────────────────────
  // `transcript` / `setTranscript` now arrive as props from TestVoiceCallLK so the
  // lines survive this subtree unmounting when the call ends.
  const pendingAgentRef = useRef<Map<string, string>>(new Map());
  // Newest user-transcript seq seen, for discarding out-of-order publishes.
  const lastUserSeqRef = useRef(0);
  // Counter giving each committed user utterance a stable, unique key.
  const userFinalCountRef = useRef(0);

  // Agent speech via RoomEvent.TranscriptionReceived
  useEffect(() => {
    if (!room) return;
    const handler = (
      segments: TranscriptionSegment[],
      participant?: Participant,
    ) => {
      // Only handle agent's transcriptions (not user's own mic)
      if (participant?.identity === room.localParticipant.identity) return;

      segments.forEach((seg) => {
        const text = seg.text?.trim();
        if (!text) return;
        const id = `agent-${seg.id}`;

        if (seg.final) {
          // Final segment: replace interim or append
          setTranscript((prev) => {
            const existing = prev.findIndex((e) => e.id === id);
            const entry: TranscriptEntry = { id, role: 'agent', text, final: true, ts: Date.now() };
            if (existing >= 0) {
              const updated = [...prev];
              updated[existing] = entry;
              return updated;
            }
            return [...prev, entry];
          });
          pendingAgentRef.current.delete(id);
        } else {
          // Interim segment: update in-place without duplicating
          pendingAgentRef.current.set(id, text);
          setTranscript((prev) => {
            const existing = prev.findIndex((e) => e.id === id);
            const entry: TranscriptEntry = { id, role: 'agent', text, final: false, ts: Date.now() };
            if (existing >= 0) {
              const updated = [...prev];
              updated[existing] = entry;
              return updated;
            }
            return [...prev, entry];
          });
        }
      });
    };

    room.on(RoomEvent.TranscriptionReceived, handler);
    return () => { room.off(RoomEvent.TranscriptionReceived, handler); };
  }, [room]);

  // User speech via RoomEvent.DataReceived (topic: 'lifodial-transcript')
  useEffect(() => {
    if (!room) return;
    // Untyped trailing params (kind/topic/encryptionType) — unused here, and
    // typing them to match RoomEvent.DataReceived's real signature exactly
    // isn't worth the churn since room.on() infers the handler contextually.
    const handler = (payload: Uint8Array, ..._rest: unknown[]) => {
      try {
        const data = JSON.parse(new TextDecoder().decode(payload));
        if (data.role !== 'user') return;
        const text = (data.text ?? '').trim();
        if (!text) return;

        // Publishes are fired as background tasks agent-side, so a slow interim
        // can land after its own final. Drop anything older than the newest seen
        // or finished text would be overwritten by a stale partial.
        const seq = typeof data.seq === 'number' ? data.seq : 0;
        if (seq) {
          if (seq < lastUserSeqRef.current) return;
          lastUserSeqRef.current = seq;
        }

        // Agent builds before the interim-transcript change omit `final`; treat a
        // missing flag as final so an older worker still renders correctly.
        const isFinal = data.final !== false;

        setTranscript((prev) => {
          const liveIdx = prev.findIndex((e) => e.id === LIVE_USER_ID);

          if (isFinal) {
            // Promote the in-progress bubble to a committed one (keeping its
            // position) so the next utterance starts a fresh live bubble.
            const committed: TranscriptEntry = {
              id: `user-final-${++userFinalCountRef.current}`,
              role: 'user', text, final: true, ts: Date.now(),
            };
            if (liveIdx >= 0) {
              const updated = [...prev];
              updated[liveIdx] = committed;
              return updated;
            }
            return [...prev, committed];
          }

          // Interim: update one bubble in place rather than appending per result.
          const live: TranscriptEntry = {
            id: LIVE_USER_ID, role: 'user', text, final: false, ts: Date.now(),
          };
          if (liveIdx >= 0) {
            const updated = [...prev];
            updated[liveIdx] = live;
            return updated;
          }
          return [...prev, live];
        });
      } catch {
        // ignore malformed JSON
      }
    };

    room.on(RoomEvent.DataReceived, handler);
    return () => { room.off(RoomEvent.DataReceived, handler); };
  }, [room]);

  // (Auto-scroll now lives in TranscriptPanel, which owns the scroll container.)

  // ── Mic publication state ─────────────────────────────────────────────────
  // Without this the widget had NO signal that the caller's mic was dead: if
  // getUserMedia was denied, <LiveKitRoom audio={false}> connected happily, the
  // agent greeted, and nothing was ever heard — indistinguishable from a broken
  // STT key. Read the actual local mic publication so the failure is visible and
  // recoverable in-place.
  const [micLive, setMicLive] = useState(false);
  const [micBusy, setMicBusy] = useState(false);
  useEffect(() => {
    if (!room) return;
    const update = () => {
      const pub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      setMicLive(!!pub?.track && !pub.isMuted);
    };
    update();
    const events = [
      RoomEvent.LocalTrackPublished,
      RoomEvent.LocalTrackUnpublished,
      RoomEvent.TrackMuted,
      RoomEvent.TrackUnmuted,
      RoomEvent.Connected,
    ] as const;
    events.forEach((e) => room.on(e, update));
    return () => { events.forEach((e) => room.off(e, update)); };
  }, [room]);

  const enableMic = useCallback(async () => {
    if (!room) return;
    setMicBusy(true);
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
    } catch (e) {
      console.error('Could not enable microphone:', e);
    } finally {
      setMicBusy(false);
    }
  }, [room]);

  // ── UI state ──────────────────────────────────────────────────────────────
  const agentReady =
    connState === ConnectionState.Connected &&
    (!!agentParticipant || remoteParticipants.length > 0);

  const [waitedLong, setWaitedLong] = useState(false);
  useEffect(() => {
    if (agentReady) { setWaitedLong(false); return; }
    const t = setTimeout(() => setWaitedLong(true), AGENT_WAIT_MS);
    return () => clearTimeout(t);
  }, [agentReady]);

  const stateConfig: Record<string, { label: string; color: string }> = {
    listening: { label: '🎤 Listening…', color: '#3B82F6' },
    thinking:  { label: '💭 Thinking…',  color: '#F59E0B' },
    speaking:  { label: '🔊 Speaking',   color: '#3ECF8E' },
    idle:      { label: '● Ready',       color: '#3ECF8E' },
  };

  const { label, color } = agentReady
    ? (stateConfig[agentState] ?? { label: '● Live', color: '#3ECF8E' })
    : { label: 'Connecting to agent…', color: '#F59E0B' };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0,
      padding: '12px', gap: 10,
    }}>
      {/* Audio unlock — required for iOS Safari */}
      {!audioUnlocked && (
        <div style={{ textAlign: 'center' }}>
          <StartAudio
            label="🔊 Tap to Enable Audio"
            style={{
              background: '#3ECF8E', color: '#051b11', padding: '8px 20px',
              borderRadius: 20, fontWeight: 700, border: 'none', cursor: 'pointer', fontSize: 13,
              // ── These four are load-bearing, not styling ──────────────────
              // @livekit/components-styles (imported globally at the top of this
              // file) ships:
              //     .lk-start-audio-button { position: fixed; top: 50%;
              //                              left: 50%; transform: translate(-50%,-50%) }
              // StartAudio renders exactly that class, so without these overrides
              // the button leaves this panel entirely and pins itself to the
              // CENTRE OF THE VIEWPORT — a fixed element floating over the page
              // content, eating clicks wherever it lands, for as long as audio
              // stays locked. Inline styles beat a class rule only for the
              // properties they declare, and the component's own style prop does
              // not touch position, so all four have to be named here.
              //
              // Easy to miss when testing: Chrome launched with
              // --autoplay-policy=no-user-gesture-required unlocks audio
              // immediately and this button never renders at all.
              position: 'relative', top: 'auto', left: 'auto', transform: 'none',
            }}
          />
        </div>
      )}

      {/* Mic-dead warning — the agent literally cannot hear you in this state */}
      {connState === ConnectionState.Connected && !micLive && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
          justifyContent: 'center', textAlign: 'left',
          background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.45)',
          borderRadius: 8, padding: '8px 10px', fontSize: 11, color: '#FCA5A5',
          lineHeight: 1.45,
        }}>
          <span style={{ flex: 1, minWidth: 150 }}>
            <strong>Microphone is not live.</strong> The agent can speak but cannot
            hear you. Allow mic access for this site, then re-enable.
          </span>
          <button
            onClick={enableMic}
            disabled={micBusy}
            style={{
              padding: '5px 12px', borderRadius: 14, border: '1px solid #EF4444',
              background: 'rgba(239,68,68,0.18)', color: '#FCA5A5',
              fontWeight: 700, fontSize: 11,
              cursor: micBusy ? 'default' : 'pointer', whiteSpace: 'nowrap',
              opacity: micBusy ? 0.6 : 1,
            }}
          >
            {micBusy ? 'Enabling…' : '🎤 Enable mic'}
          </button>
        </div>
      )}

      {/* Agent avatar + visualizer */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <div style={{ position: 'relative' }}>
          {/* Pulse ring when agent is speaking */}
          {agentSpeaking && (
            <div style={{
              position: 'absolute', inset: -6, borderRadius: '50%',
              border: `2px solid ${color}`, opacity: 0.7,
              animation: 'ringPulse 1s ease-out infinite',
            }} />
          )}
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt={agentName || 'Agent'}
              style={{
                width: 52, height: 52, borderRadius: '50%', objectFit: 'cover',
                border: `2.5px solid ${color}`,
                boxShadow: agentSpeaking ? `0 0 20px ${color}99` : `0 0 10px ${color}44`,
                transition: 'border-color 0.3s, box-shadow 0.3s',
              }}
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
            />
          ) : (
            <div style={{
              width: 52, height: 52, borderRadius: '50%',
              background: `${color}18`, border: `2.5px solid ${color}`,
              boxShadow: agentSpeaking ? `0 0 20px ${color}99` : `0 0 10px ${color}44`,
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24,
              transition: 'box-shadow 0.3s',
            }}>
              🎧
            </div>
          )}
        </div>

        {/* Voice visualizer */}
        <div style={{ width: '100%', maxWidth: 320, height: 52 }}>
          {agentReady && agentTrackRef ? (
            <BarVisualizer
              trackRef={agentTrackRef}
              barCount={30}
              options={{ minHeight: 3 }}
              style={{
                '--lk-fg': color,
                height: '52px', width: '100%',
                transition: '--lk-fg 0.3s',
              } as React.CSSProperties}
            />
          ) : (
            <div style={{ height: 52, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{
                width: 32, height: 32,
                border: '3px solid #1a1a1a', borderTopColor: color,
                borderRadius: '50%', animation: 'spin 0.8s linear infinite',
              }} />
            </div>
          )}
        </div>

        <div style={{ fontSize: 12, fontWeight: 600, color }}>{label}</div>

        {/* Long-wait hint */}
        {!agentReady && waitedLong && (
          <div style={{ fontSize: 11, color: '#F59E0B', textAlign: 'center', maxWidth: 300, lineHeight: 1.5 }}>
            Voice service may be cold-starting (~30s on Render).{' '}
            {onRetry && (
              <button
                onClick={onRetry}
                style={{ background: 'none', border: 'none', color: '#3ECF8E', textDecoration: 'underline', cursor: 'pointer', font: 'inherit', padding: 0 }}
              >
                Retry
              </button>
            )}
          </div>
        )}
      </div>

      {/* Live transcript panel — same component the post-call review screen uses. */}
      <TranscriptPanel
        transcript={transcript}
        agentName={agentName}
        title="Live Transcript"
        dotColor={agentReady ? '#3ECF8E' : '#F59E0B'}
        emptyText={agentReady
          ? 'Speak naturally — transcript will appear here'
          : 'Waiting for the agent to join the call…'}
      />

      {/* Debug info (dev) */}
      {import.meta.env.DEV && remoteParticipants.length > 0 && (
        <div style={{ fontSize: 10, color: '#333', lineHeight: 1.4 }}>
          {remoteParticipants.map((p) => (
            <span key={p.identity} style={{ marginRight: 6 }}>
              👤 {p.identity} ({allTracks.filter((t) => t.participant?.identity === p.identity).length}tr)
            </span>
          ))}
          {agentTrackRef && (
            <span> 🎵 {agentTrackRef.participant?.identity}</span>
          )}
        </div>
      )}

      {/* Disconnect button */}
      <button
        onClick={onDisconnect}
        style={{
          alignSelf: 'center',
          padding: '9px 24px', borderRadius: 40, border: '1px solid #ef4444',
          background: 'rgba(239,68,68,0.08)', color: '#ef4444',
          fontWeight: 600, fontSize: 13, cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 6,
          transition: 'all 0.2s ease',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(239,68,68,0.2)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(239,68,68,0.08)'; }}
      >
        <PhoneOff size={14} />
        End Call
      </button>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes ringPulse {
          0%   { transform: scale(1); opacity: 0.8; }
          100% { transform: scale(1.5); opacity: 0; }
        }
        @keyframes bubbleIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

// ─── Transcript bubble ───────────────────────────────────────────────────────

function TranscriptBubble({
  entry,
  agentName,
}: {
  entry: TranscriptEntry;
  agentName?: string;
}) {
  const isAgent = entry.role === 'agent';
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: isAgent ? 'flex-start' : 'flex-end',
        animation: 'bubbleIn 0.2s ease',
      }}
    >
      <div style={{ fontSize: 10, color: '#444', marginBottom: 2, padding: '0 4px' }}>
        {isAgent ? agentName || 'Agent' : 'You'}
      </div>
      <div
        style={{
          maxWidth: '85%',
          padding: '7px 11px',
          borderRadius: isAgent ? '4px 12px 12px 12px' : '12px 4px 12px 12px',
          background: isAgent ? 'rgba(62,207,142,0.12)' : 'rgba(96,165,250,0.12)',
          border: `1px solid ${isAgent ? 'rgba(62,207,142,0.25)' : 'rgba(96,165,250,0.25)'}`,
          color: entry.final ? (isAgent ? '#cffae4' : '#bfdbfe') : '#666',
          fontSize: 13,
          lineHeight: 1.5,
          fontStyle: entry.final ? 'normal' : 'italic',
        }}
      >
        {entry.text}
        {!entry.final && (
          <span style={{ marginLeft: 4, opacity: 0.5 }}>…</span>
        )}
      </div>
    </div>
  );
}

// ─── Outer shell with LiveKitRoom ────────────────────────────────────────────

export default function TestVoiceCallLK({
  agent,
  agentId,
  agentName,
  avatarUrl,
  onClose,
}: {
  agent?: any;
  agentId?: string;
  agentName?: string;
  avatarUrl?: string;
  onClose?: () => void;
}) {
  const [token, setToken] = useState('');
  const [wsUrl, setWsUrl] = useState('');
  // 'ended' is the post-call review state: the room is torn down and the audio
  // stopped, but the transcript and controls stay on screen. Ending a call is NOT
  // the same event as closing this panel, and conflating the two is what made the
  // transcript unreadable the instant the call finished.
  const [phase, setPhase] = useState<'idle' | 'connecting' | 'live' | 'ended' | 'error' | 'demo'>('idle');
  const [error, setError] = useState('');
  // Owned here, not in TestCallUI: that component lives inside <LiveKitRoom> and
  // unmounts with it, so a transcript held there was destroyed by the disconnect
  // that produced it.
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [micAvailable, setMicAvailable] = useState(true);
  const [slow, setSlow] = useState(false);

  // ── Wake the voice worker as soon as this panel opens ─────────────────────
  // The worker is on Render's free plan and sleeps after 15 min idle; booting it
  // takes ~55s. Doing that when the user presses Start puts the whole boot in
  // front of them (and used to time the call out entirely). Firing it on mount
  // overlaps the boot with them reading this screen, so Start is usually instant.
  // Fire-and-forget: a failure here changes nothing, startCall still waits.
  const [workerWarm, setWorkerWarm] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchWithAuth('/agents/voice-worker/warm', { method: 'POST' })
      .then((d: any) => { if (!cancelled) setWorkerWarm(!!d?.warm); })
      .catch(() => { if (!cancelled) setWorkerWarm(null); });

    // Poll until it reports ready, so the button can say so honestly.
    const iv = setInterval(() => {
      fetchWithAuth('/agents/voice-worker/status')
        .then((d: any) => {
          if (cancelled) return;
          setWorkerWarm(!!d?.warm);
          if (d?.warm) clearInterval(iv);
        })
        .catch(() => {});
    }, 5000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const startCall = useCallback(async () => {
    setPhase('connecting');
    setError('');
    setSlow(false);
    // Clear the PREVIOUS call's transcript here, at the start of a new one, rather
    // than when a call ends — that is what lets the ended screen still show it.
    setTranscript([]);

    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);
    const hardTimer = setTimeout(() => {
      setError('Connection timed out. The voice service may be cold-starting (~30s on Render free tier). Please try again.');
      setPhase('error');
    }, TIMEOUT_MS);

    try {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        setMicAvailable(true);
      } catch (micErr: any) {
        console.warn('Mic unavailable:', micErr?.name || micErr);
        setMicAvailable(false);
      }

      const data = await fetchWithAuth(`/agents/${agentId}/web-call-token?test_mode=true`, { method: 'POST' });
      if (data?.demo || !data?.token) {
        setPhase('demo');
        return;
      }
      setToken(data.token);
      setWsUrl(data.wsUrl);
      setPhase('live');
    } catch (e: any) {
      setError(e?.message || 'Failed to start the test call.');
      setPhase('error');
    } finally {
      clearTimeout(hardTimer);
      clearTimeout(slowTimer);
    }
  }, [agentId]);

  // The call finished — hang up, KEEP the panel. Clearing token/wsUrl unmounts
  // <LiveKitRoom>, which disconnects the room and stops all audio; the transcript
  // is untouched because it lives on this component, not in that subtree.
  //
  // This is what RoomEvent.Disconnected and the "End Call" button both run. It
  // deliberately does NOT call onClose: a disconnect is never a request to close
  // the panel. That includes disconnects we did not initiate (network drop, the
  // agent worker dying), where wiping the transcript destroys the only evidence
  // of what went wrong.
  const handleCallEnded = useCallback(() => {
    setToken('');
    setWsUrl('');
    setPhase((p) => (p === 'error' || p === 'demo' ? p : 'ended'));
  }, []);

  // ── Navigating away mid-call ENDS the call. Decided, not incidental. ────────
  //
  // Leaving the page unmounts this component and with it <LiveKitRoom>, which
  // disconnects the room and stops the local mic track — so the call ends cleanly
  // and the browser's recording indicator goes out. There is deliberately no
  // persistent mini-player:
  //
  //   * This is a TEST call against the clinic's own agent, started from one
  //     button on one page. A call that followed the operator around the app would
  //     need a global room provider, and would keep a live mic open on pages that
  //     give no indication a call is running.
  //   * A mini-player is also what created the reported bug's shape — a floating
  //     widget over the app chrome. The panel now stays inside the page that owns
  //     it, and the chrome outranks it either way.
  //
  // Not blocked, and not silent: the panel is visibly gone, and the room's
  // Disconnected event finalises the call record server-side as it would for a
  // hang-up. React Router's useBlocker (to warn first) needs a data router; this
  // app mounts a plain BrowserRouter, so there is no confirm step to hook into.
  useEffect(() => () => {
    // Explicit teardown so the intent is in the code and not only in LiveKitRoom's
    // unmount behaviour. Safe to run when no call is up.
    setToken('');
    setWsUrl('');
  }, []);

  // Back out to the pre-call screen, keeping the panel open. Used by the error and
  // "not configured" screens, where "Back" means "let me try again", not "close".
  const handleBackToIdle = useCallback(() => {
    setToken('');
    setWsUrl('');
    setError('');
    setTranscript([]);
    setPhase('idle');
  }, []);

  // The ONLY path that closes the panel: an explicit user click. Nothing about
  // call status reaches this.
  const handleClose = useCallback(() => {
    setToken('');
    setWsUrl('');
    onClose?.();
  }, [onClose]);

  const shell = (children: React.ReactNode) => (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 14, padding: 24, textAlign: 'center', color: '#fff',
    }}>
      {children}
    </div>
  );

  if (phase === 'idle') {
    // THE one language. This badge used to read stt_language, which is exactly
    // how it came to display "ta-IN" while the editor's header and Language field
    // both said Malayalam — the two columns were independently editable.
    const language = agent?.language || 'en-IN';
    const sttProvider = agent?.stt_provider || DEFAULT_STT_PROVIDER;
    // Providers whose pipecat service actually constructs an
    // InterimTranscriptionFrame. Kept in sync with _STT_REALTIME in
    // backend/agent/pipeline.py — 'elevenlabs' was listed here but pipecat 1.5.0's
    // ElevenLabs STT emits no interim frames at all, so it was reported as
    // real-time when it is not.
    const sttRealtime = ['deepgram', 'assemblyai'].includes(sttProvider);
    // NO llmModel here any more. It rendered a raw vendor model id into a widget a
    // clinic admin uses to test their own receptionist — an internal detail leaking
    // into a product surface, and one they cannot act on since the LLM is locked.
    const ttsVoice = agent?.tts_voice || 'priya';

    return shell(
      <>
        <div style={{ position: 'relative', margin: '12px 0' }}>
          <div style={{
            position: 'absolute', inset: -8, borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(62,207,142,0.4) 0%, rgba(62,207,142,0) 70%)',
            animation: 'pulseGlow 2.5s infinite ease-in-out',
          }} />
          {avatarUrl ? (
            <img
              src={avatarUrl} alt={agentName || 'Agent'}
              style={{
                position: 'relative', width: 80, height: 80, borderRadius: '50%',
                objectFit: 'cover', border: '3px solid #3ECF8E',
                boxShadow: '0 0 24px rgba(62,207,142,0.4)',
              }}
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
            />
          ) : (
            <div style={{
              position: 'relative', width: 80, height: 80, borderRadius: '50%',
              background: 'rgba(62,207,142,0.12)', border: '3px solid #3ECF8E',
              boxShadow: '0 0 24px rgba(62,207,142,0.4)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Headphones size={40} color="#3ECF8E" />
            </div>
          )}
        </div>

        <div style={{ fontSize: 17, fontWeight: 700 }}>{agentName || 'AI Receptionist'}</div>
        {/* Was "Pipecat + LiveKit + Deepgram Nova-3" — four vendor names in a line
            of copy aimed at a clinic admin. What they need to know is that it is a
            live voice call, not which stack builds it. */}
        <div style={{ fontSize: 12, color: '#666', maxWidth: 280, lineHeight: 1.5 }}>
          Real-time AI voice — speak as a patient would on the phone.
        </div>

        {/* Two pills, both actionable by the tester:
              language  — what they have to speak for the test to prove anything;
              voice     — the persona name they chose in the Voice Library. A
                          product name, not a vendor brand.
            Removed: the LLM model id and the STT provider brand. The transcription
            BEHAVIOUR those implied is genuinely useful, so it survives as the line
            below, phrased as what the tester will observe rather than as a vendor. */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'center', margin: '4px 0' }}>
          <Pill color="#3ECF8E">🌐 {language}</Pill>
          <Pill color="#A78BFA">🎙️ {ttsVoice}</Pill>
        </div>

        {/*
          Live transcription is a property of the STT PROVIDER, not of this widget.
          Only providers that stream interim results put words on screen while the
          caller is still talking; the others emit a transcript once the caller
          pauses — so the transcript panel below legitimately stays empty
          mid-sentence, and each turn also waits ~0.8s longer before the agent
          replies (pipecat's measured p99: 1.17s vs 0.35s). Saying so here stops
          that reading as "it isn't hearing me".

          Phrased WITHOUT naming the provider. The old copy said "sarvam transcribes
          only after you pause — switch STT to Deepgram", which both exposed two
          vendor names and told a clinic admin to change a setting they cannot see.
          What is left describes what the tester will observe, which is the part that
          actually helps them interpret the test.
        */}
        <div style={{
          fontSize: 11, color: sttRealtime ? '#3ECF8E' : '#F59E0B',
          maxWidth: 300, lineHeight: 1.5,
        }}>
          {sttRealtime
            ? 'Live transcription: words appear while you speak.'
            : 'Transcription appears after each pause, so replies come a moment later. This language does not support live transcription yet.'}
        </div>

        <button
          onClick={startCall}
          style={{
            marginTop: 12, padding: '13px 36px', borderRadius: 40,
            background: '#3ECF8E', color: '#051b11', fontWeight: 700, fontSize: 15,
            border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 10,
            boxShadow: '0 0 24px rgba(62,207,142,0.4)', transition: 'all 0.2s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'scale(1.04)';
            e.currentTarget.style.boxShadow = '0 0 36px rgba(62,207,142,0.7)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'scale(1)';
            e.currentTarget.style.boxShadow = '0 0 24px rgba(62,207,142,0.4)';
          }}
        >
          <Mic size={20} color="#051b11" /> Start Voice Call
        </button>

        {/* Honest readiness signal — the worker sleeps after 15 min on the free
            plan and takes ~55s to boot. Warming starts when this panel opens, so
            this usually flips to ready before the user reaches for the button. */}
        <div style={{
          fontSize: 11, lineHeight: 1.5, maxWidth: 300,
          color: workerWarm ? '#3ECF8E' : '#F59E0B',
        }}>
          {workerWarm
            ? '● Voice service ready — the call will connect immediately.'
            : '◌ Waking the voice service (up to a minute on the free plan). You can press Start now — it will wait rather than fail.'}
        </div>

        <style>{`
          @keyframes pulseGlow {
            0%, 100% { transform: scale(1); opacity: 0.5; }
            50%       { transform: scale(1.18); opacity: 1; }
          }
        `}</style>
      </>
    );
  }

  if (phase === 'connecting') {
    return shell(
      <>
        <div style={{
          width: 40, height: 40,
          border: '3px solid #1f1f1f', borderTopColor: '#3ECF8E',
          borderRadius: '50%', animation: 'spin 0.8s linear infinite',
        }} />
        <div style={{ fontSize: 14, fontWeight: 600 }}>Starting voice call…</div>
        <div style={{ fontSize: 12, color: '#555' }}>Setting up the voice pipeline</div>
        {slow && (
          <div style={{ fontSize: 12, color: '#F59E0B', maxWidth: 320, lineHeight: 1.5, marginTop: 4 }}>
            Waiting for the voice worker to come up. On the free plan it spins down
            after ~15 min idle and takes up to a minute to boot — the call won't
            start until it's genuinely ready, so this wait replaces what used to be
            a silent room.
          </div>
        )}
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </>
    );
  }

  if (phase === 'error') {
    return shell(
      <>
        <div style={{ fontSize: 32 }}>⚠️</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Could not start the voice call</div>
        <div style={{ fontSize: 12, color: '#777', maxWidth: 300 }}>{error}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button
            onClick={startCall}
            style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: '#3ECF8E', color: '#000', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <RotateCcw size={14} /> Retry
          </button>
          <button
            onClick={handleBackToIdle}
            style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#aaa', cursor: 'pointer' }}
          >
            Back
          </button>
        </div>
      </>
    );
  }

  if (phase === 'demo') {
    return shell(
      <>
        <div style={{ fontSize: 32 }}>🔌</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Voice service not configured</div>
        <div style={{ fontSize: 12, color: '#666', maxWidth: 300 }}>
          Set <code>LIVEKIT_URL</code>, <code>LIVEKIT_API_KEY</code>, <code>LIVEKIT_API_SECRET</code> in <code>.env</code>.
        </div>
        <button onClick={handleBackToIdle} style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#aaa', cursor: 'pointer', marginTop: 12 }}>
          Back
        </button>
      </>
    );
  }

  // ── phase === 'ended' — post-call review ───────────────────────────────────
  // The room is already gone (token/wsUrl cleared), so there is no LiveKit context
  // and no audio here. The transcript survives because it is this component's
  // state. Nothing on this screen closes the panel except the explicit Close
  // button; the parent modal's X still works too.
  if (phase === 'ended') {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, gap: 12, padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#aaa', fontSize: 13, fontWeight: 600 }}>
          <PhoneOff size={14} />
          Call ended
          <span style={{ color: '#555', fontWeight: 500, fontSize: 12 }}>
            · {transcript.length} {transcript.length === 1 ? 'line' : 'lines'} transcribed
          </span>
        </div>

        <TranscriptPanel
          transcript={transcript}
          agentName={agentName}
          title="Transcript"
          dotColor="#555"
          emptyText="No speech was transcribed during this call."
        />

        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button
            onClick={startCall}
            style={{
              padding: '9px 22px', borderRadius: 40, border: '1px solid #3ECF8E',
              background: 'rgba(62,207,142,0.08)', color: '#3ECF8E',
              fontWeight: 600, fontSize: 13, cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <RotateCcw size={14} /> Call again
          </button>
          <button
            onClick={handleClose}
            style={{
              padding: '9px 22px', borderRadius: 40, border: '1px solid #2e2e2e',
              background: 'none', color: '#aaa', fontWeight: 600, fontSize: 13, cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>
      </div>
    );
  }

  // phase === 'live'
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <LiveKitRoom
        token={token}
        serverUrl={wsUrl}
        connect={true}
        audio={micAvailable}
        video={false}
        onDisconnected={handleCallEnded}
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
      >
        {/*
          RoomAudioRenderer: subscribes to & auto-plays ALL remote audio tracks.
          This is what actually produces the agent's audio output. MUST be rendered.
          StartAudio handles Chrome/Safari/iOS autoplay policy.
        */}
        <RoomAudioRenderer />
        <TestCallUI
          agentName={agentName}
          avatarUrl={avatarUrl}
          onDisconnect={handleCallEnded}
          onRetry={() => { handleCallEnded(); setTimeout(startCall, 300); }}
          transcript={transcript}
          setTranscript={setTranscript}
        />
      </LiveKitRoom>
    </div>
  );
}

// ── Small helper ──────────────────────────────────────────────────────────────
function Pill({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span style={{
      fontSize: 11, padding: '3px 10px', borderRadius: 12,
      background: `${color}18`, border: `1px solid ${color}44`,
      color, fontWeight: 600,
    }}>
      {children}
    </span>
  );
}
