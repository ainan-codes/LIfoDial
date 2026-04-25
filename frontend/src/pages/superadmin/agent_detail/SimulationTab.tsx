import React, { useState, useCallback } from 'react';

import { API_URL } from '../../../api/client';
const API = API_URL;

interface SimulationScore {
  overall_score: number;
  goal_completion: number;
  naturalness: number;
  conciseness: number;
  language_accuracy: number;
  error_handling: number;
  issues: string[];
  strengths: string[];
}

interface SimResult {
  scenario: string;
  scenario_name: string;
  scenario_description: string;
  conversation: Array<{ role: string; text: string }>;
  booking_made: boolean;
  emergency_detected: boolean;
  turn_count: number;
  elapsed_ms: number;
  scores: SimulationScore;
  passed: boolean;
  expected_outcome: string;
  ran_at: string;
  error?: string;
}

interface AllSimResults {
  agent_id: string;
  total_scenarios: number;
  passed: number;
  failed: number;
  pass_rate: string;
  avg_score: number;
  results: SimResult[];
  ready_for_production: boolean;
  ran_at: string;
}

interface Props {
  agentId: string;
}

const ScoreBar: React.FC<{ value: number; max?: number }> = ({ value, max = 100 }) => {
  const pct = Math.min(100, Math.round((value / max) * 100));
  const color = pct >= 80 ? '#22c55e' : pct >= 60 ? '#f59e0b' : '#ef4444';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        flex: 1, height: 6, backgroundColor: 'rgba(255,255,255,0.1)',
        borderRadius: 3, overflow: 'hidden',
      }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          backgroundColor: color, borderRadius: 3,
          transition: 'width 0.6s ease',
        }} />
      </div>
      <span style={{ fontSize: 12, color, fontWeight: 600, minWidth: 32, textAlign: 'right' }}>
        {value}
      </span>
    </div>
  );
};

const ConversationView: React.FC<{ messages: Array<{ role: string; text: string }> }> = ({ messages }) => (
  <div style={{
    maxHeight: 240, overflowY: 'auto', padding: '12px',
    backgroundColor: 'rgba(0,0,0,0.2)', borderRadius: 8,
    display: 'flex', flexDirection: 'column', gap: 8,
  }}>
    {messages.map((m, i) => (
      <div key={i} style={{
        display: 'flex',
        justifyContent: m.role === 'ai' ? 'flex-start' : 'flex-end',
      }}>
        <div style={{
          maxWidth: '80%', padding: '6px 12px', borderRadius: 12,
          fontSize: 12, lineHeight: 1.5,
          backgroundColor: m.role === 'ai'
            ? 'rgba(99, 102, 241, 0.25)'
            : 'rgba(34, 197, 94, 0.15)',
          color: m.role === 'ai' ? '#a5b4fc' : '#86efac',
          borderTopLeftRadius: m.role === 'ai' ? 4 : 12,
          borderTopRightRadius: m.role === 'patient' ? 4 : 12,
        }}>
          <div style={{ fontSize: 10, opacity: 0.6, marginBottom: 2, fontWeight: 600 }}>
            {m.role === 'ai' ? '🤖 Agent' : '👤 Patient'}
          </div>
          {m.text}
        </div>
      </div>
    ))}
  </div>
);

