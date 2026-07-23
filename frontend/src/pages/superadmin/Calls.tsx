import React, { useState, useEffect } from 'react';
import { CallLog } from '../../store/saStore';
import fetchWithAuth from '../../api/client';
import { StatusBadge, EmptyState } from '../../components/superadmin/SAShared';
import { Search, Filter, Phone, Clock, PlayCircle, Eye } from 'lucide-react';

function CallTranscriptDrawer({ call, onClose }: { call: CallLog; onClose: () => void }) {
  return (
    <div style={{ width: '400px', backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
      {/* Header */}
      <div style={{ padding: '24px', borderBottom: '1px solid #2E2E2E', position: 'relative' }}>
        <button onClick={onClose} style={{ position: 'absolute', top: '16px', right: '16px', background: 'none', border: 'none', color: '#888', cursor: 'pointer' }}>
          ✕
        </button>
        <span style={{ backgroundColor: '#2E2E2E', color: '#fff', padding: '4px 8px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, fontFamily: 'monospace', marginBottom: '12px', display: 'inline-block' }}>
          {call.id}
        </span>
        <h2 style={{ fontSize: '18px', fontWeight: 700, color: '#fff', margin: '0 0 4px' }}>{call.clinic_name}</h2>
        <p style={{ color: '#888', fontSize: '13px', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Clock size={12} /> {call.date} · {call.duration}
        </p>
        <div style={{ marginTop: '16px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <span style={{ backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', color: '#888', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>{call.intent}</span>
          <span style={{ backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', color: '#888', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>{call.language}</span>
          <StatusBadge status={call.status} />
        </div>
      </div>

      {/* Audio Playback Mock */}
      <div style={{ padding: '16px 24px', borderBottom: '1px solid #2E2E2E', backgroundColor: '#0F0F0F', display: 'flex', alignItems: 'center', gap: '16px' }}>
        <button style={{ width: '36px', height: '36px', borderRadius: '50%', backgroundColor: '#3ECF8E', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: '#000', flexShrink: 0 }}>
          <PlayCircle size={20} />
        </button>
        <div style={{ flex: 1, height: '8px', backgroundColor: '#2E2E2E', borderRadius: '4px', overflow: 'hidden' }}>
          <div style={{ width: '0%', height: '100%', backgroundColor: '#3ECF8E' }} />
        </div>
        <span style={{ color: '#888', fontFamily: 'monospace', fontSize: '11px' }}>{call.duration}</span>
      </div>

      {/* Transcript */}
      <div style={{ padding: '24px', flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {call.transcript.map((msg, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: msg.role === 'ai' ? 'flex-start' : 'flex-end' }}>
            <span style={{ color: '#555', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', marginBottom: '4px' }}>
              {msg.role === 'ai' ? 'AI Receptionist' : 'Patient'} · {msg.time}
            </span>
            <div style={{
              backgroundColor: msg.role === 'ai' ? '#3ECF8E15' : '#2E2E2E',
              border: `1px solid ${msg.role === 'ai' ? '#3ECF8E40' : '#3E3E3E'}`,
              color: '#fff', fontSize: '13px', padding: '12px 16px', borderRadius: '12px',
              borderTopLeftRadius: msg.role === 'ai' ? '4px' : '12px',
              borderTopRightRadius: msg.role === 'patient' ? '4px' : '12px',
              maxWidth: '85%', lineHeight: 1.5,
            }}>
              {msg.text}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SACalls() {
  const [callLogs, setCallLogs] = useState<CallLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selectedCall, setSelectedCall] = useState<CallLog | null>(null);

  // Real network-wide calls from call_records (via /api/call_logs). No fixtures:
  // an empty table renders an honest empty state, never City Dental / Metro Care.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchWithAuth('/api/call_logs?limit=50')
      .then((data: any) => {
        if (cancelled) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        setCallLogs(items.map((r: any): CallLog => ({
          id: r.id,
          clinic_name: r.clinic_name || '—',
          phone: r.phone || r.caller_number || '—',
          date: r.date || r.created_at || '—',
          duration: r.duration || '—',
          intent: r.intent || '—',
          status: r.status || 'Pending',
          language: r.language || '—',
          transcript: Array.isArray(r.transcript)
            ? r.transcript.map((m: any) => ({
                role: (m.role === 'user' || m.role === 'patient') ? 'patient' : 'ai',
                text: m.text ?? m.content ?? '',
                time: m.time ?? '',
              }))
            : [],
        })));
      })
      .catch((e: any) => { if (!cancelled) setError(e?.message || 'Failed to load calls'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filtered = callLogs.filter(c =>
    c.clinic_name.toLowerCase().includes(search.toLowerCase()) ||
    c.phone.includes(search) ||
    c.id.toLowerCase().includes(search.toLowerCase())
  );

  const emptyMessage = error
    ? error
    : loading
    ? 'Loading calls…'
    : callLogs.length === 0
    ? 'No calls yet'
    : 'No calls match your search';

  return (
    <div style={{ padding: '32px', display: 'flex', gap: '20px', height: '100%', overflow: 'hidden' }}>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px' }}>
          <div>
            <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>Network Call Logs</h1>
            <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>Master view of all AI conversations</p>
          </div>
        </div>

        {/* Filters */}
        <div style={{ display: 'flex', gap: '12px', marginBottom: '20px' }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <Search size={14} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: '#888' }} />
            <input
              type="text" placeholder="Search by Clinic, Phone, or Call ID..."
              value={search} onChange={e => setSearch(e.target.value)}
              style={{ width: '100%', backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '9px 12px 9px 36px', color: '#fff', fontSize: '14px', outline: 'none', boxSizing: 'border-box' }}
            />
          </div>
          <button style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '9px 16px', color: '#fff', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
            <Filter size={14} /> Filters
          </button>
        </div>

        {/* Table */}
        <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', overflow: 'hidden', flex: 1, overflowY: 'auto' }}>
          {filtered.length === 0 ? (
            <EmptyState icon={Phone} message={emptyMessage} />
          ) : (
            <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
              <thead style={{ backgroundColor: '#0F0F0F', position: 'sticky', top: 0, zIndex: 1 }}>
                <tr>
                  {['Call ID', 'Clinic', 'Patient Phone', 'Date & Time', 'Intent', 'Status', 'Action'].map(h => (
                    <th key={h} style={{ padding: '12px 20px', textAlign: 'left', color: '#888', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(c => (
                  <tr
                    key={c.id}
                    onClick={() => setSelectedCall(c)}
                    style={{
                      borderTop: '1px solid #2E2E2E', cursor: 'pointer',
                      backgroundColor: selectedCall?.id === c.id ? '#2E2E2E' : 'transparent',
                      transition: 'background-color 0.1s',
                    }}
                    onMouseEnter={e => { if (selectedCall?.id !== c.id) (e.currentTarget as HTMLElement).style.backgroundColor = '#1e1e1e'; }}
                    onMouseLeave={e => { if (selectedCall?.id !== c.id) (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                  >
                    <td style={{ padding: '14px 20px', color: '#888', fontFamily: 'monospace', fontSize: '12px' }}>{c.id}</td>
                    <td style={{ padding: '14px 20px', color: '#fff', fontWeight: 600 }}>{c.clinic_name}</td>
                    <td style={{ padding: '14px 20px', color: '#fff', fontFamily: 'monospace' }}>{c.phone}</td>
                    <td style={{ padding: '14px 20px', color: '#888' }}>{c.date} <span style={{ color: '#555', marginLeft: '4px' }}>({c.duration})</span></td>
                    <td style={{ padding: '14px 20px' }}>
                      <span style={{ backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', color: '#888', padding: '2px 8px', borderRadius: '4px', fontSize: '11px' }}>{c.intent}</span>
                    </td>
                    <td style={{ padding: '14px 20px' }}><StatusBadge status={c.status} /></td>
                    <td style={{ padding: '14px 20px' }}>
                      <button style={{ backgroundColor: 'transparent', border: 'none', color: '#888', cursor: 'pointer', display: 'flex', alignItems: 'center' }}>
                        <Eye size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Transcript Drawer */}
      {selectedCall && <CallTranscriptDrawer call={selectedCall} onClose={() => setSelectedCall(null)} />}

    </div>
  );
}
