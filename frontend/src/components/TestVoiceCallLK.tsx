/**
 * TestVoiceCallLK.tsx — LiveKit web-call test widget
 *
 * Architecture:
 *  - LiveKitRoom handles WebRTC connection
 *  - RoomAudioRenderer auto-plays ALL remote audio (handles browser autoplay policy)
 *  - StartAudio button unlocks browser audio context on first user gesture
 *  - useTracks([Track.Source.Microphone]) finds the agent's audio track for the visualizer
 *  - Agent identified by identity starting with 'lifodial-agent' (set in pipeline.py)
 *
 * Why this works: Pipecat's LiveKitTransport joins as a standard participant
 * (identity='lifodial-agent-XXXXXX') and publishes a microphone-source audio track.
 * RoomAudioRenderer subscribes & plays all remote audio automatically. The
 * useVoiceAssistant() hook is intentionally NOT used — it requires ParticipantKind.AGENT
 * which only the ctx.connect() ghost participant gets (publishes no tracks).
 */
import { Headphones, Mic, RotateCcw, PhoneOff } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
import { Track, ConnectionState, RoomEvent } from 'livekit-client';
import '@livekit/components-styles';
import fetchWithAuth from '../api/client';

const SLOW_MS = 12_000;
const TIMEOUT_MS = 60_000;
const AGENT_WAIT_MS = 45_000;