const ResultCard: React.FC<{ result: SimResult; expanded: boolean; onToggle: () => void }> = ({
  result, expanded, onToggle,
}) => {
  const score = result.scores?.overall_score ?? 0;
  const passed = result.passed;

  return (
    <div style={{
      borderRadius: 10, overflow: 'hidden',
      border: `1px solid ${passed ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
      backgroundColor: 'rgba(255,255,255,0.03)',
    }}>
      {/* Header row */}
      <button
        onClick={onToggle}
        style={{
          width: '100%', padding: '12px 16px',
          display: 'flex', alignItems: 'center', gap: 12,
          background: 'none', border: 'none', cursor: 'pointer', color: 'inherit',
          textAlign: 'left',
        }}
      >
        <span style={{ fontSize: 18 }}>{passed ? '✅' : '⚠️'}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{result.scenario_name}</div>
          <div style={{ fontSize: 11, opacity: 0.6 }}>{result.scenario_description}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{
            fontWeight: 700, fontSize: 15,
            color: score >= 80 ? '#22c55e' : score >= 60 ? '#f59e0b' : '#ef4444',
          }}>
            {score}/100
          </div>
          <div style={{
            fontSize: 11, fontWeight: 600,
            color: passed ? '#22c55e' : '#ef4444',
          }}>
            {passed ? 'PASS' : 'FAIL'}
          </div>
        </div>
        <span style={{ fontSize: 12, opacity: 0.5 }}>{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Expanded content */}
      {expanded && !result.error && (
        <div style={{ padding: '0 16px 16px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          {/* Score breakdown */}
          <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {[
              { label: 'Goal Completion', val: result.scores?.goal_completion },
              { label: 'Naturalness', val: result.scores?.naturalness },
              { label: 'Conciseness', val: result.scores?.conciseness },
              { label: 'Language Accuracy', val: result.scores?.language_accuracy },
            ].map(({ label, val }) => (
              <div key={label}>
                <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>{label}</div>
                <ScoreBar value={val ?? 0} />
              </div>
            ))}
          </div>

          {/* Issues & Strengths */}
          {result.scores?.issues?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>⚠ Issues found</div>
              {result.scores.issues.map((issue, i) => (
                <div key={i} style={{
                  fontSize: 12, color: '#fca5a5', padding: '4px 0',
                  paddingLeft: 8, borderLeft: '2px solid #ef4444',
                }}>{issue}</div>
              ))}
            </div>
          )}

          {result.scores?.strengths?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>✓ Strengths</div>
              {result.scores.strengths.map((s, i) => (
                <div key={i} style={{
                  fontSize: 12, color: '#86efac', padding: '4px 0',
                  paddingLeft: 8, borderLeft: '2px solid #22c55e',
                }}>{s}</div>
              ))}
            </div>
          )}

          {/* Conversation */}
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 6 }}>
              Conversation ({result.turn_count} patient turns · {result.elapsed_ms}ms)
            </div>
            {result.conversation && <ConversationView messages={result.conversation} />}
          </div>
        </div>
      )}

      {expanded && result.error && (
        <div style={{ padding: '12px 16px', color: '#fca5a5', fontSize: 13 }}>
          Error: {result.error}
        </div>
      )}
    </div>
  );
};


const SimulationTab: React.FC<Props> = ({ agentId }) => {
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [allResults, setAllResults] = useState<AllSimResults | null>(null);
  const [expandedScenario, setExpandedScenario] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runAll = useCallback(async () => {
    setRunning(true);
    setError(null);
    setAllResults(null);
    setProgress(10);

    // Animate progress while waiting (backend takes 30-60s)
    const timer = setInterval(() => {
      setProgress(prev => Math.min(prev + 3, 90));
    }, 1500);

    try {
      const res = await fetch(`${API}/agents/${agentId}/simulate/all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      clearInterval(timer);
      setProgress(100);
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data: AllSimResults = await res.json();
      setAllResults(data);
    } catch (e: any) {
      clearInterval(timer);
      setError(e.message || 'Simulation failed');
    } finally {
      setRunning(false);
    }
  }, [agentId]);

  const totalScenarios = allResults?.total_scenarios ?? 8;
  const progressCount = allResults
    ? allResults.total_scenarios
    : Math.round((progress / 100) * totalScenarios);

  return (
    <div style={{ padding: '0 0 24px' }}>
      {/* Header */}
      <div style={{
        background: 'linear-gradient(135deg, rgba(99,102,241,0.12) 0%, rgba(168,85,247,0.08) 100%)',
        border: '1px solid rgba(99,102,241,0.25)',
        borderRadius: 12, padding: 20, marginBottom: 20,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 17, marginBottom: 4 }}>
              🧪 Simulation Testing
            </div>
            <div style={{ fontSize: 13, opacity: 0.7 }}>
              Test your agent against 8 pre-built patient scenarios before going live.
              Each scenario is scored 0–100 by AI evaluation.
            </div>
          </div>
          <button
            onClick={runAll}
            disabled={running}
            style={{
              padding: '10px 20px', borderRadius: 8, fontWeight: 600, fontSize: 13,
              background: running
                ? 'rgba(99,102,241,0.3)'
                : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              border: 'none', color: '#fff', cursor: running ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap',
            }}
          >
            {running ? (
              <>
                <span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>⟳</span>
                Running…
              </>
            ) : '▶ Run All Scenarios'}
          </button>
        </div>

        {/* Progress bar */}
        {(running || allResults) && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, opacity: 0.7, marginBottom: 6 }}>
              <span>Progress</span>
              <span>{progressCount}/{totalScenarios} scenarios</span>
            </div>
            <div style={{ height: 8, backgroundColor: 'rgba(255,255,255,0.1)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{
                height: '100%', width: `${progress}%`,
                background: 'linear-gradient(90deg, #6366f1, #8b5cf6)',
                borderRadius: 4, transition: 'width 0.5s ease',
              }} />
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          padding: '12px 16px', borderRadius: 8, marginBottom: 16,
          backgroundColor: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.3)',
          color: '#fca5a5', fontSize: 13,
        }}>
          ❌ {error}
        </div>
      )}

      {/* Aggregate summary */}
      {allResults && (
        <>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: 12, marginBottom: 20,
          }}>
            {[
              { label: 'Passed', value: allResults.passed, color: '#22c55e', icon: '✅' },
              { label: 'Failed', value: allResults.failed, color: '#ef4444', icon: '❌' },
              { label: 'Pass Rate', value: allResults.pass_rate, color: '#6366f1', icon: '📊' },
              { label: 'Avg Score', value: `${allResults.avg_score}/100`, color: '#f59e0b', icon: '⭐' },
            ].map(item => (
              <div key={item.label} style={{
                backgroundColor: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 10, padding: '14px 16px', textAlign: 'center',
              }}>
                <div style={{ fontSize: 20, marginBottom: 4 }}>{item.icon}</div>
                <div style={{ fontWeight: 700, fontSize: 20, color: item.color }}>{item.value}</div>
                <div style={{ fontSize: 12, opacity: 0.6 }}>{item.label}</div>
              </div>
            ))}
          </div>

          {/* Production readiness banner */}
          <div style={{
            padding: '12px 16px', borderRadius: 10, marginBottom: 20,
            backgroundColor: allResults.ready_for_production
              ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
            border: `1px solid ${allResults.ready_for_production ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
            display: 'flex', alignItems: 'center', gap: 12, fontSize: 14,
          }}>
            <span style={{ fontSize: 22 }}>
              {allResults.ready_for_production ? '🚀' : '🔧'}
            </span>
            <div>
              <div style={{ fontWeight: 700 }}>
                {allResults.ready_for_production ? 'Ready for production' : 'Needs improvement before going live'}
              </div>
              <div style={{ fontSize: 12, opacity: 0.7, marginTop: 2 }}>
                {allResults.passed}/{allResults.total_scenarios} scenarios passed · avg {allResults.avg_score}/100
                {!allResults.ready_for_production && ' · Fix failing scenarios and update system prompt'}
              </div>
            </div>
          </div>

          {/* Individual results */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {allResults.results.map(result => (
              <ResultCard
                key={result.scenario}
                result={result}
                expanded={expandedScenario === result.scenario}
                onToggle={() => setExpandedScenario(s => s === result.scenario ? null : result.scenario)}
              />
            ))}
          </div>
        </>
      )}

      {/* Empty state */}
      {!running && !allResults && !error && (
        <div style={{
          textAlign: 'center', padding: '48px 24px',
          color: 'rgba(255,255,255,0.4)',
        }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🧪</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>No simulations run yet</div>
          <div style={{ fontSize: 13 }}>
            Click "Run All Scenarios" to test your agent before going live.
            <br />Takes approximately 30–60 seconds to complete all 8 scenarios.
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
};

export default SimulationTab;
