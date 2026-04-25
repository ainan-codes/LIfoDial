import React, { useEffect, useState, useCallback } from 'react';

import { API_URL } from '../../../api/client';
const API = API_URL;

interface HealthData {
  agent_id: string;
  agent_name: string;
  status: 'healthy' | 'degraded' | 'slow';
  last_24h: {
    total_calls: number;
    successful: number;
    failed: number;
    transferred: number;
  };
  latency: {
    avg_ms: number | null;
    target_ms: number;
    on_target: boolean;
    sample_size: number;
  };
  evaluation_stats_7d: {
    total_calls: number;
    calls_booked: number;
    booking_success_rate: number;
    sentiment_breakdown: { positive: number; neutral: number; negative: number };
    outcome_breakdown: Record<string, number>;
  };
  simulation_score: number | null;
}

interface LatencyStats {
  data_available: boolean;
  avg_latency_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  target_ms: number;
  status: 'on_target' | 'above_target';
  sample_size: number;
  breakdown: { stt_avg: number; llm_avg: number; tts_avg: number };
  message?: string;
}

interface Props {
  agentId: string;
}

const StatusDot: React.FC<{ status: string }> = ({ status }) => {
  const colors: Record<string, string> = {
    healthy: '#22c55e',
    degraded: '#ef4444',
    slow: '#f59e0b',
    on_target: '#22c55e',
    above_target: '#f59e0b',
  };
  return (
    <span style={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      backgroundColor: colors[status] || '#6b7280',
      boxShadow: `0 0 6px ${colors[status] || '#6b7280'}88`,
      marginRight: 6,
    }} />
  );
};

const MetricCard: React.FC<{
  icon: string; label: string; value: string | number;
  sub?: string; color?: string;
}> = ({ icon, label, value, sub, color }) => (
  <div style={{
    backgroundColor: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 12, padding: '16px',
  }}>
    <div style={{ fontSize: 22, marginBottom: 6 }}>{icon}</div>
    <div style={{ fontWeight: 700, fontSize: 22, color: color || '#fff' }}>{value}</div>
    <div style={{ fontSize: 12, fontWeight: 600, marginTop: 2 }}>{label}</div>
    {sub && <div style={{ fontSize: 11, opacity: 0.5, marginTop: 4 }}>{sub}</div>}
  </div>
);

const ProgressBar: React.FC<{ value: number; max: number; color?: string; showLabel?: boolean }> = ({
  value, max, color = '#6366f1', showLabel = true,
}) => {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        flex: 1, height: 6, backgroundColor: 'rgba(255,255,255,0.1)',
        borderRadius: 3, overflow: 'hidden',
      }}>
        <div style={{
          height: '100%', width: `${pct}%`, backgroundColor: color,
          borderRadius: 3, transition: 'width 0.5s ease',
        }} />
      </div>
      {showLabel && (
        <span style={{ fontSize: 12, opacity: 0.7, minWidth: 36, textAlign: 'right' }}>
          {value}ms
        </span>
      )}
    </div>
  );
};

