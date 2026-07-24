import { Headphones, Mic, Phone, PhoneOff, RotateCcw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import {
  LiveKitRoom,
  useVoiceAssistant,
  BarVisualizer,
  RoomAudioRenderer,
  VoiceAssistantControlBar,
} from '@livekit/components-react';
import '@livekit/components-styles';
import fetchWithAuth from '../api/client';

const SLOW_MS = 12_000;
const TIMEOUT_MS = 45_000;
const AGENT_WAIT_MS = 60_000;

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
  const [attempt, setAttempt] = useState(0);

  const startCall = async () => {
    setPhase('connecting');
    setError('');
    setSlow(false);

    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);
    const hardTimer = setTimeout(() => {
      setError('Connection timed out. The voice service may be starting up (cold start). Please try again.');
      setPhase('error');
    }, TIMEOUT_MS);

    try {
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
  };

  const shell = (children: React.ReactNode) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '14px', padding: '24px', textAlign: 'center', color: '#fff' }}>
      {children}
    </div>
  );

  // ── PRE-CALL LANDING STATE (Idle — requires user to click Start Call) ─────
  if (phase === 'idle') {
    const language = agent?.stt_language || agent?.tts_language || agent?.language || 'en-IN';
    const llmModel = (agent?.llm_model || 'gemini-2.5-flash').replace('gemini-', 'g-').replace('-versatile', '');
    const ttsVoice = agent?.tts_voice || 'priya';

    return shell(
      <>
        {/* Glow badge avatar */}
        <div style={{ position: 'relative', margin: '12px 0' }}>
          <div style={{
            position: 'absolute', inset: -6, borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(62,207,142,0.4) 0%, rgba(62,207,142,0) 70%)',
            animation: 'pulseGlow 2.5s infinite ease-in-out'
          }} />
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt={agentName || 'Agent'}
              style={{
                position: 'relative', width: 72, height: 72, borderRadius: '50%',
                objectFit: 'cover', border: '3px solid #3ECF8E',
                boxShadow: '0 0 20px rgba(62,207,142,0.3)',
              }}
              onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
            />
          ) : (
            <div style={{
              position: 'relative', width: 72, height: 72, borderRadius: '50%',
              background: 'rgba(62,207,142,0.12)', border: '3px solid #3ECF8E',
              boxShadow: '0 0 20px rgba(62,207,142,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Headphones size={36} color="#3ECF8E" />
            </div>
          )}
        </div>

        {/* Title */}
        <div style={{ fontSize: 18, fontWeight: 700, color: '#ffffff' }}>
          {agentName || 'AI Receptionist'}
        </div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 300, lineHeight: 1.5 }}>
          Real-time AI voice testing via Pipecat + LiveKit pipeline
        </div>

        {/* Config Badges */}
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

        {/* Green Start Call Button */}
        <button
          onClick={startCall}
          style={{
            marginTop: 12,
            padding: '14px 32px',
            borderRadius: 40,
            background: '#3ECF8E',
            color: '#051b11',
            fontWeight: 700,
            fontSize: 15,
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            boxShadow: '0 0 24px rgba(62,207,142,0.4)',
            transition: 'all 0.2s ease',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.transform = 'scale(1.04)';
            e.currentTarget.style.boxShadow = '0 0 32px rgba(62,207,142,0.6)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.transform = 'scale(1)';
            e.currentTarget.style.boxShadow = '0 0 24px rgba(62,207,142,0.4)';
          }}
        >
          <Mic size={20} color="#051b11" /> Start Voice Call
        </button>

        <style>{`
          @keyframes pulseGlow {
            0%, 100% { transform: scale(1); opacity: 0.5; }
            50% { transform: scale(1.15); opacity: 0.9; }
          }
        `}</style>
      </>
    );
  }

  if (phase === 'connecting') {
    return shell(
      <>
        <div style={{ width: 36, height: 36, border: '3px solid #1f1f1f', borderTopColor: '#3ECF8E', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        <div style={{ fontSize: 15, fontWeight: 600 }}>Connecting to {agentName || 'agent'}…</div>
        <div style={{ fontSize: 12, color: '#666' }}>Setting up LiveKit WebRTC pipeline</div>
        {slow && (
          <div style={{ fontSize: 12, color: '#F59E0B', maxWidth: 320, lineHeight: 1.5, marginTop: 4 }}>
            Starting worker instance (Render cold start can take ~20-30s). Please wait…
          </div>
        )}
      </>
    );
  }

  if (phase === 'error') {
    return shell(
      <>
        <div style={{ fontSize: 28 }}>⚠️</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Couldn’t start the test call</div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 300 }}>{error}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button onClick={startCall} style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: '#3ECF8E', color: '#000', fontWeight: 600, cursor: 'pointer' }}>
            <RotateCcw size={14} style={{ display: 'inline', marginRight: 6 }} /> Retry
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
        <div style={{ fontSize: 28 }}>🔌</div>
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
        <TestCallUI
          agentName={agentName}
          avatarUrl={avatarUrl}
          micAvailable={micAvailable}
          onDisconnect={handleDisconnect}
          onRetry={() => { handleDisconnect(); startCall(); }}
        />
        <RoomAudioRenderer />
      </LiveKitRoom>
    </div>
  );
}

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

  const { state, audioTrack, agentTranscriptions, agent } = useVoiceAssistant();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Readiness MUST come from the agent PARTICIPANT + its audio track — NOT from
  // `state`. This worker runs a raw pipecat PipelineTask, which never emits the
  // `lk.agent.state` participant attribute the SDK needs to advance `state` past
  // "connecting". Gating on `state` (the earlier rewrite did) left a warm, actively
  // speaking agent stuck reading "connecting"/"waiting" forever.
  const agentReady = !!agent && !!audioTrack;

  const liveStates: Record<string, { label: string; color: string }> = {
    listening: { label: '🎤 Listening', color: '#3B82F6' },
    thinking: { label: '💭 Thinking', color: '#F59E0B' },
    speaking: { label: '🔊 Speaking', color: '#3ECF8E' },
    idle: { label: '● Ready — speak', color: '#3ECF8E' },
  };
  const { label, color } = agentReady
    ? (liveStates[state] || { label: '● Live — speak', color: '#3ECF8E' })
    : { label: 'Connecting to the agent…', color: '#F59E0B' };

  const [waitedLong, setWaitedLong] = useState(false);
  useEffect(() => {
    if (agentReady) { setWaitedLong(false); return; }
    const t = setTimeout(() => setWaitedLong(true), AGENT_WAIT_MS);
    return () => clearTimeout(t);
  }, [agentReady]);

  // Auto-scroll transcript to the newest line.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [agentTranscriptions]);

  const hasTranscript = agentTranscriptions && agentTranscriptions.length > 0;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: '16px', gap: 12 }}>
      {/* Agent avatar + visualizer (compact so transcript gets the room) */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        {/* Agent avatar / identity badge */}
        {avatarUrl ? (
          <img
            src={avatarUrl}
            alt={agentName || 'Agent'}
            style={{
              width: 48, height: 48, borderRadius: '50%',
              objectFit: 'cover',
              border: `2px solid ${color}`,
              boxShadow: `0 0 12px ${color}44`,
              transition: 'border-color 0.3s, box-shadow 0.3s',
            }}
            onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <div style={{
            width: 48, height: 48, borderRadius: '50%',
            background: `${color}18`,
            border: `2px solid ${color}`,
            boxShadow: `0 0 12px ${color}44`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 22,
            transition: 'border-color 0.3s, box-shadow 0.3s',
          }}>
            🎧
          </div>
        )}
        <div style={{ width: '100%', maxWidth: 320 }}>
          <BarVisualizer
            state={state}
            trackRef={audioTrack}
            barCount={28}
            options={{ minHeight: 4 }}
            style={{ '--lk-fg': color, height: '56px', width: '100%' } as React.CSSProperties}
          />
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, color }}>{label}</div>
      </div>

      {/* Waiting-for-agent notice (worker cold start) */}
      {!agentReady && waitedLong && (
        <div style={{ fontSize: 12, color: '#F59E0B', textAlign: 'center', maxWidth: 340, alignSelf: 'center', lineHeight: 1.5 }}>
          Waiting for the voice service to respond — it may be starting up (cold start).
          {onRetry && <> <button onClick={onRetry} style={{ background: 'none', border: 'none', color: '#3ECF8E', textDecoration: 'underline', cursor: 'pointer', font: 'inherit', padding: 0 }}>Retry</button></>}
        </div>
      )}

      {/* Live transcript */}
      <div
        ref={scrollRef}
        style={{
          flex: 1, minHeight: 0, overflowY: 'auto',
          display: 'flex', flexDirection: 'column', gap: 8,
          padding: 12, borderRadius: 10,
          background: 'rgba(0,0,0,0.25)', border: '1px solid #1f1f1f',
        }}
      >
        {hasTranscript ? (
          agentTranscriptions.map(seg => (
            <div key={seg.id} style={{ display: 'flex', justifyContent: 'flex-start' }}>
              <div style={{ maxWidth: '85%', padding: '6px 12px', borderRadius: 12, borderTopLeftRadius: 4, fontSize: 13, lineHeight: 1.5, background: 'rgba(99,102,241,0.22)', color: '#c7d2fe' }}>
                <div style={{ fontSize: 10, opacity: 0.6, marginBottom: 2, fontWeight: 600 }}>🤖 {agentName || 'Agent'}</div>
                {seg.text}
              </div>
            </div>
          ))
        ) : (
          <div style={{ margin: 'auto', textAlign: 'center', color: '#555', fontSize: 12, lineHeight: 1.6 }}>
            {agentReady
              ? (micAvailable === false
                  ? 'Listening for the agent… (transcript appears as it speaks)'
                  : 'Say hello — the transcript appears here as you and the agent speak.')
              : 'Waiting for the agent to join the call…'}
          </div>
        )}
      </div>

      {/* Mode note */}
      {micAvailable === false ? (
        <div style={{ fontSize: 11, color: '#F59E0B', textAlign: 'center', lineHeight: 1.5 }}>
          🔇 No microphone — <strong>listen-only</strong>. You'll hear and read {agentName || 'the agent'}, but can't speak back.
        </div>
      ) : (
        <div style={{ fontSize: 11, color: '#666', textAlign: 'center' }}>
          Speak naturally — you can interrupt {agentName || 'the agent'} mid-sentence.
        </div>
      )}

      {/* Controls (wrap on narrow screens) */}
      <div className="lk-test-controls" style={{ display: 'flex', justifyContent: 'center', flexWrap: 'wrap' }}>
        <VoiceAssistantControlBar controls={{ leave: true, microphone: micAvailable !== false }} saveUserChoices={false} />
      </div>
    </div>
  );
}
