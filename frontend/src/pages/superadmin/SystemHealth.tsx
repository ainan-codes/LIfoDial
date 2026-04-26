import { AlertTriangle, CheckCircle, Database, RefreshCw, Server, WifiOff } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { API_URL } from '../../api/client';

// ── Types ────────────────────────────────────────────────────────────────────
interface HealthStatus {
  database?: { status: string; latency_ms?: number; type?: string; tenant_count?: number; appointment_count?: number; error?: string };
  environment?: string;
  timestamp?: string;
  // Service key statuses — 'connected' | 'missing_key'
  gemini?: string;
  sarvam?: string;
  livekit?: string;
  vobiz?: string;
  oxzygen?: string;
  groq?: string;
  elevenlabs?: string;
}

interface ConfigCheck {
  label: string;
  description: string;
  status: 'ok' | 'warn' | 'error' | 'unknown';
}

// ── Service Card ─────────────────────────────────────────────────────────────
function ServiceCard({ label, status, detail, latency, extra }: {
  label: string;
  status: 'healthy' | 'degraded' | 'error' | 'unknown';
  detail: string;
  latency?: string | number;
  extra?: string;
}) {
  const colors = { healthy: '#10B981', degraded: '#F59E0B', error: '#EF4444', unknown: '#555' };
  const color = colors[status];
  const icons = { healthy: CheckCircle, degraded: AlertTriangle, error: AlertTriangle, unknown: WifiOff };
  const Icon = icons[status];

  return (
    <div style={{ backgroundColor: '#1A1A1A', border: `1px solid ${color}25`, borderRadius: '12px', padding: '20px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: '#fff', fontWeight: 600, fontSize: '14px' }}>{label}</span>
        <Icon size={16} color={color} />
      </div>
      <span style={{ color: '#888', fontSize: '12px' }}>{detail}</span>
      {latency && (
        <span style={{ color, fontSize: '20px', fontWeight: 700, fontFamily: 'monospace' }}>
          {typeof latency === 'number' ? `${latency}ms` : latency}
        </span>
      )}
      {extra && <span style={{ color: '#555', fontSize: '11px' }}>{extra}</span>}
      <div style={{ height: '3px', borderRadius: '2px', backgroundColor: '#0F0F0F', overflow: 'hidden', marginTop: '4px' }}>
        <div style={{ height: '100%', width: status === 'healthy' ? '100%' : status === 'degraded' ? '60%' : '20%', backgroundColor: color, transition: 'width 1s ease' }} />
      </div>
    </div>
  );
}

// ── Config Checklist ──────────────────────────────────────────────────────────
function ConfigRow({ item }: { item: ConfigCheck }) {
  const icon = item.status === 'ok' ? '✅' : item.status === 'warn' ? '⚠️' : item.status === 'error' ? '❌' : '❓';
  const color = item.status === 'ok' ? '#10B981' : item.status === 'warn' ? '#F59E0B' : item.status === 'error' ? '#EF4444' : '#555';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '16px', padding: '12px 0', borderBottom: '1px solid #1A1A1A' }}>
      <span style={{ fontSize: '14px', flexShrink: 0 }}>{icon}</span>
      <div style={{ flex: 1 }}>
        <span style={{ color: '#fff', fontSize: '13px', fontWeight: 500 }}>{item.label}</span>
      </div>
      <span style={{ color, fontSize: '12px' }}>{item.description}</span>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function SASystemHealth() {
  const [health, setHealth] = useState<HealthStatus>({});
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [p50, setP50] = useState(742);

  // Live latency simulation
  useEffect(() => {
    const id = setInterval(() => setP50(l => Math.max(680, Math.min(900, l + (Math.random() * 20 - 10)))), 3000);
    return () => clearInterval(id);
  }, []);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/admin/health-status`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      setHealth(data);
    } catch {
      setHealth({ database: { status: 'unknown' }, environment: 'development' });
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, 30000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  const dbStatus = health.database?.status === 'healthy' ? 'healthy'
    : health.database?.status === 'error' ? 'error'
    : 'unknown';

  // Helper: map backend 'connected'|'missing_key' → checklist status
  const svcStatus = (v?: string): 'ok' | 'warn' => v === 'connected' ? 'ok' : 'warn';
  const svcDesc   = (v?: string, label?: string) =>
    v === 'connected' ? `${label ?? 'Key'} detected ✓` : `Set in environment`;

  const configChecks: ConfigCheck[] = [
    { label: 'Database', description: health.database?.status === 'healthy' ? `${health.database.type} — ${health.database.latency_ms}ms` : 'Not connected', status: dbStatus === 'healthy' ? 'ok' : 'error' },
    { label: 'Session Store', description: 'In-memory (Redis not required in dev)', status: 'ok' },
    { label: 'Gemini LLM',          description: svcDesc(health.gemini, 'GEMINI_API_KEY'),         status: svcStatus(health.gemini) },
    { label: 'Sarvam STT/TTS',      description: svcDesc(health.sarvam, 'SARVAM_API_KEY'),         status: svcStatus(health.sarvam) },
    { label: 'LiveKit Voice',        description: svcDesc(health.livekit, 'LIVEKIT_API_KEY'),       status: svcStatus(health.livekit) },
    { label: 'Telephony (Vobiz)',    description: svcDesc(health.vobiz, 'VOBIZ_ACCOUNT_SID'),      status: svcStatus(health.vobiz) },
    { label: 'HIS Integration (Oxzygen)', description: svcDesc(health.oxzygen, 'OXZYGEN_API_KEY'), status: svcStatus(health.oxzygen) },
  ];

  return (
    <div style={{ padding: '32px', height: '100%', overflowY: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '28px' }}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>System Health</h1>
          <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>
            Real-time infrastructure monitoring · Auto-refreshes every 30s
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <span style={{ color: '#555', fontSize: '12px' }}>
            Last updated: {lastRefresh.toLocaleTimeString('en-IN')}
          </span>
          {dbStatus === 'healthy' ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#10B981', animation: 'pulse 2s infinite' }} />
              <span style={{ color: '#10B981', fontSize: '13px', fontWeight: 600 }}>All Systems Operational</span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#F59E0B' }} />
              <span style={{ color: '#F59E0B', fontSize: '13px', fontWeight: 600 }}>Checking...</span>
            </div>
          )}
          <button
            onClick={fetchHealth}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '8px 14px', color: '#888', fontSize: '13px', cursor: 'pointer' }}
          >
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </div>

      {/* Service Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '16px', marginBottom: '28px' }}>
        <ServiceCard
          label="Database"
          status={dbStatus}
          detail={health.database?.type ?? 'SQLite'}
          latency={health.database?.latency_ms}
          extra={health.database?.tenant_count !== undefined ? `${health.database.tenant_count} tenants · ${health.database.appointment_count} appointments` : undefined}
        />
        <ServiceCard label="Agent API" status="healthy" detail={`FastAPI · ${health.environment ?? 'development'}`} latency={`~${Math.round(p50)}ms`} extra="Live latency (simulated)" />
        <ServiceCard label="Session Store" status="healthy" detail="In-memory (dev mode)" latency="<1ms" extra="No Redis needed locally" />
        <ServiceCard
          label="LiveKit"
          status={health.livekit === 'connected' ? 'healthy' : loading ? 'unknown' : 'degraded'}
          detail={health.livekit === 'connected' ? 'Keys configured ✓' : 'Set LIVEKIT_URL + KEY in env'}
          extra="Voice infrastructure"
        />
        <ServiceCard
          label="Sarvam AI"
          status={health.sarvam === 'connected' ? 'healthy' : loading ? 'unknown' : 'degraded'}
          detail={health.sarvam === 'connected' ? 'API key detected ✓' : 'Set SARVAM_API_KEY in env'}
          extra="STT + TTS provider"
        />
        <ServiceCard
          label="Gemini LLM"
          status={health.gemini === 'connected' ? 'healthy' : loading ? 'unknown' : 'degraded'}
          detail={health.gemini === 'connected' ? 'API key detected ✓' : 'Set GEMINI_API_KEY in env'}
          extra="LLM backbone"
        />
        <ServiceCard
          label="Vobiz Telephony"
          status={health.vobiz === 'connected' ? 'healthy' : 'unknown'}
          detail={health.vobiz === 'connected' ? 'Account SID detected ✓' : 'Set VOBIZ_* in env'}
          extra="SIP telephony"
        />
      </div>

      {/* Config Checklist + DB Stats side by side */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '24px' }}>
        {/* Config Checklist */}
        <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px' }}>
          <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '16px', margin: '0 0 16px' }}>
            AI Configuration Checklist
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {configChecks.map(c => <ConfigRow key={c.label} item={c} />)}
          </div>
        </div>

        {/* DB Stats */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px' }}>
            <h3 style={{ fontSize: '13px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Database size={15} color="#888" /> Database Stats
            </h3>
            {health.database?.status === 'healthy' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {[
                  { label: 'Tenants', value: health.database.tenant_count?.toString() ?? '0' },
                  { label: 'Appointments', value: health.database.appointment_count?.toString() ?? '0' },
                  { label: 'Latency', value: `${health.database.latency_ms}ms` },
                  { label: 'Engine', value: health.database.type ?? 'SQLite' },
                ].map(({ label, value }) => (
                  <div key={label} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: '#888', fontSize: '12px' }}>{label}</span>
                    <span style={{ color: '#fff', fontFamily: 'monospace', fontSize: '13px', fontWeight: 600 }}>{value}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: '#555', fontSize: '13px' }}>Database health data unavailable</p>
            )}
          </div>

          <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px' }}>
            <h3 style={{ fontSize: '13px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Server size={15} color="#888" /> Environment
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#888', fontSize: '12px' }}>Mode</span>
                <code style={{ backgroundColor: '#0F0F0F', color: '#3ECF8E', padding: '2px 8px', borderRadius: '4px', fontSize: '12px' }}>{health.environment ?? 'development'}</code>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#888', fontSize: '12px' }}>API Base</span>
                <code style={{ backgroundColor: '#0F0F0F', color: '#60a5fa', padding: '2px 8px', borderRadius: '4px', fontSize: '12px' }}>{API_URL}</code>
              </div>
              {health.timestamp && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#888', fontSize: '12px' }}>Server Time</span>
                  <span style={{ color: '#555', fontSize: '11px', fontFamily: 'monospace' }}>{new Date(health.timestamp).toLocaleTimeString('en-IN')}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
    </div>
  );
}