const AgentHealthTab: React.FC<Props> = ({ agentId }) => {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [latency, setLatency] = useState<LatencyStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, l] = await Promise.all([
        fetch(`${API}/agents/${agentId}/health`).then(r => r.json()),
        fetch(`${API}/agents/${agentId}/latency-stats?days=7`).then(r => r.json()),
      ]);
      setHealth(h);
      setLatency(l);
    } catch (e: any) {
      setError(e.message || 'Failed to load health data');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) return (
    <div style={{ textAlign: 'center', padding: 64, opacity: 0.5 }}>
      <div style={{ fontSize: 32, marginBottom: 12, animation: 'spin 1s linear infinite', display: 'inline-block' }}>⟳</div>
      <div>Loading health data…</div>
      <style>{`@keyframes spin { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }`}</style>
    </div>
  );

  if (error) return (
    <div style={{
      padding: '16px', borderRadius: 10, margin: '16px 0',
      backgroundColor: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)',
      color: '#fca5a5',
    }}>
      ❌ {error}
      <button onClick={fetchData} style={{ marginLeft: 12, padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', background: 'rgba(255,255,255,0.1)', border: 'none', color: 'inherit' }}>Retry</button>
    </div>
  );

  const h24 = health?.last_24h;
  const evalStats = health?.evaluation_stats_7d;
  const lat = health?.latency;
  const sentiment = evalStats?.sentiment_breakdown;

  const statusColor = {
    healthy: '#22c55e', degraded: '#ef4444', slow: '#f59e0b',
  }[health?.status || 'healthy'] || '#6b7280';

  return (
    <div style={{ paddingBottom: 32 }}>
      {/* Overall status banner */}
      <div style={{
        background: `linear-gradient(135deg, ${statusColor}18, transparent)`,
        border: `1px solid ${statusColor}44`,
        borderRadius: 12, padding: '16px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 20, flexWrap: 'wrap', gap: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <StatusDot status={health?.status || 'healthy'} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>
              Agent Health: {health?.agent_name || 'Unknown'}
            </div>
            <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>
              STATUS: <span style={{ color: statusColor, fontWeight: 600, textTransform: 'uppercase' }}>
                {health?.status || 'Unknown'}
              </span>
            </div>
          </div>
        </div>
        <button
          onClick={fetchData}
          style={{
            padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.15)',
            color: '#fff', cursor: 'pointer',
          }}
        >
          ↻ Refresh
        </button>
      </div>

      {/* Last 24h metrics */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, opacity: 0.6, marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Last 24 Hours
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px,1fr))', gap: 12 }}>
          <MetricCard icon="📞" label="Total Calls" value={h24?.total_calls ?? 0} />
          <MetricCard icon="✅" label="Successful" value={h24?.successful ?? 0} color="#22c55e" />
          <MetricCard icon="❌" label="Failed" value={h24?.failed ?? 0} color="#ef4444" />
          <MetricCard icon="↗" label="Transferred" value={h24?.transferred ?? 0} color="#f59e0b" />
        </div>
      </div>

      {/* Latency section */}
      <div style={{
        backgroundColor: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 12, padding: 20, marginBottom: 20,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>⚡ Latency (Last 7 Days)</div>
          {latency?.data_available && (
            <div style={{
              padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
              backgroundColor: latency.status === 'on_target'
                ? 'rgba(34,197,94,0.2)' : 'rgba(245,158,11,0.2)',
              color: latency.status === 'on_target' ? '#22c55e' : '#f59e0b',
            }}>
              {latency.status === 'on_target' ? '✅ On Target' : '⚠ Above Target'}
            </div>
          )}
        </div>

        {latency?.data_available ? (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 16 }}>
              {[
                { label: 'P50 (median)', val: latency.p50_ms },
                { label: 'P95', val: latency.p95_ms },
                { label: 'P99 (worst)', val: latency.p99_ms },
              ].map(({ label, val }) => (
                <div key={label} style={{ textAlign: 'center' }}>
                  <div style={{
                    fontWeight: 700, fontSize: 22,
                    color: val <= 800 ? '#22c55e' : val <= 1500 ? '#f59e0b' : '#ef4444',
                  }}>
                    {val}ms
                  </div>
                  <div style={{ fontSize: 11, opacity: 0.5 }}>{label}</div>
                </div>
              ))}
            </div>

            <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 8 }}>
              Breakdown (avg {latency.avg_latency_ms}ms · {latency.sample_size} calls · target {latency.target_ms}ms)
            </div>
            {[
              { label: 'STT (Sarvam)', val: latency.breakdown?.stt_avg },
              { label: 'LLM (Gemini)', val: latency.breakdown?.llm_avg },
              { label: 'TTS (Sarvam)', val: latency.breakdown?.tts_avg },
            ].map(({ label, val }) => (
              <div key={label} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                  <span>{label}</span>
                </div>
                <ProgressBar value={val ?? 0} max={latency.avg_latency_ms || 800} color="#6366f1" />
              </div>
            ))}
          </>
        ) : (
          <div style={{ textAlign: 'center', padding: '24px', opacity: 0.5, fontSize: 13 }}>
            {latency?.message || 'No latency data yet. Make web calls to populate this.'}
          </div>
        )}
      </div>

      {/* Call Evaluation Stats (7 days) */}
      {evalStats && (
        <div style={{
          backgroundColor: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 12, padding: 20, marginBottom: 20,
        }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 16 }}>
            📈 Call Stats (Last 7 Days)
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div>
              <div style={{ fontSize: 12, opacity: 0.6 }}>Total calls analysed</div>
              <div style={{ fontWeight: 700, fontSize: 20 }}>{evalStats.total_calls}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, opacity: 0.6 }}>Booking success rate</div>
              <div style={{ fontWeight: 700, fontSize: 20, color: '#22c55e' }}>
                {evalStats.booking_success_rate}%
              </div>
            </div>
          </div>

          {sentiment && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 8 }}>Patient Sentiment Breakdown</div>
              <div style={{ display: 'flex', gap: 12 }}>
                {[
                  { key: 'positive', label: 'Positive', color: '#22c55e' },
                  { key: 'neutral', label: 'Neutral', color: '#94a3b8' },
                  { key: 'negative', label: 'Negative', color: '#ef4444' },
                ].map(({ key, label, color }) => (
                  <div key={key} style={{
                    flex: 1, textAlign: 'center', padding: '10px 8px',
                    borderRadius: 8, backgroundColor: `${color}18`,
                    border: `1px solid ${color}33`,
                  }}>
                    <div style={{ fontWeight: 700, fontSize: 18, color }}>
                      {(sentiment as any)[key] ?? 0}
                    </div>
                    <div style={{ fontSize: 11, opacity: 0.6 }}>{label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Outcome breakdown */}
          {evalStats.outcome_breakdown && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 8 }}>Outcome Breakdown</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {Object.entries(evalStats.outcome_breakdown)
                  .filter(([, v]) => v > 0)
                  .map(([outcome, count]) => (
                    <div key={outcome} style={{
                      padding: '4px 10px', borderRadius: 6,
                      backgroundColor: 'rgba(255,255,255,0.07)',
                      fontSize: 12, fontWeight: 600,
                    }}>
                      {outcome}: <span style={{ color: '#a5b4fc' }}>{count}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Simulation score (if available) */}
      {health?.simulation_score !== null && health?.simulation_score !== undefined && (
        <div style={{
          backgroundColor: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 12, padding: 20,
        }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>🧪 Simulation Score</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ fontWeight: 800, fontSize: 36, color: '#a5b4fc' }}>
              {health.simulation_score}
            </div>
            <div style={{ opacity: 0.6, fontSize: 13 }}>/ 100<br />avg across 8 scenarios</div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentHealthTab;
