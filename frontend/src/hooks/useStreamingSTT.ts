/**
 * useStreamingSTT.ts
 * React hook for real-time speech-to-text transcription via Sarvam streaming API
 * WebSocket-based with auto-reconnection and error handling
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { wsUrlWithAuth } from '../api/client';

interface STTMessage {
  type: 'speech_start' | 'speech_end' | 'transcript' | 'translation' | 'error' | 'ready' | 'pong';
  text?: string;
  confidence?: number;
  message?: string;
  code?: string;
  language_code?: string;
  mode?: string;
  timestamp?: number;
}

interface UseStreamingSTTOptions {
  tenantId: string;
  agentId: string;
  languageCode?: string;
  mode?: 'transcribe' | 'translate' | 'verbatim' | 'translit' | 'codemix';
  sampleRate?: number;
  onTranscript?: (text: string, confidence?: number) => void;
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
  onError?: (error: string) => void;
}

export function useStreamingSTT(options: UseStreamingSTTOptions) {
  const {
    tenantId,
    agentId,
    languageCode = 'en-IN',
    mode = 'transcribe',
    sampleRate = 16000,
    onTranscript,
    onSpeechStart,
    onSpeechEnd,
    onError,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [lastTranscript, setLastTranscript] = useState('');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Connect to streaming STT WebSocket
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const wsUrl = wsUrlWithAuth(`/ws/streaming-stt/${tenantId}/${agentId}`);

    try {
      wsRef.current = new WebSocket(wsUrl);

      wsRef.current.onopen = () => {
        console.log('✅ Connected to streaming STT');
        setIsConnected(true);

        // Send config
        wsRef.current!.send(
          JSON.stringify({
            type: 'config',
            language_code: languageCode,
            mode,
            sample_rate: sampleRate,
          })
        );

        // Start ping to keep alive
        pingIntervalRef.current = setInterval(() => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
      };

      wsRef.current.onmessage = (event) => {
        try {
          const msg: STTMessage = JSON.parse(event.data);

          switch (msg.type) {
            case 'ready':
              console.log('STT ready:', msg.language_code, msg.mode);
              break;
            case 'speech_start':
              setIsListening(true);
              onSpeechStart?.();
              break;
            case 'speech_end':
              setIsListening(false);
              onSpeechEnd?.();
              break;
            case 'transcript':
              setLastTranscript(msg.text || '');
              onTranscript?.(msg.text || '', msg.confidence);
              break;
            case 'translation':
              setLastTranscript(msg.text || '');
              onTranscript?.(msg.text || '', msg.confidence);
              break;
            case 'error':
              const errMsg = msg.message || 'STT error';
              console.error('STT error:', errMsg);
              onError?.(errMsg);
              break;
            case 'pong':
              break;
          }
        } catch (e) {
          console.error('Failed to parse STT message:', e);
        }
      };

      wsRef.current.onerror = (error) => {
        console.error('STT WebSocket error:', error);
        onError?.('WebSocket connection error');
        setIsConnected(false);
      };

      wsRef.current.onclose = () => {
        console.log('STT WebSocket closed');
        setIsConnected(false);
        setIsListening(false);

        // Attempt reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(() => {
          console.log('Attempting STT reconnect...');
          connect();
        }, 3000);
      };
    } catch (error) {
      console.error('Failed to create STT WebSocket:', error);
      onError?.(String(error));
    }
  }, [tenantId, agentId, languageCode, mode, sampleRate, onTranscript, onSpeechStart, onSpeechEnd, onError]);

  // Disconnect and cleanup
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    setIsConnected(false);
    setIsListening(false);
  }, []);

  // Send audio chunk for transcription
  const sendAudio = useCallback(async (audioBytes: Uint8Array) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('STT WebSocket not ready');
      return false;
    }

    try {
      // Convert Uint8Array to base64
      const binary = String.fromCharCode.apply(null, Array.from(audioBytes));
      const b64 = btoa(binary);

      wsRef.current.send(
        JSON.stringify({
          type: 'audio',
          audio: b64,
          encoding: 'audio/wav',
          sample_rate: sampleRate,
        })
      );
      return true;
    } catch (error) {
      console.error('Failed to send audio:', error);
      return false;
    }
  }, [sampleRate]);

  // Flush the buffer to force immediate processing
  const flush = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'flush' }));
    }
  }, []);

  // Auto-connect on mount
  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  return {
    isConnected,
    isListening,
    lastTranscript,
    sendAudio,
    flush,
    connect,
    disconnect,
  };
}
