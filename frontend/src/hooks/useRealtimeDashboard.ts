import { useEffect, useRef, useState } from 'react';
import { wsUrlWithAuth } from '../api/client';

interface Booking {
  id: string;
  patient_phone: string;
  patient_name?: string;
  phone?: string;
  clinic_name?: string;
  doctor?: string;
  slot_time: string;
}

export function useRealtimeDashboard(tenantId: string) {
  const [liveCallCount, setLiveCallCount] = useState(0);
  const [lastBooking, setLastBooking] = useState<Booking | null>(null);
  const [agentStatus, setAgentStatus] = useState<'online' | 'offline' | 'unknown'>('unknown');
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!tenantId) return;

    function connect() {
      const ws = new WebSocket(wsUrlWithAuth(`/ws/calls/${tenantId}`));
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setAgentStatus('online');
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          switch (data.type) {
            case 'connected':
              setLiveCallCount(data.active_calls || 0);
              setAgentStatus('online');
              break;
            case 'call.active_count':
              setLiveCallCount(data.active_calls || 0);
              break;
            case 'call.started':
              setLiveCallCount((c) => c + 1);
              break;
            case 'call.ended':
              setLiveCallCount((c) => Math.max(0, c - 1));
              break;
            case 'booking.created':
              setLastBooking(data.booking);
              break;
            case 'agent.status':
              setAgentStatus(data.status || 'online');
              break;
            case 'heartbeat':
            case 'ping':
              // keepalive
              break;
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onerror = () => {
        setAgentStatus('offline');
        setIsConnected(false);
      };

      ws.onclose = () => {
        setAgentStatus('offline');
        setIsConnected(false);
        // Reconnect after 5 seconds
        reconnectRef.current = setTimeout(connect, 5000);
      };
    }

    connect();

    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [tenantId]);

  return { liveCallCount, lastBooking, agentStatus, isConnected };
}

export default useRealtimeDashboard;
