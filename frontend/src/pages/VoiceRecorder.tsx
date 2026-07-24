import React, { useState, useEffect, useRef } from 'react';
import { Mic, Square, RefreshCw, CheckCircle2 } from 'lucide-react';
import { API_URL } from '../api/client';

const TENANT_ID = 'e0f46c3b-d336-411a-85d1-13c5f516a7f0';

export default function VoiceRecorder() {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [uploaded, setUploaded] = useState(false);
  const [bars, setBars] = useState<number[]>(Array(48).fill(3));
  const [voiceStatus, setVoiceStatus] = useState<'default' | 'custom'>('default');

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    fetch(`${API_URL}/api/tenants/${TENANT_ID}/voice-status`)
      .then(r => r.json())
      .then(d => { if (d.status) setVoiceStatus(d.status); })
      .catch(() => {/* API offline, stay at default */});
  }, []);

  useEffect(() => {
    let t: ReturnType<typeof setInterval>;
    if (recording) {
      t = setInterval(() => {
        setBars(Array(48).fill(0).map(() => 3 + Math.random() * 42));
      }, 80);
    } else {
      setBars(Array(48).fill(3));
    }
    return () => clearInterval(t);
  }, [recording]);

  const convertToWav = async (webmBlob: Blob): Promise<Blob> => {
    const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)({ sampleRate: 16000 });
    const arrayBuffer = await webmBlob.arrayBuffer();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    const numOfChan = 1;
    const length = audioBuffer.length * numOfChan * 2 + 44;
    const buffer = new ArrayBuffer(length);
    const view = new DataView(buffer);
    let pos = 0, offset = 0;
    const setUint16 = (d: number) => { view.setUint16(pos, d, true); pos += 2; };
    const setUint32 = (d: number) => { view.setUint32(pos, d, true); pos += 4; };
    setUint32(0x46464952); setUint32(length - 8); setUint32(0x45564157);
    setUint32(0x20746d66); setUint32(16); setUint16(1); setUint16(numOfChan);
    setUint32(audioBuffer.sampleRate); setUint32(audioBuffer.sampleRate * 2);
    setUint16(2); setUint16(16); setUint32(0x61746164); setUint32(length - pos - 4);
    const ch = audioBuffer.getChannelData(0);
    while (offset < audioBuffer.length) {
      let s = Math.max(-1, Math.min(1, ch[offset]));
      s = (0.5 + s < 0 ? s * 32768 : s * 32767) | 0;
      view.setInt16(pos, s, true); pos += 2; offset++;
    }
    return new Blob([buffer], { type: 'audio/wav' });
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mr;
      audioChunksRef.current = [];
      mr.ondataavailable = e => audioChunksRef.current.push(e.data);
      mr.onstop = async () => {
        setProcessing(true);
        try {
          const wav = await convertToWav(new Blob(audioChunksRef.current, { type: 'audio/webm' }));
          await uploadVoice(wav);
        } catch (err) {
          console.error('Voice processing error', err);
        } finally {
          setProcessing(false);
          stream.getTracks().forEach(t => t.stop());
        }
      };
      mr.start();
      setRecording(true);
      setUploaded(false);
    } catch (err) {
      console.error('Mic access denied', err);
      alert('Please allow microphone access to record custom voice.');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && recording) {
      mediaRecorderRef.current.stop();
      setRecording(false);
    }
  };

  const uploadVoice = async (wavBlob: Blob) => {
    const fd = new FormData();
    fd.append('file', wavBlob, 'custom_voice.wav');
    try {
      const res = await fetch(`${API_URL}/api/tenants/${TENANT_ID}/upload-voice`, { method: 'POST', body: fd });
      const data = await res.json();
      // Only claim a custom voice when the backend actually cloned one. When
      // cloning isn't available it returns 200 with status:"received" — surface
      // that honestly instead of showing a fake "Voice Cloned" success.
      if (res.ok && data.status === 'active') { setUploaded(true); setVoiceStatus('custom'); }
      else alert(data.message || data.detail || 'Upload failed');
    } catch { alert('Network error during upload'); }
  };

  return (
    <div data-testid="voice-recorder-page" className="h-full flex flex-col">
      {/* Header */}
      <div
        className="px-8 py-5 flex items-center justify-between flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      >
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em', margin: 0 }}>
            Voice Clone
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
            Record a 10s sample to clone your AI's voice via Sarvam TTS
          </p>
        </div>
        {/* Voice status badge */}
        <div
          className="flex items-center gap-2"
          style={{
            padding: '4px 12px',
            borderRadius: '9999px',
            backgroundColor: voiceStatus === 'custom' ? 'var(--accent-dim)' : 'var(--warning-dim)',
            border: `1px solid ${voiceStatus === 'custom' ? 'var(--accent-border)' : 'rgba(251,191,36,0.3)'}`,
          }}
        >
          <div
            className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: voiceStatus === 'custom' ? 'var(--accent)' : 'var(--warning)' }}
          />
          <span style={{ fontSize: '12px', fontWeight: 600, color: voiceStatus === 'custom' ? 'var(--accent)' : 'var(--warning)' }}>
            {voiceStatus === 'custom' ? 'Custom Voice Active' : 'Default Voice'}
          </span>
        </div>
      </div>

      <div
        className="flex-1 flex items-center justify-center p-8"
        style={{ backgroundColor: 'var(--bg-page)' }}
      >
        <div className="w-full space-y-4" style={{ maxWidth: '480px' }}>
          {/* Recorder card */}
          <div
            className="rounded-xl p-8 flex flex-col items-center gap-6"
            style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}
          >
            {/* Recording badge */}
            <div
              className="flex items-center gap-2 transition-opacity"
              style={{
                padding: '4px 12px',
                borderRadius: '9999px',
                backgroundColor: 'var(--destructive-dim)',
                border: '1px solid rgba(248,113,113,0.3)',
                opacity: recording ? 1 : 0,
              }}
            >
              <div className="w-1.5 h-1.5 rounded-full dot-pulse" style={{ backgroundColor: 'var(--destructive)' }} />
              <span style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--destructive)' }}>
                Recording…
              </span>
            </div>

            {/* Waveform */}
            <div className="flex items-center justify-center gap-0.5 overflow-hidden" style={{ height: '56px', width: '100%', padding: '0 16px' }}>
              {bars.map((h, i) => (
                <div
                  key={i}
                  className="flex-1 rounded-full transition-all duration-75"
                  style={{
                    height: `${h}px`,
                    maxWidth: '4px',
                    backgroundColor: recording ? 'var(--destructive)' : 'var(--bg-surface-3)',
                    opacity: recording ? 0.7 + (h / 45) * 0.3 : 1,
                  }}
                />
              ))}
            </div>

            {/* Record button */}
            <button
              onClick={recording ? stopRecording : startRecording}
              disabled={processing}
              className="flex items-center justify-center transition-all"
              style={{
                width: '80px',
                height: '80px',
                borderRadius: '50%',
                border: 'none',
                cursor: processing ? 'not-allowed' : 'pointer',
                backgroundColor: recording ? 'var(--destructive-dim)' : 'var(--accent)',
                outline: recording ? '2px solid var(--destructive)' : 'none',
                color: recording ? 'var(--destructive)' : '#000',
                opacity: processing ? 0.5 : 1,
                transform: 'scale(1)',
                transition: 'transform 0.15s, background-color 0.15s',
              }}
              onMouseEnter={e => { if (!processing) (e.currentTarget as HTMLElement).style.transform = 'scale(1.05)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)'; }}
            >
              {processing ? (
                <RefreshCw className="animate-spin" size={28} />
              ) : recording ? (
                <Square fill="currentColor" size={28} />
              ) : (
                <Mic fill="currentColor" size={30} />
              )}
            </button>

            {/* Status text */}
            <p style={{ fontSize: '13px', fontWeight: 500, color: uploaded ? 'var(--accent)' : 'var(--text-secondary)' }}>
              {processing ? 'Processing & Cloning Voice…'
                : uploaded ? '✓ Voice Cloned Successfully!'
                : recording ? 'Click to stop and upload'
                : 'Click to start recording'}
            </p>
          </div>

          {/* Info card */}
          <div
            className="rounded-xl p-5"
            style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}
          >
            <p style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
              Tips for best results
            </p>
            {[
              'Speak clearly for at least 10 seconds',
              'Record in a quiet environment',
              'Say a natural sentence — not just words',
            ].map((tip, i) => (
              <div key={i} className="flex items-start gap-2.5 mb-2">
                <CheckCircle2 size={14} style={{ color: 'var(--accent)', marginTop: '2px', flexShrink: 0 }} />
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{tip}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
