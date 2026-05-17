/**
 * useThinkingSound.ts
 *
 * Plays a subtle looping audio cue while the voice pipeline is processing
 * (STT → LLM → TTS). Stops instantly when the agent's response arrives.
 *
 * Generates sound SYNTHETICALLY via Web Audio API — no audio files,
 * no network requests, no autoplay restrictions.
 *
 * Usage:
 *   const { startThinking, stopThinking } = useThinkingSound('ping');
 *   startThinking();  // call when user stops speaking / audio is sent to backend
 *   stopThinking();   // call when first audio chunk arrives from backend
 *
 * Sound styles:
 *   'ping'   — Soft 880Hz sine chime every 2.2s  (recommended for medical)
 *   'typing' — Rapid soft click bursts            (office feel)
 *   'breath' — Barely-audible low ambient pad     (most subtle)
 */
import { useCallback, useEffect, useRef } from 'react';

export type ThinkingSoundStyle = 'ping' | 'typing' | 'breath';

interface UseThinkingSoundOptions {
  volume?: number;          // master gain 0–1, default 0.07
  pingIntervalMs?: number;  // interval between pings, default 2200ms
}

export function useThinkingSound(
  style: ThinkingSoundStyle = 'ping',
  options: UseThinkingSoundOptions = {},
) {
  const { volume = 0.07, pingIntervalMs = 2200 } = options;

  // We store a stop-function for the currently running sound loop
  const stopFnRef = useRef<(() => void) | null>(null);
  // Shared AudioContext — created lazily on first startThinking() call
  const ctxRef = useRef<AudioContext | null>(null);

  /** Lazily get-or-create AudioContext. */
  function getCtx(): AudioContext {
    if (!ctxRef.current || ctxRef.current.state === 'closed') {
      ctxRef.current = new AudioContext();
    }
    return ctxRef.current;
  }

  // ── PING style ─────────────────────────────────────────────────────────────
  function startPing(ctx: AudioContext): () => void {
    let cancelled = false;

    const masterGain = ctx.createGain();
    masterGain.gain.value = volume;
    masterGain.connect(ctx.destination);

    /** Schedule one soft ping chime starting at AudioContext time `t`. */
    function schedulePing(t: number) {
      if (cancelled) return;

      // Oscillator — sine wave at A5 (880Hz): soft, not harsh
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = 880;

      // Amplitude envelope: fast attack, gentle exponential decay
      const env = ctx.createGain();
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(1.0, t + 0.04);   // 40ms attack
      env.gain.exponentialRampToValueAtTime(0.001, t + 0.55); // 550ms decay

      osc.connect(env);
      env.connect(masterGain);

      osc.start(t);
      osc.stop(t + 0.6);

      // Schedule next ping
      const nextT = t + pingIntervalMs / 1000;
      const delayMs = (nextT - ctx.currentTime) * 1000;
      setTimeout(() => schedulePing(ctx.currentTime), Math.max(0, delayMs));
    }

    // Start first ping immediately (small offset to avoid click)
    schedulePing(ctx.currentTime + 0.02);

    return () => {
      cancelled = true;
      masterGain.gain.setTargetAtTime(0, ctx.currentTime, 0.05);
      setTimeout(() => {
        try { masterGain.disconnect(); } catch (_) {}
      }, 300);
    };
  }

  // ── TYPING style ───────────────────────────────────────────────────────────
  function startTyping(ctx: AudioContext): () => void {
    let cancelled = false;
    const masterGain = ctx.createGain();
    masterGain.gain.value = volume * 0.8;
    masterGain.connect(ctx.destination);

    /** Generate one short noise click (simulates a keypress). */
    function scheduleClick(t: number) {
      if (cancelled) return;

      const bufSize = Math.floor(ctx.sampleRate * 0.025); // 25ms of noise
      const buf = ctx.createBuffer(1, bufSize, ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < bufSize; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.exp(-i / (bufSize * 0.3));
      }

      const src = ctx.createBufferSource();
      src.buffer = buf;

      // Low-pass filter to make it sound like a keyboard, not static
      const lp = ctx.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.value = 4000;

      const env = ctx.createGain();
      env.gain.setValueAtTime(0.6, t);
      env.gain.exponentialRampToValueAtTime(0.001, t + 0.025);

      src.connect(lp);
      lp.connect(env);
      env.connect(masterGain);
      src.start(t);
    }

    /** Schedule a burst of 3–5 clicks (one "word" being typed). */
    function scheduleBurst() {
      if (cancelled) return;
      const clickCount = 3 + Math.floor(Math.random() * 3); // 3–5
      const now = ctx.currentTime + 0.01;
      for (let i = 0; i < clickCount; i++) {
        scheduleClick(now + i * (0.07 + Math.random() * 0.04));
      }
      // Next burst in 600–1000ms
      const nextMs = 600 + Math.random() * 400;
      setTimeout(scheduleBurst, nextMs);
    }

    scheduleBurst();

    return () => {
      cancelled = true;
      masterGain.gain.setTargetAtTime(0, ctx.currentTime, 0.03);
      setTimeout(() => {
        try { masterGain.disconnect(); } catch (_) {}
      }, 200);
    };
  }

  // ── BREATH style ───────────────────────────────────────────────────────────
  function startBreath(ctx: AudioContext): () => void {
    let cancelled = false;
    const masterGain = ctx.createGain();
    masterGain.gain.value = 0;
    masterGain.connect(ctx.destination);

    // Fade in gently
    masterGain.gain.setTargetAtTime(volume * 0.6, ctx.currentTime, 0.4);

    // White noise source
    const bufSize = ctx.sampleRate * 2; // 2s of noise
    const buf = ctx.createBuffer(1, bufSize, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < bufSize; i++) data[i] = Math.random() * 2 - 1;

    const noise = ctx.createBufferSource();
    noise.buffer = buf;
    noise.loop = true;

    // Low-pass filter → "breath" texture
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 200;
    lp.Q.value = 0.5;

    // LFO to pulse the volume slowly (0.15 Hz ~ one "breath" every 6.5s)
    const lfo = ctx.createOscillator();
    lfo.type = 'sine';
    lfo.frequency.value = 0.15;
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 0.3;
    lfo.connect(lfoGain);
    lfoGain.connect(masterGain.gain);

    noise.connect(lp);
    lp.connect(masterGain);

    noise.start();
    lfo.start();

    return () => {
      cancelled = true;
      masterGain.gain.setTargetAtTime(0, ctx.currentTime, 0.15);
      setTimeout(() => {
        try { noise.stop(); lfo.stop(); masterGain.disconnect(); } catch (_) {}
      }, 600);
    };
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  const startThinking = useCallback(() => {
    // Stop any previously running sound first
    if (stopFnRef.current) {
      stopFnRef.current();
      stopFnRef.current = null;
    }

    try {
      const ctx = getCtx();
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }

      let stopFn: () => void;
      switch (style) {
        case 'typing': stopFn = startTyping(ctx); break;
        case 'breath': stopFn = startBreath(ctx); break;
        case 'ping':
        default:       stopFn = startPing(ctx);  break;
      }
      stopFnRef.current = stopFn;
    } catch (e) {
      // AudioContext may not be available (e.g. server-side render) — fail silently
      console.debug('[useThinkingSound] could not start:', e);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [style, volume, pingIntervalMs]);

  const stopThinking = useCallback(() => {
    if (stopFnRef.current) {
      stopFnRef.current();
      stopFnRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (stopFnRef.current) {
        stopFnRef.current();
        stopFnRef.current = null;
      }
      // Don't close the AudioContext here — the call widget may be reusing it
    };
  }, []);

  return { startThinking, stopThinking };
}
