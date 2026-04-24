/**
 * TestAgentModal.tsx
 * Full in-browser AI agent tester with:
 *  - Voice mode: WebSocket + Web Audio API VAD for real-time voice call
 *  - Chat mode: REST text conversation
 *
 * FIXES APPLIED:
 * Issue 1: WebSocket now waits 15s for ready msg; shows clear errors if none received.
 * Issue 2: Right-side slide panel (420px, 100vh) replacing centered modal.
 * Issue 3: Silence detection (VAD), mp3 blob hints, timing display in footer.
 * Issue 4: Full cleanup (audio, mic, ws, recorder) on panel close.
 * Issue 5: Handles tts_failed message with "🔇 Audio unavailable" badge.
 */
import {
  Download,
  Headphones,
  MessageSquare,
  Mic, MicOff,
  Phone,
  PhoneOff,
  RotateCcw,
  Send,
  X
} from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL, WS_URL } from '../api/client';

// ── Types ─────────────────────────────────────────────────────────────────────
interface Message {
  id: string;
  role: 'agent' | 'user';
  text: string;
  time: string;
  ms?: number;
  audioUnavailable?: boolean;
}

interface TestAgentModalProps {
  agent?: any;
  agentId?: string;
  agentName?: string;
  defaultMode?: Mode;
  onClose: () => void;
}

interface TimingInfo {
  stt_ms?: number;
  llm_ms?: number;
  tts_ms?: number;
  total_ms?: number;
}

type Mode = 'voice' | 'chat';
type CallStatus = 'idle' | 'requesting' | 'connecting' | 'connected' | 'ended';

// ── Helpers ───────────────────────────────────────────────────────────────────
function nowTime() {
  return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
}

function formatDuration(secs: number) {
  const m = String(Math.floor(secs / 60)).padStart(2, '0');
  const s = String(secs % 60).padStart(2, '0');
  return `${m}:${s}`;
}

function buildTranscript(messages: Message[], mode: Mode, agentName: string, duration?: number): string {
  const header = [
    '----------------------------------------',
    'Lifodial — Agent Test Transcript',
    `Agent: ${agentName}`,
    `Date: ${new Date().toISOString().slice(0, 10)}`,
    `Mode: ${mode === 'voice' ? 'Voice Call' : 'Chat'}`,
    duration !== undefined ? `Duration: ${formatDuration(duration)}` : '',
    '----------------------------------------',
  ].filter(Boolean).join('\n');

  const body = messages
    .map(m => `${m.role === 'agent' ? `Agent (${agentName})` : 'User'}: ${m.text}`)
    .join('\n');

  return `${header}\n${body}\n----------------------------------------`;
}

function downloadTxt(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Waveform Visualizer ───────────────────────────────────────────────────────
export function VoiceWaveform({ isActive, color = '#3ECF8E', audioLevel = 0, size = 'md' }: { isActive: boolean; color?: string; audioLevel?: number; size?: 'sm' | 'md' | 'lg' }) {
  const config = { sm: { bars: 9, gap: 3, w: 3, h: 36 }, md: { bars: 24, gap: 2, w: 3, h: 48 }, lg: { bars: 36, gap: 2, w: 3, h: 56 } };
  const { bars: BAR_COUNT, gap, w, h: HEIGHT } = config[size];
  const [barHeights, setBarHeights] = React.useState<number[]>(Array(BAR_COUNT).fill(3));
  const animRef = React.useRef<number>(0);
  const phaseRef = React.useRef<number[]>(Array.from({ length: BAR_COUNT }, () => Math.random() * Math.PI * 2));

  React.useEffect(() => {
    let active = true;
    const animate = () => {
      if (!active) return;
      const level = Math.min(audioLevel, 1);
      const maxH = HEIGHT * 0.85;
      const newHeights = phaseRef.current.map((phase, i) => {
        if (!isActive) return 3;
        const t = Date.now() * 0.005;
        const wave1 = Math.sin(t + phase + i * 0.35);
        const wave2 = Math.sin(t * 1.7 + phase * 2 + i * 0.2);
        const combined = (wave1 + wave2) * 0.5;
        const base = 3 + level * maxH * 0.5;
        const variance = level * maxH * 0.4 * Math.abs(combined);
        return Math.max(3, Math.min(maxH, base + variance));
      });
      setBarHeights(newHeights);
      animRef.current = requestAnimationFrame(animate);
    };
    animRef.current = requestAnimationFrame(animate);
    return () => { active = false; cancelAnimationFrame(animRef.current); };
  }, [isActive, audioLevel, HEIGHT]);

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: `${gap}px`, height: `${HEIGHT}px` }}>
      {barHeights.map((h, i) => (
        <div key={i} style={{
          width: `${w}px`, borderRadius: '3px', backgroundColor: isActive ? color : '#333',
          height: `${h}px`, transition: 'height 0.06s ease-out, background-color 0.3s',
          opacity: isActive ? 0.7 + (h / HEIGHT) * 0.3 : 0.3,
        }} />
      ))}
    </div>
  );
}

