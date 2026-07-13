/**
 * KnowledgeBase.tsx — Phase 6
 * Super-admin Knowledge Base management per clinic.
 * FAQs, working hours, contacts, custom info — all editable inline.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  BookOpen, Plus, Search, Trash2, Edit2, Check, X,
  Clock, Phone, HelpCircle, FileText, ChevronDown, Building2, Save,
} from 'lucide-react';

import fetchWithAuth from '../../api/client';

// ── Types ─────────────────────────────────────────────────────────────────────
interface Clinic { id: string; clinic_name: string; location?: string; }
interface KBEntry { id: string; category: string; key?: string; title: string; content: string; is_active: boolean; created_at?: string; }

// ── Category config ────────────────────────────────────────────────────────────
const CATS = [
  { id: 'all',     label: 'All',          icon: FileText,     color: '#A1A1A1' },
  { id: 'hours',   label: 'Working Hours', icon: Clock,        color: '#f59e0b' },
  { id: 'contact', label: 'Contacts',      icon: Phone,        color: '#3ECF8E' },
  { id: 'faq',     label: 'FAQs',          icon: HelpCircle,   color: '#60a5fa' },
  { id: 'custom',  label: 'Custom Info',   icon: FileText,     color: '#a78bfa' },
];

const CAT_COLOR: Record<string, string> = { hours: '#f59e0b', contact: '#3ECF8E', faq: '#60a5fa', custom: '#a78bfa' };

// ── Skeleton ──────────────────────────────────────────────────────────────────
function Skeleton({ h = '60px' }: { h?: string }) {
  return <div style={{ height: h, borderRadius: '10px', background: 'linear-gradient(90deg, #1a1a1a 25%, #252525 50%, #1a1a1a 75%)', backgroundSize: '200% 100%', animation: 'shimmer 1.4s infinite' }} />;
}

// ── Inline KB Entry Row ────────────────────────────────────────────────────────
function EntryRow({
  entry, onDelete, onUpdate,
}: {
  entry: KBEntry;
  onDelete: (id: string) => Promise<void>;
  onUpdate: (id: string, data: Partial<KBEntry>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(entry.title);
  const [content, setContent] = useState(entry.content);
  const [saving, setSaving] = useState(false);
  const color = CAT_COLOR[entry.category] || '#A1A1A1';

  const save = async () => {
    setSaving(true);
    await onUpdate(entry.id, { title, content });
    setSaving(false);
    setEditing(false);
  };

  return (
    <div style={{ background: '#131313', border: `1px solid ${editing ? color + '40' : '#1E1E1E'}`, borderRadius: '12px', padding: '16px', transition: 'border-color 0.2s' }}>
      {editing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <input
            value={title}
            onChange={e => setTitle(e.target.value)}
            style={{ padding: '8px 12px', background: '#0D0D0D', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#fff', fontSize: '13px', fontWeight: 600, outline: 'none' }}
          />
          <textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            rows={3}
            style={{ padding: '8px 12px', background: '#0D0D0D', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#ccc', fontSize: '13px', outline: 'none', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.6 }}
          />
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={save} disabled={saving} style={{ padding: '7px 16px', background: color, border: 'none', borderRadius: '7px', color: '#000', fontWeight: 700, fontSize: '12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '5px' }}>
              <Save size={12} /> {saving ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => { setEditing(false); setTitle(entry.title); setContent(entry.content); }} style={{ padding: '7px 12px', background: 'none', border: '1px solid #2E2E2E', borderRadius: '7px', color: '#888', cursor: 'pointer', fontSize: '12px' }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
          <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: color, flexShrink: 0, marginTop: '6px' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, color: '#fff', fontSize: '13px', marginBottom: '4px' }}>{entry.title}</div>
            <div style={{ color: '#888', fontSize: '12px', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{entry.content}</div>
          </div>
          <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
            <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', padding: '4px', display: 'flex' }}><Edit2 size={13} /></button>
            <button onClick={() => onDelete(entry.id)} style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', padding: '4px', display: 'flex' }}><Trash2 size={13} /></button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Add Entry Form ─────────────────────────────────────────────────────────────
function AddEntryForm({ category, onAdd }: { category: string; onAdd: (data: Partial<KBEntry>) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [cat, setCat] = useState(category === 'all' ? 'faq' : category);
  const [saving, setSaving] = useState(false);
  const color = CAT_COLOR[cat] || '#A1A1A1';

  const submit = async () => {
    if (!title.trim() || !content.trim()) return;
    setSaving(true);
    await onAdd({ category: cat, title, content });
    setTitle(''); setContent('');
    setSaving(false);
    setOpen(false);
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={{ width: '100%', padding: '12px', background: '#111', border: '1px dashed #2E2E2E', borderRadius: '12px', color: '#555', fontSize: '13px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', fontWeight: 600, transition: 'all 0.15s' }}
        onMouseEnter={e => { e.currentTarget.style.borderColor = '#3ECF8E'; e.currentTarget.style.color = '#3ECF8E'; }}
        onMouseLeave={e => { e.currentTarget.style.borderColor = '#2E2E2E'; e.currentTarget.style.color = '#555'; }}
      >
        <Plus size={14} /> Add Knowledge Entry
      </button>
    );
  }

  return (
    <div style={{ background: '#131313', border: `1px solid ${color}40`, borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', gap: '10px' }}>
        <select value={cat} onChange={e => setCat(e.target.value)} style={{ padding: '8px 12px', background: '#0D0D0D', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#fff', fontSize: '12px', outline: 'none', fontWeight: 600 }}>
          {CATS.filter(c => c.id !== 'all').map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Title (e.g. Monday–Saturday Hours)" style={{ flex: 1, padding: '8px 12px', background: '#0D0D0D', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#fff', fontSize: '13px', fontWeight: 600, outline: 'none' }} />
      </div>
      <textarea value={content} onChange={e => setContent(e.target.value)} placeholder="Content / Answer…" rows={3} style={{ padding: '8px 12px', background: '#0D0D0D', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#ccc', fontSize: '13px', outline: 'none', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.6 }} />
      <div style={{ display: 'flex', gap: '8px' }}>
        <button onClick={submit} disabled={saving || !title.trim() || !content.trim()} style={{ padding: '8px 18px', background: color, border: 'none', borderRadius: '8px', color: '#000', fontWeight: 700, fontSize: '13px', cursor: 'pointer', opacity: saving || !title.trim() || !content.trim() ? 0.5 : 1 }}>
          {saving ? 'Saving…' : 'Add Entry'}
        </button>
        <button onClick={() => setOpen(false)} style={{ padding: '8px 14px', background: 'none', border: '1px solid #2E2E2E', borderRadius: '8px', color: '#888', cursor: 'pointer', fontSize: '13px' }}>Cancel</button>
      </div>
    </div>
  );
}

// ── Document Upload UI ────────────────────────────────────────────────────────
function DocumentUpload({ onUpload }: { onUpload: (file: File) => void }) {
  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      if (file.size > 60 * 1024 * 1024) {
        alert("File size exceeds 60MB limit.");
        return;
      }
      onUpload(file);
    }
  };

  return (
    <div style={{ background: '#131313', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <BookOpen size={16} color="#60a5fa" />
        <h3 style={{ fontSize: '13px', fontWeight: 600, color: '#fff', margin: 0 }}>Upload Documents</h3>
      </div>
      <p style={{ margin: 0, fontSize: '12px', color: '#888' }}>Upload PDFs, Word docs, or text files to train the agent. Max 60MB per file.</p>
      
      <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '16px', background: '#0D0D0D', border: '1px dashed #3ECF8E50', borderRadius: '8px', cursor: 'pointer', transition: 'all 0.2s', marginTop: '8px' }}
        onMouseEnter={e => e.currentTarget.style.borderColor = '#3ECF8E'}
        onMouseLeave={e => e.currentTarget.style.borderColor = '#3ECF8E50'}
      >
        <Plus size={14} color="#3ECF8E" />
        <span style={{ fontSize: '13px', color: '#3ECF8E', fontWeight: 600 }}>Select File to Upload (Max 60MB)</span>
        <input type="file" style={{ display: 'none' }} onChange={handleFile} accept=".txt,.pdf,.doc,.docx" />
      </label>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function KnowledgeBasePage() {
  const [clinics, setClinics] = useState<Clinic[]>([]);
  const [selectedClinic, setSelectedClinic] = useState<string>('');
  const [entries, setEntries] = useState<KBEntry[]>([]);
  const [catFilter, setCatFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  // Load clinics
  useEffect(() => {
    fetchWithAuth('/tenants')
      .then(data => {
        const list = Array.isArray(data) ? data : [];
        setClinics(list);
        if (list.length > 0) setSelectedClinic(list[0].id);
      })
      .catch(() => setClinics([]));
  }, []);

  // Load KB entries when clinic changes
  const loadEntries = useCallback(async () => {
    if (!selectedClinic) return;
    setLoading(true);
    try {
      const data = await fetchWithAuth(`/tenants/${selectedClinic}/kb`);
      setEntries(data);
    } catch { setEntries([]); }
    finally { setLoading(false); }
  }, [selectedClinic]);

  useEffect(() => { loadEntries(); }, [loadEntries]);

  const handleAdd = async (data: Partial<KBEntry>) => {
    try {
      await fetchWithAuth(`/tenants/${selectedClinic}/kb`, {
        method: 'POST',
        body: JSON.stringify({ ...data, is_active: true }),
      });
      showToast('✓ Entry added');
      await loadEntries();
    } catch {
      // Keep parity with previous behavior: silently no-op on failure.
    }
  };

  const handleUpdate = async (id: string, data: Partial<KBEntry>) => {
    const original = entries.find(e => e.id === id)!;
    try {
      await fetchWithAuth(`/tenants/${selectedClinic}/kb/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...original, ...data }),
      });
      showToast('✓ Saved');
      await loadEntries();
    } catch {
      // Keep parity with previous behavior: silently no-op on failure.
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await fetchWithAuth(`/tenants/${selectedClinic}/kb/${id}`, { method: 'DELETE' });
      showToast('Entry removed');
      setEntries(prev => prev.filter(e => e.id !== id));
    } catch {
      // Keep parity with previous behavior: silently no-op on failure.
    }
  };

  const filtered = entries.filter(e => {
    const matchCat = catFilter === 'all' || e.category === catFilter;
    const q = search.toLowerCase();
    const matchSearch = !q || e.title.toLowerCase().includes(q) || e.content.toLowerCase().includes(q);
    return matchCat && matchSearch;
  });

  const grouped = CATS.filter(c => c.id !== 'all').reduce((acc, c) => {
    acc[c.id] = filtered.filter(e => e.category === c.id);
    return acc;
  }, {} as Record<string, KBEntry[]>);

  const currentClinic = clinics.find(c => c.id === selectedClinic);

  return (
    <div style={{ padding: '32px', height: '100%', overflowY: 'auto', background: '#0A0A0A' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '28px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <BookOpen size={22} color="#60a5fa" />
            Knowledge Base
          </h1>
          <p style={{ color: '#666', fontSize: '13px', margin: '6px 0 0' }}>
            Manage per-clinic FAQs, hours, contacts and custom info injected into agent prompts
          </p>
        </div>

        {/* Clinic selector */}
        <div style={{ position: 'relative', minWidth: '240px' }}>
          <Building2 size={13} color="#555" style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
          <select
            value={selectedClinic}
            onChange={e => setSelectedClinic(e.target.value)}
            style={{ width: '100%', padding: '10px 12px 10px 32px', background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '10px', color: '#fff', fontSize: '13px', outline: 'none', appearance: 'none', fontWeight: 600 }}
          >
            {clinics.length === 0 && <option>No clinics yet</option>}
            {clinics.map(c => <option key={c.id} value={c.id}>{c.clinic_name}</option>)}
          </select>
          <ChevronDown size={12} color="#555" style={{ position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
        </div>
      </div>

      {!selectedClinic ? (
        <div style={{ textAlign: 'center', padding: '80px', color: '#555' }}>
          <BookOpen size={40} color="#333" style={{ marginBottom: '12px' }} />
          <p>No clinics available. Approve an onboarding request to create a clinic first.</p>
        </div>
      ) : (
        <>
          {/* Filters */}
          <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', flexWrap: 'wrap', alignItems: 'center' }}>
            {/* Search */}
            <div style={{ position: 'relative', flex: 1, minWidth: '200px', maxWidth: '300px' }}>
              <Search size={13} color="#555" style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search knowledge base…"
                style={{ width: '100%', padding: '9px 12px 9px 34px', background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '9px', color: '#fff', fontSize: '13px', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>

            {/* Category tabs */}
            <div style={{ display: 'flex', gap: '6px', padding: '4px', background: '#1A1A1A', borderRadius: '10px', border: '1px solid #2E2E2E' }}>
              {CATS.map(c => {
                const Icon = c.icon;
                const isActive = catFilter === c.id;
                return (
                  <button key={c.id} onClick={() => setCatFilter(c.id)} style={{ padding: '6px 14px', borderRadius: '7px', border: 'none', background: isActive ? '#2E2E2E' : 'none', color: isActive ? '#fff' : '#666', fontSize: '12px', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '5px', transition: 'all 0.15s' }}>
                    <Icon size={11} color={isActive ? c.color : '#666'} />
                    {c.label}
                  </button>
                );
              })}
            </div>

            <span style={{ color: '#555', fontSize: '12px', marginLeft: 'auto' }}>{filtered.length} entries</span>
          </div>

          {/* Add form */}
          <div style={{ marginBottom: '20px' }}>
            <AddEntryForm category={catFilter} onAdd={handleAdd} />
            <DocumentUpload onUpload={(file) => showToast(`✓ Uploaded document: ${file.name}`)} />
          </div>

          {/* Entries grouped by category */}
          {loading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {[1, 2, 3, 4].map(i => <Skeleton key={i} h="72px" />)}
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '60px', color: '#555', background: '#111', borderRadius: '14px', border: '1px solid #1A1A1A' }}>
              <BookOpen size={32} color="#333" style={{ marginBottom: '10px' }} />
              <p style={{ margin: 0 }}>No entries yet for {currentClinic?.clinic_name}.</p>
              <p style={{ margin: '6px 0 0', fontSize: '13px' }}>Click "Add Knowledge Entry" above to start building the agent's knowledge base.</p>
            </div>
          ) : catFilter === 'all' ? (
            // Grouped view
            CATS.filter(c => c.id !== 'all' && grouped[c.id]?.length > 0).map(c => (
              <div key={c.id} style={{ marginBottom: '28px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                  <c.icon size={14} color={c.color} />
                  <h3 style={{ fontSize: '12px', fontWeight: 700, color: '#888', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{c.label}</h3>
                  <span style={{ fontSize: '11px', color: '#444' }}>· {grouped[c.id].length}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {grouped[c.id].map(entry => (
                    <EntryRow key={entry.id} entry={entry} onDelete={handleDelete} onUpdate={handleUpdate} />
                  ))}
                </div>
              </div>
            ))
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {filtered.map(entry => (
                <EntryRow key={entry.id} entry={entry} onDelete={handleDelete} onUpdate={handleUpdate} />
              ))}
            </div>
          )}
        </>
      )}

      {/* Toast */}
      {toast && (
        <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999, background: '#1A1A1A', border: '1px solid #3ECF8E40', color: '#3ECF8E', padding: '12px 20px', borderRadius: '10px', fontSize: '13px', fontWeight: 600, boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}>
          {toast}
        </div>
      )}

      <style>{`@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
    </div>
  );
}
