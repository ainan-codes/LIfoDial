import { useEffect, useRef, useState } from 'react';
import { API_URL } from '../api/client';
import { useStreamingSTT } from '../hooks/useStreamingSTT';
import { useThinkingSound } from '../hooks/useThinkingSound';

interface WebCallInterfaceProps {
  agent: {
    id: string;
    name: string;
    clinic_name: string;
    tts_language: string;
    tts_voice: string;
  };
  onClose: () => void;
}

interface TranscriptEntry {
  role: 'ai' | 'user';
  text: string;
  time: string;
}

async function getCallToken(agentId: string): Promise<{
  token: string;
  roomName: string;
  wsUrl: string;
  callId: string;
  demo?: boolean;
  message?: string;
}> {
  const res = await fetch(`${API_URL}/agents/${agentId}/web-call-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error('Failed to get call token');
  return res.json();
}

const LANG_LABELS: Record<string, string> = {
  'hi-IN': '🇮🇳 Hindi',
  'ml-IN': '🇮🇳 Malayalam',
  'ar-SA': '🇦🇪 Arabic',
  'en-IN': '🇮🇳 English',
  'ta-IN': '🇮🇳 Tamil',
  'bn-IN': '🇮🇳 Bengali',
};

export function WebCallInterface({ agent, onClose }: WebCallInterfaceProps) {
  const [callState, setCallState] = useState<'connecting' | 'connected' | 'demo' | 'error'>('connecting');
  const [error, setError] = useState('');
  const [callDuration, setCallDuration] = useState(0);
  const [agentState, setAgentState] = useState<'connecting' | 'listening' | 'thinking' | 'speaking'>('connecting');
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [isMuted, setIsMuted] = useState(false);
  const [callId, setCallId] = useState('');
  const [roomName, setRoomName] = useState('');
  const [tenantId] = useState('default-tenant'); // TODO: get from context
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number | undefined>(undefined);
  const streamRef = useRef<MediaStream | null>(null);

  // Thinking sound — plays soft ping while STT→LLM→TTS pipeline runs
  const { startThinking, stopThinking } = useThinkingSound('ping');

  // Streaming STT hook
  const {
    isConnected: sttConnected,
    isListening,
    sendAudio,
    lastTranscript,
  } = useStreamingSTT({
    tenantId,
    agentId: agent.id,
    languageCode: agent.tts_language || 'en-IN',
    mode: 'transcribe',
    onTranscript: (text, confidence) => {
      if (text && text.trim().length > 0) {
        setTranscript((t) => {
          // Check if we should append or create new entry
          const last = t[t.length - 1];
          if (last && last.role === 'user') {
            // Update last user message
            return [...t.slice(0, -1), { ...last, text }];
          } else {
            // Create new user message
            return [...t, { role: 'user', text, time: new Date().toLocaleTimeString() }];
          }
        });
      }
    },
    onSpeechStart: () => {
      setAgentState('listening');
    },
    onSpeechEnd: () => {
      setAgentState('thinking');
      startThinking();  // ← user stopped talking, pipeline starts
      setTimeout(() => {
        setAgentState('speaking');
        stopThinking();  // ← agent about to speak, stop thinking sound
      }, 500);
    },
  });

  // Connect to call
  useEffect(() => {
    getCallToken(agent.id)
      .then((data) => {
        setCallId(data.callId);
        setRoomName(data.roomName);

        if (data.demo) {
          setCallState('demo');
          setAgentState('listening');
          // Start demo mode — show UI without real LiveKit
          startDemoMode();
        } else if (data.token) {
          // LiveKit is configured — connect for real
          setCallState('connected');
          setAgentState('listening');
        } else {
          setCallState('demo');
          setAgentState('listening');
          startDemoMode();
        }
      })
      .catch((e) => {
        setError(e.message);
        setCallState('error');
      });
  }, [agent.id]);

  // Timer
  useEffect(() => {
    if (callState === 'connected' || callState === 'demo') {
      timerRef.current = setInterval(() => {
        setCallDuration((d) => d + 1);
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [callState]);

  // Audio visualizer
  useEffect(() => {
    if (callState !== 'connected' && callState !== 'demo') return;

    let active = true;

    async function setupAudio() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        const ctx = new AudioContext();
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 128;
        source.connect(analyser);
        analyserRef.current = analyser;

        function draw() {
          if (!active || !canvasRef.current || !analyserRef.current) return;
          const canvas = canvasRef.current;
          const canvasCtx = canvas.getContext('2d');
          if (!canvasCtx) return;

          const bufferLength = analyserRef.current.frequencyBinCount;
          const dataArray = new Uint8Array(bufferLength);
          analyserRef.current.getByteFrequencyData(dataArray);

          canvasCtx.clearRect(0, 0, canvas.width, canvas.height);

          const barWidth = (canvas.width / bufferLength) * 1.5;
          const centerY = canvas.height / 2;

          for (let i = 0; i < bufferLength; i++) {
            const barHeight = (dataArray[i] / 255) * centerY * 0.8;
            const x = i * (barWidth + 2);
            const hue = 155; // greenish
            const saturation = 70;
            const lightness = 50 + (dataArray[i] / 255) * 20;

            canvasCtx.fillStyle = `hsl(${hue}, ${saturation}%, ${lightness}%)`;
            canvasCtx.fillRect(x, centerY - barHeight, barWidth, barHeight);
            canvasCtx.fillRect(x, centerY, barWidth, barHeight);
          }

          animFrameRef.current = requestAnimationFrame(draw);
        }
        draw();
      } catch (e) {
        console.error('Microphone access denied:', e);
      }
    }

    setupAudio();

    return () => {
      active = false;
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, [callState]);

  function startDemoMode() {
    // Simulate agent greeting after 2 seconds
    setTimeout(() => {
      setAgentState('speaking');
      setTranscript((t) => [
        ...t,
        {
          role: 'ai',
          text: agent.name + ' connected. LiveKit keys not configured — this is a demo preview of the web call UI.',
          time: new Date().toLocaleTimeString(),
        },
      ]);
      setTimeout(() => setAgentState('listening'), 3000);
    }, 2000);
  }

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60).toString().padStart(2, '0');
    const s = (secs % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  const getStateLabel = () => {
    switch (agentState) {
      case 'connecting': return 'Connecting...';
      case 'listening': return 'Listening';
      case 'thinking': return 'Processing';
      case 'speaking': return 'Speaking';
      default: return '● Ready';
    }
  };

  const getStateColor = () => {
    switch (agentState) {
      case 'speaking': return '#3ECF8E';
      case 'listening': return '#3B82F6';
      case 'thinking': return '#F59E0B';
      default: return '#666';
    }
  };

  const handleEndCall = () => {
    stopThinking(); // ← clean up thinking sound on hang-up
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
    }
    onClose();
  };

  const toggleMute = () => {
    if (streamRef.current) {
      streamRef.current.getAudioTracks().forEach((t) => {
        t.enabled = !t.enabled;
      });
      setIsMuted(!isMuted);
    }
  };

  if (callState === 'error') {
    return (
      <div style={overlayStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: 'center', padding: '40px 20px' }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>⚠️</div>
            <p style={{ color: '#ef4444', fontSize: '14px', marginBottom: '20px' }}>{error}</p>
            <button onClick={onClose} style={endBtnStyle}>Close</button>
          </div>
        </div>
      </div>
    );
  }

  if (callState === 'connecting') {
    return (
      <div style={overlayStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: 'center', padding: '60px 20px' }}>
            <div style={spinnerStyle} />
            <p style={{ color: '#888', fontSize: '14px', marginTop: '20px' }}>
              Connecting to {agent.name}...
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={overlayStyle}>
      <div style={cardStyle}>
        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '16px 20px', borderBottom: '1px solid #1A1A1A',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              width: '40px', height: '40px', borderRadius: '50%',
              background: 'linear-gradient(135deg, #3ECF8E 0%, #2A9D6E 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '18px',
            }}>●</div>
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff', margin: 0 }}>{agent.name}</h3>
              <p style={{ fontSize: '11px', color: '#666', margin: 0 }}>{agent.clinic_name}</p>
            </div>
          </div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '8px',
            fontSize: '13px', fontWeight: 600, color: '#ef4444',
            fontFamily: 'monospace',
          }}>
            <span style={{
              width: '8px', height: '8px', borderRadius: '50%',
              background: '#ef4444', animation: 'pulse 1.5s infinite',
            }} />
            {formatTime(callDuration)}
          </div>
        </div>

        {/* Visualizer */}
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', padding: '30px 20px', minHeight: '180px',
        }}>
          <canvas
            ref={canvasRef}
            width={320}
            height={100}
            style={{ borderRadius: '8px' }}
          />
          <p style={{
            color: getStateColor(), fontSize: '13px',
            fontWeight: 600, marginTop: '16px',
          }}>
            {getStateLabel()}
          </p>
          {callState === 'demo' && (
            <p style={{
              color: '#F59E0B', fontSize: '11px',
              marginTop: '8px', padding: '4px 12px',
              background: '#F59E0B15', borderRadius: '6px',
              border: '1px solid #F59E0B30',
            }}>
              Demo Mode — Configure LiveKit keys for real calls
            </p>
          )}
        </div>

        {/* Transcript */}
        {transcript.length > 0 && (
          <div style={{
            maxHeight: '150px', overflowY: 'auto',
            padding: '0 20px 12px', borderTop: '1px solid #1A1A1A',
          }}>
            {transcript.map((entry, i) => (
              <div key={i} style={{
                padding: '8px 0', borderBottom: '1px solid #111',
                display: 'flex', gap: '8px',
              }}>
                <span style={{ fontSize: '10px', color: '#555', flexShrink: 0 }}>
                  {entry.role === 'ai' ? 'Agent' : 'You'}
                </span>
                <span style={{
                  fontSize: '12px',
                  color: entry.role === 'ai' ? '#3ECF8E' : '#ccc',
                }}>
                  {entry.text}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Controls */}
        <div style={{
          display: 'flex', justifyContent: 'center', gap: '16px',
          padding: '16px 20px', borderTop: '1px solid #1A1A1A',
        }}>
          <button
            onClick={toggleMute}
            style={{
              ...controlBtnStyle,
              background: isMuted ? '#ef444420' : '#1A1A1A',
              color: isMuted ? '#ef4444' : '#fff',
            }}
            title={isMuted ? 'Unmute' : 'Mute'}
          >
            {isMuted ? 'M' : '●'}
          </button>
          <button onClick={handleEndCall} style={endBtnStyle}>
                        End Call
          </button>
        </div>

        {/* Language indicator */}
        <div style={{
          textAlign: 'center', padding: '8px',
          borderTop: '1px solid #111', fontSize: '11px', color: '#555',
        }}>
          {LANG_LABELS[agent.tts_language] || agent.tts_language} · {agent.tts_voice}
          {callId && <span style={{ marginLeft: '8px' }}>· {callId.slice(0, 8)}</span>}
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

// ── Styles ──────────────────────────────────────────────
const overlayStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 9999,
  background: 'rgba(0,0,0,0.85)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  backdropFilter: 'blur(8px)',
};

const cardStyle: React.CSSProperties = {
  background: '#0F0F0F', border: '1px solid #1A1A1A',
  borderRadius: '16px', width: '400px', maxWidth: '90vw',
  overflow: 'hidden',
};

const controlBtnStyle: React.CSSProperties = {
  width: '48px', height: '48px', borderRadius: '50%',
  border: '1px solid #333', background: '#1A1A1A',
  cursor: 'pointer', fontSize: '18px',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const endBtnStyle: React.CSSProperties = {
  padding: '12px 24px', borderRadius: '24px',
  background: '#ef4444', color: '#fff', border: 'none',
  cursor: 'pointer', fontSize: '13px', fontWeight: 600,
};

const spinnerStyle: React.CSSProperties = {
  width: '40px', height: '40px', border: '3px solid #222',
  borderTopColor: '#3ECF8E', borderRadius: '50%',
  animation: 'spin 0.8s linear infinite',
  margin: '0 auto',
};

export default WebCallInterface;