// ── Voice Mode ────────────────────────────────────────────────────────────────
function VoiceMode({ agent, agentId: directId, agentName: directName, onClose }: {
  agent?: any; agentId?: string; agentName?: string; onClose: () => void;
}) {
  const agentId = agent?.id || directId;
  const agentName = agent?.name || agent?.agent_name || directName || 'Agent';

  const [status, setStatus] = useState<CallStatus>('idle');
  const [muted, setMuted] = useState(false);
  const [duration, setDuration] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentTranscript, setCurrentTranscript] = useState('');
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [apiKeyMissing, setApiKeyMissing] = useState(false);
  const [detectedLang, setDetectedLang] = useState('');
  const [timing, setTiming] = useState<TimingInfo | null>(null);
  const [connectError, setConnectError] = useState('');

  const [isSpeaking, setIsSpeaking] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const animFrameRef = useRef<number>(0);

  // ISSUE 4: All refs for cleanup
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioQueueRef = useRef<Blob[]>([]);
  const isPlayingRef = useRef(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const lastAudioAtRef = useRef(0);
  const fallbackSpeakTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);  // ISSUE 4
  const currentAudioUrlRef = useRef<string | null>(null);          // ISSUE 4
  const streamRef = useRef<MediaStream | null>(null);              // ISSUE 4
  const connectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null); // ISSUE 1

  // VAD (Voice Activity Detection) refs
  const vadSilenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isRecordingRef = useRef(false);
  const chunksRef = useRef<Blob[]>([]);
  const vadActiveRef = useRef(false);

  // ISSUE 4: Master cleanup function — stops everything
  const cleanupAll = useCallback((keepMessages = false) => {
    // 1. Stop audio IMMEDIATELY
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current.src = '';
      currentAudioRef.current = null;
    }
    if (currentAudioUrlRef.current) {
      URL.revokeObjectURL(currentAudioUrlRef.current);
      currentAudioUrlRef.current = null;
    }
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    setAgentSpeaking(false);

    // 2. Close WebSocket
    if (wsRef.current) {
      const ws = wsRef.current;
      wsRef.current = null;
      try { ws.close(1000, 'Panel closed'); } catch (_) {}
    }

    // 3. Stop recording
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try { mediaRecorderRef.current.stop(); } catch (_) {}
    }
    mediaRecorderRef.current = null;

    // 4. Stop microphone
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
      setStream(null);
    }

    // 5. Stop AudioContext
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close().catch(() => {});
    }
    audioContextRef.current = null;
    analyserRef.current = null;

    // 6. Clear timers
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (vadSilenceTimerRef.current) { clearTimeout(vadSilenceTimerRef.current); vadSilenceTimerRef.current = null; }
    if (fallbackSpeakTimerRef.current) { clearTimeout(fallbackSpeakTimerRef.current); fallbackSpeakTimerRef.current = null; }
    if (connectTimeoutRef.current) { clearTimeout(connectTimeoutRef.current); connectTimeoutRef.current = null; }
    cancelAnimationFrame(animFrameRef.current);

    // 7. Cancel speech synthesis
    if (typeof window !== 'undefined' && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }

    // 8. Reset state
    if (!keepMessages) {
      setMessages([]);
      setDuration(0);
    }
    setCurrentTranscript('');
    setIsSpeaking(false);
    setAudioLevel(0);
    setDetectedLang('');
    vadActiveRef.current = false;
    isRecordingRef.current = false;
    chunksRef.current = [];
  }, []);

  // Detect microphone input level with smooth audio level tracking + VAD
  useEffect(() => {
    if (!stream) return;

    let audioCtx = audioContextRef.current;
    if (!audioCtx || audioCtx.state === 'closed') {
      audioCtx = new AudioContext();
      audioContextRef.current = audioCtx;
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();

    const analyser = audioCtx.createAnalyser();
    analyserRef.current = analyser;
    const source = audioCtx.createMediaStreamSource(stream);
    source.connect(analyser);
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.8;

    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    let smoothLevel = 0;

    const SPEECH_THRESHOLD = 12;   // Min avg amplitude to consider speech
    const SILENCE_DURATION = 1200; // ms of silence before sending chunk (ISSUE 3)

    const checkLevel = () => {
      analyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
      const avg = sum / dataArray.length;
      const normalized = Math.min(avg / 80, 1);
      smoothLevel = smoothLevel * 0.7 + normalized * 0.3;
      setAudioLevel(smoothLevel);

      const speaking = avg > SPEECH_THRESHOLD;
      setIsSpeaking(speaking);

      // VAD: detect speech start/stop for auto-chunking
      if (speaking && !vadActiveRef.current && wsRef.current?.readyState === WebSocket.OPEN && !isPlayingRef.current) {
        vadActiveRef.current = true;
        startRecordingChunk();
        if (vadSilenceTimerRef.current) {
          clearTimeout(vadSilenceTimerRef.current);
          vadSilenceTimerRef.current = null;
        }
      }

      if (speaking && vadSilenceTimerRef.current) {
        clearTimeout(vadSilenceTimerRef.current);
        vadSilenceTimerRef.current = null;
      }

      if (!speaking && vadActiveRef.current && !vadSilenceTimerRef.current) {
        vadSilenceTimerRef.current = setTimeout(() => {
          vadActiveRef.current = false;
          stopRecordingAndSend();
          vadSilenceTimerRef.current = null;
        }, SILENCE_DURATION);
      }

      animFrameRef.current = requestAnimationFrame(checkLevel);
    };

    animFrameRef.current = requestAnimationFrame(checkLevel);
    return () => {
      cancelAnimationFrame(animFrameRef.current);
      if (vadSilenceTimerRef.current) clearTimeout(vadSilenceTimerRef.current);
    };
  }, [stream]);

  const startRecordingChunk = () => {
    if (!streamRef.current || isRecordingRef.current) return;
    isRecordingRef.current = true;
    chunksRef.current = [];

    try {
      // ISSUE 3: Prefer formats Sarvam STT handles well
      const mimeType = MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')
        ? 'audio/ogg;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : 'audio/mp4';

      const recorder = new MediaRecorder(streamRef.current, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        isRecordingRef.current = false;
        if (chunksRef.current.length > 0) {
          const blob = new Blob(chunksRef.current, { type: mimeType });
          sendAudioToBackend(blob);
        }
      };

      recorder.start(100); // collect data every 100ms (ISSUE 3)
    } catch (e) {
      console.error('MediaRecorder error:', e);
      isRecordingRef.current = false;
    }
  };

  const stopRecordingAndSend = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  const sendAudioToBackend = async (blob: Blob) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    if (blob.size < 1000) return; // skip tiny clips

    try {
      const buffer = await blob.arrayBuffer();
      wsRef.current.send(buffer);
      setCurrentTranscript('Processing...');
    } catch (e) {
      console.error('Failed to send audio:', e);
    }
  };

  // ISSUE 4: Play audio with proper ref tracking for cleanup
  const playNextAudio = () => {
    if (audioQueueRef.current.length === 0) {
      isPlayingRef.current = false;
      setAgentSpeaking(false);
      currentAudioRef.current = null;
      return;
    }
    isPlayingRef.current = true;
    setAgentSpeaking(true);

    const blob = audioQueueRef.current.shift()!;
    // ISSUE 5: Hint MIME type so browser picks correct decoder
    const mimeHint = blob.type || 'audio/mpeg';
    const typedBlob = new Blob([blob], { type: mimeHint });
    const url = URL.createObjectURL(typedBlob);

    // ISSUE 4: Track current URL for cleanup
    if (currentAudioUrlRef.current) {
      URL.revokeObjectURL(currentAudioUrlRef.current);
    }
    currentAudioUrlRef.current = url;

    const audio = new Audio();
    currentAudioRef.current = audio; // ISSUE 4

    audio.onended = () => {
      if (currentAudioUrlRef.current === url) {
        URL.revokeObjectURL(url);
        currentAudioUrlRef.current = null;
      }
      currentAudioRef.current = null;
      playNextAudio();
    };
    audio.onerror = (e) => {
      console.error('Audio element error:', e);
      if (currentAudioUrlRef.current === url) {
        URL.revokeObjectURL(url);
        currentAudioUrlRef.current = null;
      }
      currentAudioRef.current = null;
      playNextAudio();
    };
    audio.src = url;
    audio.play().catch((e) => {
      console.error('Audio play error:', e);
      blob.arrayBuffer().then(buf => {
        const ctx = new AudioContext();
        return ctx.decodeAudioData(buf).then(decoded => {
          const source = ctx.createBufferSource();
          source.buffer = decoded;
          source.connect(ctx.destination);
          source.onended = () => { ctx.close(); playNextAudio(); };
          source.start();
        });
      }).catch((e2) => {
        console.error('AudioContext fallback also failed:', e2);
        if (currentAudioUrlRef.current === url) URL.revokeObjectURL(url);
        playNextAudio();
      });
    });
  };

  const enqueueAudio = (blob: Blob) => {
    lastAudioAtRef.current = Date.now();
    audioQueueRef.current.push(blob);
    if (!isPlayingRef.current) {
      playNextAudio();
    }
  };

  const speakTextFallback = (text: string, langHint = 'en-IN') => {
    if (!text || typeof window === 'undefined' || !window.speechSynthesis) return;
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = langHint;
      const voices = window.speechSynthesis.getVoices();
      const preferred = voices.find(v => v.lang?.toLowerCase().startsWith(langHint.toLowerCase().split('-')[0]));
      if (preferred) utterance.voice = preferred;
      window.speechSynthesis.speak(utterance);
    } catch (e) {
      console.warn('Speech fallback failed:', e);
    }
  };

  const scheduleSpeechFallback = (text: string, langHint = 'en-IN') => {
    if (!text) return;
    if (fallbackSpeakTimerRef.current) { clearTimeout(fallbackSpeakTimerRef.current); fallbackSpeakTimerRef.current = null; }
    const checkStartedAt = Date.now();
    fallbackSpeakTimerRef.current = setTimeout(() => {
      const noRecentAudio = Date.now() - lastAudioAtRef.current > 1200;
      const nothingQueued = audioQueueRef.current.length === 0;
      const notPlaying = !isPlayingRef.current;
      if (checkStartedAt >= lastAudioAtRef.current && noRecentAudio && nothingQueued && notPlaying) {
        speakTextFallback(text, langHint);
      }
      fallbackSpeakTimerRef.current = null;
    }, 1400);
  };

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // ISSUE 4: Cleanup on unmount
  useEffect(() => {
    return () => { cleanupAll(false); };
  }, [cleanupAll]);

  // Timer
  useEffect(() => {
    if (status === 'connected') {
      timerRef.current = setInterval(() => setDuration(d => d + 1), 1000);
    } else {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [status]);

  const addMessage = useCallback((role: 'agent' | 'user', text: string, extra?: Partial<Message>) => {
    setMessages(prev => [...prev, { id: Date.now().toString() + Math.random(), role, text, time: nowTime(), ...extra }]);
  }, []);

  const startCall = async () => {
    setStatus('requesting');
    setDuration(0);
    setMessages([]);
    setConnectError('');
    setTiming(null);
    lastAudioAtRef.current = 0;

    // Unlock audio playback AND pre-create AudioContext for VAD in user gesture context
    try {
      const unlockCtx = new AudioContext();
      if (unlockCtx.state === 'suspended') await unlockCtx.resume();
      audioContextRef.current = unlockCtx;
    } catch { /* ignore */ }

    setStatus('connecting');
    let ms: MediaStream | null = null;

    try {
      try {
        ms = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
      } catch (e1: any) {
        console.warn('Preferred audio constraints failed:', e1.name);
        try {
          ms = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e2: any) {
          console.warn('Basic audio failed, trying device enumeration');
          const devices = await navigator.mediaDevices.enumerateDevices();
          const audioInputs = devices.filter(d => d.kind === 'audioinput');
          if (audioInputs.length > 0) {
            const deviceId = audioInputs[0].deviceId;
            ms = await navigator.mediaDevices.getUserMedia(
              deviceId ? { audio: { deviceId: { exact: deviceId } } } : { audio: true }
            );
          } else {
            throw e2;
          }
        }
      }
      // ISSUE 4: Store stream in ref for cleanup
      streamRef.current = ms;
      setStream(ms);
    } catch (e: any) {
      console.warn('Mic unavailable — proceeding without mic:', e.name, e.message);
      addMessage('agent', e.name === 'NotAllowedError'
        ? '⚠️ Microphone permission denied. Click the 🔒 icon → Site Settings → Allow Microphone, then retry.'
        : '⚠️ Microphone not detected. You can still hear the agent.');
    }

    // ISSUE 1: Connect WebSocket with 15-second connection timeout
    const ws = new WebSocket(`${WS_URL}/ws/agent-call/${agentId}`);
    ws.binaryType = 'blob';
    wsRef.current = ws;

    // ISSUE 1: Start connection timeout — if no "ready" within 15s, show error
    const readyReceived = { current: false };
    connectTimeoutRef.current = setTimeout(() => {
      if (!readyReceived.current && wsRef.current === ws && ws.readyState === WebSocket.OPEN) {
        console.warn('WebSocket connected but no ready message received after 15s');
        addMessage('agent', '⚠️ Agent connected but not responding. The backend may be processing slowly. Please try again.');
        setStatus('idle');
        cleanupAll(true);
      }
    }, 30000);

    ws.onmessage = async (event) => {
      // Handle Blob
      if (event.data instanceof Blob) {
        // ISSUE 5: Try to detect MP3 (Sarvam returns base64-decoded MP3)
        const audioBlob = event.data.type ? event.data : new Blob([event.data], { type: 'audio/mpeg' });
        enqueueAudio(audioBlob);
        return;
      }
      if (event.data instanceof ArrayBuffer) {
        const audioBlob = new Blob([event.data], { type: 'audio/mpeg' });
        enqueueAudio(audioBlob);
        return;
      }

      try {
        const msg = JSON.parse(event.data);

        // ISSUE 1: Mark ready received to cancel connection timeout
        if (msg.type === 'ready') {
          readyReceived.current = true;
          if (connectTimeoutRef.current) {
            clearTimeout(connectTimeoutRef.current);
            connectTimeoutRef.current = null;
          }
          if (msg.first_message) addMessage('agent', msg.first_message);
          setStatus('connected');
          return;
        }

        if (msg.type === 'status') {
          if (msg.status === 'connected') {
            readyReceived.current = true;
            if (connectTimeoutRef.current) {
              clearTimeout(connectTimeoutRef.current);
              connectTimeoutRef.current = null;
            }
            setStatus('connected');
          }
          if (msg.status === 'ended') { setStatus('ended'); setSummaryOpen(true); }
          if (msg.status === 'processing') setCurrentTranscript('Processing...');
          if (msg.status === 'thinking') setCurrentTranscript('Thinking...');
          if (msg.status === 'speaking') setCurrentTranscript('');
          if (msg.status === 'idle') setCurrentTranscript('');
        }
        // ISSUE 3: Show timing info
        if (msg.type === 'timing') {
          setTiming({ stt_ms: msg.stt_ms, llm_ms: msg.llm_ms, tts_ms: msg.tts_ms, total_ms: msg.total_ms });
        }
        if (msg.type === 'transcript') {
          setCurrentTranscript('');
          if (msg.role === 'user') addMessage('user', msg.text);
          else if (msg.role === 'assistant') addMessage('agent', msg.text);
          if (msg.detected_language) setDetectedLang(msg.detected_language);
        }
        if (msg.type === 'agent_text') {
          setCurrentTranscript('');
          addMessage('agent', msg.text);
          if (msg.detected_language) setDetectedLang(msg.detected_language);
        }
        if (msg.type === 'language_detected') setDetectedLang(msg.language || '');
        if (msg.type === 'error') {
          setCurrentTranscript('');
          if (msg.code === 'GEMINI_KEY_MISSING') setApiKeyMissing(true);
          else addMessage('agent', `⚠️ ${msg.message || 'Something went wrong. Please try again.'}`);
        }
        // ISSUE 5: Handle TTS failure
        if (msg.type === 'tts_failed') {
          setCurrentTranscript('');
          addMessage('agent', msg.message || 'Response received', { audioUnavailable: true });
        }
      } catch { /* ignore parse errors */ }
    };

    ws.onopen = () => {
      // Connection opened — wait for "ready" message from backend (handled in onmessage)
    };

    ws.onerror = (e) => {
      console.error('WebSocket error:', e);
      if (connectTimeoutRef.current) { clearTimeout(connectTimeoutRef.current); connectTimeoutRef.current = null; }
      const errMsg = '⚠️ Connection error. Make sure the backend is running on port 8001 and try again.';
      setConnectError(errMsg);
      addMessage('agent', errMsg);
      setStatus('idle');
    };

    ws.onclose = (e) => {
      if (connectTimeoutRef.current) { clearTimeout(connectTimeoutRef.current); connectTimeoutRef.current = null; }
      // Only change state if not already ended (don't override summary)
      setStatus(prev => prev === 'ended' ? prev : 'idle');
      if (!readyReceived && e.code !== 1000 && e.code !== 1001) {
        const reason = e.reason || `Code ${e.code}`;
        const errMsg = `⚠️ Connection closed before ready: ${reason}. Check backend logs for agent "${agentId}".`;
        setConnectError(errMsg);
        if (messages.length === 0) addMessage('agent', errMsg);
      }
      // Stop mic
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(t => t.stop());
        streamRef.current = null;
        setStream(null);
      }
    };
  };

  const endCall = () => {
    // Send graceful end then cleanup
    try { wsRef.current?.send(JSON.stringify({ type: 'end' })); } catch (_) {}
    const savedMessages = [...messages];
    cleanupAll(true);
    setStatus('ended');
    setSummaryOpen(true);
  };

  const handleNewCall = () => {
    setSummaryOpen(false);
    setStatus('idle');
    setDuration(0);
    setMessages([]);
    setDetectedLang('');
    setTiming(null);
    setConnectError('');
  };

  const exportTranscript = () => {
    const content = buildTranscript(messages, 'voice', agentName, duration);
    downloadTxt(content, `agent-call-${agentId.slice(0, 8)}-${Date.now()}.txt`);
  };

  const toggleMute = () => {
    const newMuted = !muted;
    setMuted(newMuted);
    if (streamRef.current) streamRef.current.getAudioTracks().forEach(track => { track.enabled = !newMuted; });
    if (newMuted && mediaRecorderRef.current?.state !== 'inactive') {
      mediaRecorderRef.current?.stop();
      vadActiveRef.current = false;
    }
  };

  // ── Summary modal ──
  if (summaryOpen) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ padding: '20px', borderBottom: '1px solid #2E2E2E', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ color: '#fff', fontWeight: 700, margin: 0, fontSize: '16px' }}>Call Summary</h3>
          <span style={{ color: '#3ECF8E', fontFamily: 'monospace', fontSize: '20px', fontWeight: 700 }}>{formatDuration(duration)}</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {messages.map(m => (
            <div key={m.id} style={{ display: 'flex', flexDirection: 'column', gap: '2px', alignItems: m.role === 'agent' ? 'flex-start' : 'flex-end' }}>
              <span style={{ fontSize: '10px', color: '#555' }}>{m.role === 'agent' ? agentName : 'You'} · {m.time}</span>
              <div style={{ background: m.role === 'agent' ? '#1A2A1F' : '#1A1A2E', border: `1px solid ${m.role === 'agent' ? '#3ECF8E30' : '#4444aa30'}`, borderRadius: m.role === 'agent' ? '4px 12px 12px 12px' : '12px 4px 12px 12px', padding: '10px 14px', fontSize: '13px', color: '#d1d5db', maxWidth: '80%', lineHeight: 1.5 }}>
                {m.text}
                {m.audioUnavailable && <span style={{ fontSize: '11px', color: '#6b7280', display: 'block', marginTop: '4px' }}>🔇 Audio unavailable</span>}
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding: '16px', borderTop: '1px solid #2E2E2E', display: 'flex', gap: '10px' }}>
          <button onClick={exportTranscript} style={{ flex: 1, padding: '10px', background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#888', fontSize: '13px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', fontWeight: 600 }}>
            <Download size={14} /> Export .txt
          </button>
          <button onClick={handleNewCall} style={{ flex: 1, padding: '10px', background: '#3ECF8E', border: 'none', borderRadius: '8px', color: '#000', fontSize: '13px', cursor: 'pointer', fontWeight: 700 }}>
            New Test Call
          </button>
        </div>
      </div>
    );
  }

  if (apiKeyMissing) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', padding: '32px', gap: '20px', textAlign: 'center' }}>
        <Headphones size={32} color="#3ECF8E" />
        <p style={{ color: '#fff', fontWeight: 700, fontSize: '16px', margin: 0 }}>Add your API key to enable voice testing</p>
        <p style={{ color: '#888', fontSize: '13px', margin: 0 }}>Set your LLM API key in the .env file and restart the backend.</p>
        <button onClick={() => { setApiKeyMissing(false); setStatus('idle'); }} style={{ padding: '10px 20px', background: '#2E2E2E', border: 'none', borderRadius: '8px', color: '#888', cursor: 'pointer', fontWeight: 600 }}>
          Go Back
        </button>
      </div>
    );
  }

  // ── IDLE/CONNECTING state ──
  if (status === 'idle' || status === 'requesting' || status === 'connecting') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', alignItems: 'center', justifyContent: 'center', padding: '32px', gap: '24px' }}>
        <div style={{ position: 'relative' }}>
          <div style={{ width: '80px', height: '80px', borderRadius: '50%', background: '#1A1A1A', border: '3px solid #2E2E2E', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Headphones size={36} color="#555" />
          </div>
        </div>

        <div style={{ textAlign: 'center' }}>
          <p style={{ color: '#fff', fontWeight: 700, fontSize: '18px', margin: '0 0 4px' }}>{agentName}</p>
          <p style={{ color: status === 'connecting' ? '#f59e0b' : '#555', fontSize: '13px', margin: 0, fontWeight: 600 }}>
            {status === 'idle' ? 'Ready to call' : status === 'requesting' ? 'Requesting microphone...' : 'Connecting...'}
          </p>
          {connectError && (
            <p style={{ color: '#ef4444', fontSize: '12px', margin: '8px 0 0', maxWidth: '280px', lineHeight: 1.5 }}>{connectError}</p>
          )}
        </div>

        <button
          onClick={startCall}
          disabled={status !== 'idle'}
          style={{
            padding: '16px 36px', borderRadius: '40px', background: '#3ECF8E',
            border: 'none', cursor: status === 'idle' ? 'pointer' : 'wait',
            display: 'flex', alignItems: 'center', gap: '10px',
            fontSize: '15px', fontWeight: 700, color: '#000',
            boxShadow: '0 0 24px rgba(62,207,142,0.4)',
            opacity: status === 'idle' ? 1 : 0.6,
            transition: 'all 0.2s',
          }}
        >
          <Phone size={18} fill="#000" /> Start Voice Call
        </button>

        <p style={{ color: '#444', fontSize: '12px', textAlign: 'center', marginTop: 0 }}>
          Uses your microphone for voice conversation
        </p>
      </div>
    );
  }

  // ── CONNECTED state ──
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Top bar */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #1A1A1A', display: 'flex', alignItems: 'center', gap: '12px', background: '#0A0A0A' }}>
        <div style={{ width: '40px', height: '40px', borderRadius: '50%', flexShrink: 0, background: 'rgba(62,207,142,0.15)', border: '2px solid #3ECF8E', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 12px rgba(62,207,142,0.3)', position: 'relative' }}>
          <Headphones size={18} color="#3ECF8E" />
          <div style={{ position: 'absolute', bottom: -1, right: -1, width: '10px', height: '10px', borderRadius: '50%', background: '#3ECF8E', border: '2px solid #0A0A0A' }} />
        </div>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <VoiceWaveform isActive={isSpeaking || agentSpeaking} color={agentSpeaking ? '#3ECF8E' : isSpeaking ? '#60A5FA' : '#3ECF8E'} audioLevel={agentSpeaking ? 0.65 : audioLevel} size="md" />
        </div>
        <span style={{ fontFamily: 'monospace', fontSize: '14px', color: '#3ECF8E', fontWeight: 700, minWidth: '48px', textAlign: 'right' }}>{formatDuration(duration)}</span>
      </div>

      {/* Status pill */}
      <div style={{ padding: '6px 16px', display: 'flex', justifyContent: 'center' }}>
        <span style={{ fontSize: '11px', fontWeight: 600, padding: '3px 12px', borderRadius: '20px', background: agentSpeaking ? 'rgba(62,207,142,0.1)' : isSpeaking ? 'rgba(96,165,250,0.1)' : 'rgba(255,255,255,0.03)', color: agentSpeaking ? '#3ECF8E' : isSpeaking ? '#60A5FA' : '#555', border: `1px solid ${agentSpeaking ? '#3ECF8E20' : isSpeaking ? '#60A5FA20' : '#1A1A1A'}` }}>
          {agentSpeaking ? '🗣 Agent speaking...' : isSpeaking ? '🎤 Listening...' : currentTranscript ? `⏳ ${currentTranscript}` : '● Ready — speak to begin'}
        </span>
      </div>

      {/* Transcript */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {messages.length === 0 && !currentTranscript && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <p style={{ color: '#333', fontSize: '13px', textAlign: 'center' }}>Start speaking — your conversation will appear here in real-time</p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} style={{ display: 'flex', flexDirection: 'column', gap: '3px', alignItems: m.role === 'agent' ? 'flex-start' : 'flex-end' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              {m.role === 'agent' && (
                <div style={{ width: '20px', height: '20px', borderRadius: '50%', background: 'rgba(62,207,142,0.1)', border: '1px solid #3ECF8E30', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Headphones size={10} color="#3ECF8E" />
                </div>
              )}
              <span style={{ fontSize: '10px', color: '#555' }}>{m.role === 'agent' ? agentName : 'You'} · {m.time}</span>
            </div>
            <div style={{ background: m.role === 'agent' ? '#1A2A1F' : '#1A1A2E', border: `1px solid ${m.role === 'agent' ? '#3ECF8E25' : '#4444aa25'}`, borderRadius: m.role === 'agent' ? '4px 12px 12px 12px' : '12px 4px 12px 12px', padding: '10px 14px', fontSize: '13px', color: '#d1d5db', maxWidth: '85%', lineHeight: 1.6 }}>
              {m.text}
              {/* ISSUE 5: Show audio unavailable badge */}
              {m.audioUnavailable && (
                <span style={{ fontSize: '11px', color: '#6b7280', display: 'block', marginTop: '4px' }}>🔇 Audio unavailable</span>
              )}
            </div>
          </div>
        ))}
        {currentTranscript && (
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <span style={{ fontSize: '12px', color: '#666', fontStyle: 'italic', padding: '4px 12px', background: '#111', borderRadius: '12px', border: '1px solid #1A1A1A' }}>
              {currentTranscript}
            </span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Bottom controls */}
      <div style={{ padding: '12px 16px', borderTop: '1px solid #1A1A1A', display: 'flex', flexDirection: 'column', gap: '8px', background: '#0A0A0A' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '16px' }}>
          <button
            onClick={toggleMute}
            style={{ width: '48px', height: '48px', borderRadius: '50%', background: muted ? '#ef4444' : '#1A1A1A', border: `1px solid ${muted ? '#ef444460' : '#2E2E2E'}`, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.2s' }}
          >
            {muted ? <MicOff size={20} color="#fff" /> : <Mic size={20} color="#3ECF8E" />}
          </button>
          <button
            onClick={endCall}
            style={{ padding: '12px 28px', borderRadius: '40px', background: '#ef4444', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '14px', fontWeight: 700, color: '#fff', boxShadow: '0 0 16px rgba(239,68,68,0.3)' }}
          >
            <PhoneOff size={16} /> End Call
          </button>
        </div>
        {/* ISSUE 3: Show timing info */}
        {timing && timing.total_ms && (
          <p style={{ textAlign: 'center', fontSize: '10px', color: '#3ECF8E', margin: 0, fontFamily: 'monospace' }}>
            ⚡ {(timing.total_ms / 1000).toFixed(2)}s total
            {timing.stt_ms ? ` · STT ${timing.stt_ms}ms` : ''}
            {timing.llm_ms ? ` · LLM ${timing.llm_ms}ms` : ''}
            {timing.tts_ms ? ` · TTS ${timing.tts_ms}ms` : ''}
          </p>
        )}
        {detectedLang && (
          <p style={{ textAlign: 'center', fontSize: '10px', color: '#555', margin: 0 }}>{detectedLang}</p>
        )}
      </div>

      <style>{`@keyframes ping { 75%, 100% { transform: scale(1.5); opacity: 0; } }`}</style>
    </div>
  );
}

// ── Chat Mode ─────────────────────────────────────────────────────────────────
function ChatMode({ agent, agentId: directId, agentName: directName }: { agent?: any; agentId?: string; agentName?: string; onClose: () => void }) {
  const agentId = agent?.id || directId;
  const agentName = agent?.name || agent?.agent_name || directName || 'Agent';
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  useEffect(() => {
    const init = async () => {
      try {
        const res = await fetch(`${API_URL}/agent-chat/${agentId}/greeting`);
        const data = await res.json();
        setSessionId(data.session_id);
        setMessages([{ id: '0', role: 'agent', text: data.message || `Hello! I'm ${agentName}. How can I help you today?`, time: nowTime() }]);
      } catch {
        setMessages([{ id: '0', role: 'agent', text: `Hello! I'm ${agentName}. How can I help you today?`, time: nowTime() }]);
        setSessionId(crypto.randomUUID());
      }
    };
    init();
  }, [agentId, agentName]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput('');

    const userMsg: Message = { id: Date.now().toString(), role: 'user', text, time: nowTime() };
    setMessages(prev => [...prev, userMsg, { id: 'typing', role: 'agent', text: '...', time: nowTime() }]);
    setLoading(true);

    const t0 = Date.now();
    try {
      const res = await fetch(`${API_URL}/agent-chat/${agentId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      if (!res.ok) throw new Error('API error');
      const data = await res.json();
      setMessages(prev => [...prev.filter(m => m.id !== 'typing'), { id: Date.now().toString() + '-agent', role: 'agent', text: data.response || 'No response', time: nowTime(), ms: Date.now() - t0 }]);
      if (data.session_id && !sessionId) setSessionId(data.session_id);
    } catch (err: any) {
      setMessages(prev => [...prev.filter(m => m.id !== 'typing'), { id: Date.now().toString(), role: 'agent', text: err instanceof TypeError ? 'Cannot reach the backend API. Make sure it\'s running.' : 'Something went wrong. Please try again.', time: nowTime() }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const clearChat = async () => {
    if (sessionId) {
      try { await fetch(`${API_URL}/agent-chat/${agentId}/session/${sessionId}`, { method: 'DELETE' }); } catch { }
    }
    const newSid = crypto.randomUUID();
    setSessionId(newSid);
    setMessages([{ id: '0', role: 'agent', text: `Hello again! I'm ${agentName}. How can I help?`, time: nowTime() }]);
  };

  const exportChat = () => {
    const content = buildTranscript(messages.filter(m => m.id !== 'typing'), 'chat', agentName);
    downloadTxt(content, `agent-chat-${agentId.slice(0, 8)}-${Date.now()}.txt`);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '10px 16px', borderBottom: '1px solid #1A1A1A', display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
        <button onClick={clearChat} style={{ background: 'none', border: '1px solid #2E2E2E', borderRadius: '7px', color: '#888', padding: '5px 12px', fontSize: '12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 600 }}>
          <RotateCcw size={12} /> Clear Chat
        </button>
        <button onClick={exportChat} style={{ background: 'none', border: '1px solid #2E2E2E', borderRadius: '7px', color: '#888', padding: '5px 12px', fontSize: '12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '5px', fontWeight: 600 }}>
          <Download size={12} /> Export
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {messages.map(m => (
          <div key={m.id} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'agent' ? 'flex-start' : 'flex-end', gap: '3px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              {m.role === 'agent' && <div style={{ width: '22px', height: '22px', borderRadius: '50%', background: 'rgba(62,207,142,0.1)', border: '1px solid #3ECF8E30', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Headphones size={11} color="#3ECF8E" /></div>}
              <span style={{ fontSize: '10px', color: '#555' }}>{m.role === 'agent' ? agentName : 'You'} · {m.time}</span>
              {m.ms && m.role === 'agent' && <span style={{ fontSize: '10px', color: '#3ECF8E' }}>{m.ms}ms</span>}
            </div>
            {m.id === 'typing' ? (
              <div style={{ background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px 12px 12px 4px', padding: '10px 14px', display: 'flex', gap: '5px', alignItems: 'center' }}>
                {[0, 1, 2].map(i => <div key={i} style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#3ECF8E', animation: `bounce 1.2s ${i * 0.2}s infinite` }} />)}
              </div>
            ) : (
              <div style={{ background: m.role === 'agent' ? '#1A2A1F' : '#1A1A2E', border: `1px solid ${m.role === 'agent' ? '#3ECF8E25' : '#4444aa25'}`, borderRadius: m.role === 'agent' ? '4px 12px 12px 12px' : '12px 4px 12px 12px', padding: '10px 14px', fontSize: '13px', color: '#d1d5db', maxWidth: '75%', lineHeight: 1.6 }}>
                {m.text || <span style={{ color: '#555' }}>typing...</span>}
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>
      <div style={{ padding: '12px 16px', borderTop: '1px solid #1A1A1A', display: 'flex', gap: '10px' }}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendMessage()}
          placeholder="Type a message... (Enter to send)"
          style={{ flex: 1, padding: '10px 14px', background: '#0F0F0F', border: '1px solid #2E2E2E', borderRadius: '10px', color: '#fff', fontSize: '13px', outline: 'none' }}
        />
        <button
          onClick={sendMessage}
          disabled={loading || !input.trim()}
          style={{ padding: '10px 16px', background: '#3ECF8E', border: 'none', borderRadius: '10px', cursor: loading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 700, color: '#000', opacity: loading || !input.trim() ? 0.5 : 1 }}
        >
          <Send size={16} />
        </button>
      </div>
      <style>{`@keyframes bounce { 0%, 80%, 100% { transform: translateY(0); } 40% { transform: translateY(-6px); } }`}</style>
    </div>
  );
}

// ── Main Panel (ISSUE 2: Right-side slide panel replacing centered modal) ─────
interface TestAgentModalProps {
  agent?: any;
  agentId?: string;
  agentName?: string;
  defaultMode?: Mode;
  inline?: boolean;
  onClose?: () => void;
}

export default function TestAgentModal({ agent, agentId: directId, agentName: directName, defaultMode, inline, onClose }: TestAgentModalProps) {
  const agentId = agent?.id || directId;
  const agentName = agent?.name || agent?.agent_name || directName || 'Agent';
  const clinicName = agent?.clinic_name || '';
  const storageKey = `lifodial-test-tab-${agentId}`;
  const [mode, setMode] = useState<Mode>(defaultMode || (localStorage.getItem(storageKey) as Mode) || 'chat');
  const [isOpen, setIsOpen] = useState(false);

  // Trigger slide-in on mount
  useEffect(() => {
    if (inline) {
      setIsOpen(true);
      return;
    }
    const t = setTimeout(() => setIsOpen(true), 10);
    return () => clearTimeout(t);
  }, [inline]);

  useEffect(() => { localStorage.setItem(storageKey, mode); }, [mode, storageKey]);

  // Escape closes panel (only if not inline)
  useEffect(() => {
    if (inline) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape' && onClose) onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose, inline]);

  // Badge pills for agent config info
  const language = agent?.tts_language || agent?.language || '';
  const llmModel = agent?.llm_model || '';
  const ttsVoice = agent?.tts_voice || '';

  return (
    <>
      {/* ISSUE 2: NO full backdrop — agent page stays visible */}

      {/* ISSUE 2: Right-side slide-in panel (or inline container) */}
      <div
        style={inline ? {
          width: '100%',
          height: '100%',
          minHeight: '600px',
          background: '#0f0f0f',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: '12px',
          overflow: 'hidden',
          border: '1px solid rgba(255,255,255,0.08)'
        } : {
          position: 'fixed',
          right: 0,
          top: 0,
          width: '420px',
          height: '100vh',
          background: '#0f0f0f',
          borderLeft: '1px solid rgba(255,255,255,0.08)',
          zIndex: 50,
          display: 'flex',
          flexDirection: 'column',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.3s ease',
          boxShadow: '-8px 0 32px rgba(0,0,0,0.5)',
        }}
      >
        {/* HEADER — sticky 64px */}
        <div style={{
          padding: '0 16px',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          display: 'flex',
          flexDirection: 'column',
          background: '#0a0a0a',
          flexShrink: 0,
        }}>
          {/* Top row: icon + name + close */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', height: '56px' }}>
            <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(62,207,142,0.1)', border: '1px solid #3ECF8E30', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Headphones size={17} color="#3ECF8E" />
            </div>
            <div style={{ flex: 1, overflow: 'hidden' }}>
              <p style={{ color: '#fff', fontWeight: 700, margin: 0, fontSize: '14px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{agentName}</p>
              {clinicName && <p style={{ color: '#555', fontSize: '11px', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{clinicName}</p>}
            </div>
            <button
              onClick={onClose}
              style={{ background: 'none', border: '1px solid #2e2e2e', color: '#666', cursor: 'pointer', padding: '5px', display: 'flex', alignItems: 'center', borderRadius: '6px', transition: 'all 0.15s', flexShrink: 0 }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#ef4444'; (e.currentTarget as HTMLButtonElement).style.color = '#ef4444'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#2e2e2e'; (e.currentTarget as HTMLButtonElement).style.color = '#666'; }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Tab row + status badges */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', paddingBottom: '10px' }}>
            <div style={{ display: 'flex', background: '#1A1A1A', borderRadius: '7px', padding: '2px', border: '1px solid #2E2E2E' }}>
              <button
                onClick={() => setMode('voice')}
                style={{ padding: '4px 12px', borderRadius: '5px', border: 'none', cursor: 'pointer', fontSize: '11px', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '4px', background: mode === 'voice' ? '#2E2E2E' : 'transparent', color: mode === 'voice' ? '#fff' : '#555', transition: 'all 0.15s' }}
              >
                <Mic size={11} /> Voice
              </button>
              <button
                onClick={() => setMode('chat')}
                style={{ padding: '4px 12px', borderRadius: '5px', border: 'none', cursor: 'pointer', fontSize: '11px', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '4px', background: mode === 'chat' ? '#2E2E2E' : 'transparent', color: mode === 'chat' ? '#fff' : '#555', transition: 'all 0.15s' }}
              >
                <MessageSquare size={11} /> Chat
              </button>
            </div>

            {/* Status badges */}
            <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', flex: 1 }}>
              {language && <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '10px', background: 'rgba(62,207,142,0.08)', border: '1px solid #3ECF8E20', color: '#3ECF8E', fontWeight: 600 }}>{language}</span>}
              {llmModel && <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '10px', background: 'rgba(96,165,250,0.08)', border: '1px solid #60A5FA20', color: '#60A5FA', fontWeight: 600 }}>{llmModel.replace('gemini-', 'g-').replace('-versatile', '')}</span>}
              {ttsVoice && <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '10px', background: 'rgba(167,139,250,0.08)', border: '1px solid #A78BFA20', color: '#A78BFA', fontWeight: 600 }}>{ttsVoice}</span>}
            </div>
          </div>
        </div>

        {/* CONTENT AREA — flex-grow, scrollable */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {mode === 'voice' ? (
            <VoiceMode agent={agent} agentId={agentId} agentName={agentName} onClose={onClose} />
          ) : (
            <ChatMode agent={agent} agentId={agentId} agentName={agentName} onClose={onClose} />
          )}
        </div>
      </div>
    </>
  );
}