/** Inner component — must live inside <LiveKitRoom> so hooks work */
function TestCallUI({
  agentName,
  avatarUrl,
  micAvailable,
  onDisconnect,
  onRetry,
}: {
  agentName?: string;
  avatarUrl?: string;
  micAvailable?: boolean;
  onDisconnect: () => void;
  onRetry?: () => void;
}) {
  const room = useRoomContext();
  const connState = useConnectionState();
  const remoteParticipants = useRemoteParticipants();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [audioUnlocked, setAudioUnlocked] = useState(false);

  // Unlock audio context on first user interaction (browser autoplay policy)
  useEffect(() => {
    if (connState === ConnectionState.Connected && room) {
      room.startAudio()
        .then(() => setAudioUnlocked(true))
        .catch(() => {}); // Will be unlocked by StartAudio button click
    }
  }, [connState, room]);

  // useTracks with ALL sources — subscribes to every published track in the room.
  // updateOnlyOn: default (undefined) — re-render on all relevant RoomEvents.
  // This is the definitive fix for the "visualizer never updates" bug caused by
  // updateOnlyOn:[] which disabled all re-renders.
  const allTracks = useTracks(
    [Track.Source.Microphone, Track.Source.ScreenShareAudio, Track.Source.Unknown],
    {
      onlySubscribed: false,
      updateOnlyOn: [
        RoomEvent.TrackSubscribed,
        RoomEvent.TrackUnsubscribed,
        RoomEvent.ParticipantConnected,
        RoomEvent.ParticipantDisconnected,
        RoomEvent.LocalTrackPublished,
        RoomEvent.LocalTrackUnpublished,
        RoomEvent.TrackPublished,
        RoomEvent.TrackUnpublished,
      ],
    }
  );

  // Find the agent's audio track — the Pipecat transport publishes as 'lifodial-agent-*'
  const agentTrackRef = allTracks.find(
    (t) =>
      t.participant &&
      t.participant.identity !== room?.localParticipant?.identity &&
      t.participant.identity.startsWith('lifodial-agent')
  ) ?? null;

  // Fallback: any remote audio track if agent identity not matched yet
  const anyRemoteAudioTrack = allTracks.find(
    (t) =>
      t.participant &&
      t.participant.identity !== room?.localParticipant?.identity
  ) ?? null;

  const activeTrackRef = agentTrackRef ?? anyRemoteAudioTrack;

  // Agent participant: prefer the one owning the audio track
  const agentParticipant =
    activeTrackRef?.participant ??
    remoteParticipants.find((p) => p.identity.startsWith('lifodial-agent')) ??
    remoteParticipants[0] ??
    null;

  // Agent state from LiveKit participant attributes (set by livekit-agents framework)
  const [agentState, setAgentState] = useState<string>('connecting');

  useEffect(() => {
    if (!agentParticipant) {
      setAgentState('connecting');
      return;
    }
    const attrs = (agentParticipant as any).attributes || {};
    setAgentState(attrs['lk.agent.state'] || 'idle');

    const handler = () => {
      const updated = (agentParticipant as any).attributes || {};
      setAgentState(updated['lk.agent.state'] || 'idle');
    };
    agentParticipant.on('attributesChanged', handler);
    return () => { agentParticipant.off('attributesChanged', handler); };
  }, [agentParticipant]);

  // "Agent ready" = we're connected AND at least one remote participant exists
  const agentReady = connState === ConnectionState.Connected && (
    !!agentParticipant || remoteParticipants.length > 0
  );

  const liveStates: Record<string, { label: string; color: string }> = {
    listening: { label: '🎤 Listening…', color: '#3B82F6' },
    thinking: { label: '💭 Thinking…', color: '#F59E0B' },
    speaking: { label: '🔊 Speaking', color: '#3ECF8E' },
    idle: { label: '● Ready — speak now', color: '#3ECF8E' },
  };
  const { label, color } = agentReady
    ? (liveStates[agentState] || { label: '● Live', color: '#3ECF8E' })
    : { label: 'Connecting to the agent…', color: '#F59E0B' };

  const [waitedLong, setWaitedLong] = useState(false);
  useEffect(() => {
    if (agentReady) { setWaitedLong(false); return; }
    const t = setTimeout(() => setWaitedLong(true), AGENT_WAIT_MS);
    return () => clearTimeout(t);
  }, [agentReady]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: '16px', gap: 12 }}>

      {/* Autoplay unblock — only shown if browser blocked audio context */}
      {!audioUnlocked && (
        <div style={{ alignSelf: 'center' }}>
          <StartAudio
            label="🔊 Click to Enable Agent Audio"
            style={{
              background: '#3ECF8E', color: '#051b11', padding: '8px 20px',
              borderRadius: 20, fontWeight: 700, border: 'none', cursor: 'pointer',
              fontSize: 13,
            }}
          />
        </div>
      )}

      {/* Agent avatar + BarVisualizer */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        {avatarUrl ? (
          <img
            src={avatarUrl}
            alt={agentName || 'Agent'}
            style={{
              width: 56, height: 56, borderRadius: '50%',
              objectFit: 'cover',
              border: `2px solid ${color}`,
              boxShadow: `0 0 16px ${color}55`,
              transition: 'border-color 0.3s, box-shadow 0.3s',
            }}
            onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <div style={{
            width: 56, height: 56, borderRadius: '50%',
            background: `${color}18`,
            border: `2px solid ${color}`,
            boxShadow: `0 0 16px ${color}55`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 26,
            transition: 'border-color 0.3s, box-shadow 0.3s',
          }}>
            🎧
          </div>
        )}

        {/* Audio visualizer — shown once agent track is found */}
        {agentReady && activeTrackRef ? (
          <div style={{ width: '100%', maxWidth: 320 }}>
            <BarVisualizer
              trackRef={activeTrackRef}
              barCount={32}
              options={{ minHeight: 4 }}
              style={{ '--lk-fg': color, height: '60px', width: '100%' } as React.CSSProperties}
            />
          </div>
        ) : (
          <div style={{ height: '60px', maxWidth: 320, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ width: 36, height: 36, border: '3px solid #1f1f1f', borderTopColor: color, borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
          </div>
        )}

        <div style={{ fontSize: 13, fontWeight: 600, color }}>{label}</div>
      </div>

      {/* Long-wait hint */}
      {!agentReady && waitedLong && (
        <div style={{ fontSize: 12, color: '#F59E0B', textAlign: 'center', maxWidth: 340, alignSelf: 'center', lineHeight: 1.5 }}>
          Waiting for the voice service — it may be cold-starting (Render free tier ~30s).{' '}
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

      {/* Debug info in dev */}
      <div style={{ fontSize: 10, color: '#444', textAlign: 'center', lineHeight: 1.4 }}>
        {remoteParticipants.length > 0 && (
          <>
            {remoteParticipants.map(p => (
              <span key={p.identity} style={{ marginRight: 6 }}>
                👤 {p.identity} ({allTracks.filter(t => t.participant?.identity === p.identity).length} tracks)
              </span>
            ))}
            <br />
            {activeTrackRef
              ? `🎵 Audio track: ${activeTrackRef.participant?.identity} [${activeTrackRef.publication?.kind}]`
              : '⚠️ No audio track found yet'}
          </>
        )}
        {remoteParticipants.length === 0 && connState === ConnectionState.Connected && (
          <span>⏳ Waiting for agent to join room…</span>
        )}
      </div>

      {/* Disconnect button */}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <button
          onClick={onDisconnect}
          style={{
            padding: '10px 28px', borderRadius: 40, border: '1px solid #ef4444',
            background: 'rgba(239,68,68,0.1)', color: '#ef4444',
            fontWeight: 600, fontSize: 14, cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 8,
            transition: 'all 0.2s ease',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.25)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.1)'; }}
        >
          <PhoneOff size={16} />
          End Call
        </button>
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

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

  const startCall = async () => {
    setPhase('connecting');
    setError('');
    setSlow(false);

    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);
    const hardTimer = setTimeout(() => {
      setError('Connection timed out. The voice service may be cold-starting (~30s on Render free tier). Please try again in a moment.');
      setPhase('error');
    }, TIMEOUT_MS);

    try {
      // Check mic availability (non-fatal — can still hear agent)
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(t => t.stop());
        setMicAvailable(true);
      } catch (micErr: any) {
        console.warn('Mic unavailable — connecting listen-only:', micErr?.name || micErr);
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
  };

  const handleDisconnect = () => {
    setToken('');
    setWsUrl('');
    setPhase('idle');
    onClose?.();
  };

  const shell = (children: React.ReactNode) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '14px', padding: '24px', textAlign: 'center', color: '#fff' }}>
      {children}
    </div>
  );

  if (phase === 'idle') {
    const language = agent?.stt_language || agent?.tts_language || agent?.language || 'en-IN';
    const llmModel = (agent?.llm_model || 'llama-3.3-70b-versatile').replace('-versatile', '').replace('llama-3.3-70b', 'Llama-3.3');
    const ttsVoice = agent?.tts_voice || 'priya';

    return shell(
      <>
        <div style={{ position: 'relative', margin: '12px 0' }}>
          <div style={{
            position: 'absolute', inset: -8, borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(62,207,142,0.4) 0%, rgba(62,207,142,0) 70%)',
            animation: 'pulseGlow 2.5s infinite ease-in-out'
          }} />
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt={agentName || 'Agent'}
              style={{
                position: 'relative', width: 80, height: 80, borderRadius: '50%',
                objectFit: 'cover', border: '3px solid #3ECF8E',
                boxShadow: '0 0 24px rgba(62,207,142,0.4)',
              }}
              onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
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

        <div style={{ fontSize: 18, fontWeight: 700, color: '#ffffff' }}>
          {agentName || 'AI Receptionist'}
        </div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 300, lineHeight: 1.5 }}>
          Real-time AI voice — powered by Pipecat + LiveKit
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'center', margin: '6px 0' }}>
          <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 12, background: 'rgba(62,207,142,0.1)', border: '1px solid rgba(62,207,142,0.3)', color: '#3ECF8E', fontWeight: 600 }}>
            🌐 {language}
          </span>
          <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 12, background: 'rgba(96,165,250,0.1)', border: '1px solid rgba(96,165,250,0.3)', color: '#60A5FA', fontWeight: 600 }}>
            ⚡ {llmModel}
          </span>
          <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 12, background: 'rgba(167,139,250,0.1)', border: '1px solid rgba(167,139,250,0.3)', color: '#A78BFA', fontWeight: 600 }}>
            🎙️ {ttsVoice}
          </span>
        </div>

        <button
          onClick={startCall}
          style={{
            marginTop: 12, padding: '14px 36px', borderRadius: 40,
            background: '#3ECF8E', color: '#051b11', fontWeight: 700, fontSize: 15,
            border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 10,
            boxShadow: '0 0 24px rgba(62,207,142,0.4)', transition: 'all 0.2s ease',
          }}
          onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.04)'; e.currentTarget.style.boxShadow = '0 0 36px rgba(62,207,142,0.7)'; }}
          onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; e.currentTarget.style.boxShadow = '0 0 24px rgba(62,207,142,0.4)'; }}
        >
          <Mic size={20} color="#051b11" /> Start Voice Call
        </button>

        <style>{`
          @keyframes pulseGlow { 0%, 100% { transform: scale(1); opacity: 0.5; } 50% { transform: scale(1.18); opacity: 1; } }
        `}</style>
      </>
    );
  }

  if (phase === 'connecting') {
    return shell(
      <>
        <div style={{ width: 40, height: 40, border: '3px solid #1f1f1f', borderTopColor: '#3ECF8E', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        <div style={{ fontSize: 15, fontWeight: 600 }}>Connecting to {agentName || 'agent'}…</div>
        <div style={{ fontSize: 12, color: '#666' }}>Setting up LiveKit WebRTC pipeline</div>
        {slow && (
          <div style={{ fontSize: 12, color: '#F59E0B', maxWidth: 320, lineHeight: 1.5, marginTop: 4 }}>
            Worker may be cold-starting (Render free tier takes ~20-30s). Please wait…
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
        <div style={{ fontSize: 14, fontWeight: 600 }}>Couldn't start the test call</div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 300 }}>{error}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button onClick={startCall} style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: '#3ECF8E', color: '#000', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}>
            <RotateCcw size={14} /> Retry
          </button>
          <button onClick={handleDisconnect} style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#fff', cursor: 'pointer' }}>
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
        <div style={{ fontSize: 12, color: '#888', maxWidth: 320 }}>
          Set <code>LIVEKIT_URL</code>, <code>LIVEKIT_API_KEY</code>, and <code>LIVEKIT_API_SECRET</code> in <code>.env</code>.
        </div>
        <button onClick={handleDisconnect} style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#fff', cursor: 'pointer', marginTop: 12 }}>
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
          RoomAudioRenderer MUST be rendered — it subscribes to and auto-plays ALL
          remote audio tracks (incl. the agent's speech). Without it, audio is
          subscribed but no <audio> element is created → silence.
          StartAudio handles Chrome/Safari autoplay policy (requires user gesture).
        */}
        <RoomAudioRenderer />
        <TestCallUI
          agentName={agentName}
          avatarUrl={avatarUrl}
          micAvailable={micAvailable}
          onDisconnect={handleDisconnect}
          onRetry={() => { handleDisconnect(); setTimeout(startCall, 200); }}
        />
      </LiveKitRoom>
    </div>
  );
}
