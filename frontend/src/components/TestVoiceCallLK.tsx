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
const TIMEOUT_MS = 60_000;
const AGENT_WAIT_MS = 45_000;
const TRANSCRIPT_TOPIC = 'lifodial-transcript';

interface TranscriptEntry {
  id: string;
  role: 'agent' | 'user';
  text: string;
  final: boolean;
  ts: number;
}

// ─── Inner component — must be inside <LiveKitRoom> ─────────────────────────

function TestCallUI({
  agentName,
  avatarUrl,
  onDisconnect,
  onRetry,
}: {
  agentName?: string;
  avatarUrl?: string;
  onDisconnect: () => void;
  onRetry?: () => void;
}) {
  const room = useRoomContext();
  const connState = useConnectionState();
  const remoteParticipants = useRemoteParticipants();
  const transcriptRef = useRef<HTMLDivElement>(null);

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
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const pendingAgentRef = useRef<Map<string, string>>(new Map());

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
    const handler = (payload: Uint8Array, participant?: Participant, _reliable?: boolean, _topic?: string) => {
      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text);
        if (data.role === 'user' && data.text?.trim()) {
          const entry: TranscriptEntry = {
            id: `user-${Date.now()}-${Math.random()}`,
            role: 'user',
            text: data.text.trim(),
            final: true,
            ts: Date.now(),
          };
          setTranscript((prev) => [...prev, entry]);
        }
      } catch {
        // ignore malformed JSON
      }
    };

    room.on(RoomEvent.DataReceived, handler);
    return () => { room.off(RoomEvent.DataReceived, handler); };
  }, [room]);

  // Auto-scroll transcript to bottom
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [transcript]);

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
            }}
          />
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

      {/* Live transcript panel */}
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
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: agentReady ? '#3ECF8E' : '#F59E0B' }} />
          Live Transcript
        </div>

        <div
          ref={transcriptRef}
          style={{
            flex: 1, overflowY: 'auto', padding: '10px 12px',
            display: 'flex', flexDirection: 'column', gap: 8,
          }}
        >
          {transcript.length === 0 ? (
            <div style={{ fontSize: 12, color: '#333', textAlign: 'center', marginTop: 16 }}>
              {agentReady
                ? 'Speak naturally — transcript will appear here'
                : 'Waiting for the agent to join the call…'
              }
            </div>
          ) : (
            transcript.map((entry) => (
              <TranscriptBubble key={entry.id} entry={entry} agentName={agentName} />
            ))
          )}
        </div>
      </div>

      {/* Debug info (dev) */}
      {process.env.NODE_ENV !== 'production' && remoteParticipants.length > 0 && (
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
  const [phase, setPhase] = useState<'idle' | 'connecting' | 'live' | 'error' | 'demo'>('idle');
  const [error, setError] = useState('');
  const [micAvailable, setMicAvailable] = useState(true);
  const [slow, setSlow] = useState(false);

  const startCall = useCallback(async () => {
    setPhase('connecting');
    setError('');
    setSlow(false);

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

  const handleDisconnect = useCallback(() => {
    setToken('');
    setWsUrl('');
    setPhase('idle');
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
    const language = agent?.stt_language || agent?.tts_language || agent?.language || 'en-IN';
    const sttProvider = agent?.stt_provider || 'sarvam';
    const llmModel = (agent?.llm_model || 'llama-3.3-70b').replace('-versatile', '').replace('llama-3.3-70b', 'Llama-3.3');
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
        <div style={{ fontSize: 12, color: '#666', maxWidth: 280, lineHeight: 1.5 }}>
          Real-time AI voice — Pipecat + LiveKit + {sttProvider === 'deepgram' ? 'Deepgram Nova-3' : sttProvider === 'sarvam' ? 'Sarvam STT' : sttProvider}
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'center', margin: '4px 0' }}>
          <Pill color="#3ECF8E">🌐 {language}</Pill>
          <Pill color="#60A5FA">⚡ {llmModel}</Pill>
          <Pill color="#A78BFA">🎙️ {ttsVoice}</Pill>
          <Pill color={sttProvider === 'deepgram' ? '#F59E0B' : '#888'}>
            🎤 {sttProvider === 'deepgram' ? 'Deepgram' : sttProvider}
          </Pill>
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
        <div style={{ fontSize: 12, color: '#555' }}>Setting up Pipecat + LiveKit pipeline</div>
        {slow && (
          <div style={{ fontSize: 12, color: '#F59E0B', maxWidth: 300, lineHeight: 1.5, marginTop: 4 }}>
            Worker may be cold-starting (~20–30s on Render free tier). Please wait…
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
            onClick={handleDisconnect}
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
        <div style={{ fontSize: 14, fontWeight: 600 }}>LiveKit not configured</div>
        <div style={{ fontSize: 12, color: '#666', maxWidth: 300 }}>
          Set <code>LIVEKIT_URL</code>, <code>LIVEKIT_API_KEY</code>, <code>LIVEKIT_API_SECRET</code> in <code>.env</code>.
        </div>
        <button onClick={handleDisconnect} style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#aaa', cursor: 'pointer', marginTop: 12 }}>
          Back
        </button>
      </>
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
        onDisconnected={handleDisconnect}
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
          onDisconnect={handleDisconnect}
          onRetry={() => { handleDisconnect(); setTimeout(startCall, 300); }}
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
