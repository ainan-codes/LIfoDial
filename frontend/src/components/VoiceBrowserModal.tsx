import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  X, Search, Play, Pause, Volume2, VolumeX,
  RefreshCw, Mic, User, Users, Check, Loader,
} from 'lucide-react';
import { API_URL } from '../api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ELVoice {
  voice_id: string;
  name: string;
  preview_url: string | null;
  category: string;
  description: string;
  gender: string;
  accent: string;
  age: string;
  use_case: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSelect: (voice: { voice_id: string; name: string }) => void;
  selectedVoiceId?: string;
}

// ── VoiceBrowserModal ─────────────────────────────────────────────────────────

export default function VoiceBrowserModal({ open, onClose, onSelect, selectedVoiceId }: Props) {
  const [voices, setVoices]       = useState<ELVoice[]>([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState('');
  const [search, setSearch]       = useState('');
  const [gender, setGender]       = useState<'all' | 'male' | 'female'>('all');
  const [category, setCategory]   = useState<string>('all');
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // ── Fetch voices ────────────────────────────────────────────────────────────

  const fetchVoices = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    setError('');
    try {
      if (forceRefresh) {
        await fetch(`${API_URL}/voices/elevenlabs/refresh`, { method: 'POST' });
      }
      const res = await fetch(`${API_URL}/voices/elevenlabs`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setVoices(data.voices || []);
    } catch (e: any) {
      setError(e.message || 'Failed to load voices');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && voices.length === 0) fetchVoices();
  }, [open, fetchVoices]);

  // ── Stop audio on close ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) stopAudio();
  }, [open]);

  // ── Audio helpers ───────────────────────────────────────────────────────────

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    setPlayingId(null);
    setLoadingId(null);
  };

  const handlePlayPause = (voice: ELVoice) => {
    if (!voice.preview_url) return;

    // Pause if this voice is already playing
    if (playingId === voice.voice_id) {
      stopAudio();
      return;
    }

    // Stop any currently playing audio
    stopAudio();

    setLoadingId(voice.voice_id);

    const audio = new Audio(voice.preview_url);
    audioRef.current = audio;

    audio.addEventListener('canplay', () => {
      setLoadingId(null);
      setPlayingId(voice.voice_id);
      audio.play().catch(() => {
        setLoadingId(null);
        setPlayingId(null);
      });
    });

    audio.addEventListener('ended', () => {
      setPlayingId(null);
      audioRef.current = null;
    });

    audio.addEventListener('error', () => {
      setLoadingId(null);
      setPlayingId(null);
    });
  };

  // ── Filter voices ───────────────────────────────────────────────────────────

  const filtered = voices.filter(v => {
    const q = search.toLowerCase();
    const matchesSearch = !search
      || v.name.toLowerCase().includes(q)
      || v.accent.toLowerCase().includes(q)
      || v.description.toLowerCase().includes(q)
      || v.use_case.toLowerCase().includes(q);
    const matchesGender = gender === 'all' || v.gender === gender;
    const matchesCategory = category === 'all' || v.category === category;
    return matchesSearch && matchesGender && matchesCategory;
  });

  const categories = ['all', ...Array.from(new Set(voices.map(v => v.category))).sort()];

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!open) return null;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        backgroundColor: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '16px',
        backdropFilter: 'blur(4px)',
      }}
      onClick={e => { if (e.target === e.currentTarget) { stopAudio(); onClose(); } }}
    >
      <div
        style={{
          width: '100%', maxWidth: '880px', maxHeight: '90vh',
          backgroundColor: '#0D0D0D',
          border: '1px solid #1E1E1E',
          borderRadius: '16px',
          display: 'flex', flexDirection: 'column',
          boxShadow: '0 32px 80px rgba(0,0,0,0.6)',
          overflow: 'hidden',
        }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div style={{
          padding: '20px 24px',
          borderBottom: '1px solid #1A1A1A',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 10,
              background: 'linear-gradient(135deg, #3ECF8E22, #3ECF8E44)',
              border: '1px solid #3ECF8E44',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Volume2 size={17} color="#3ECF8E" />
            </div>
            <div>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#fff', letterSpacing: '-0.02em' }}>
                ElevenLabs Voice Library
              </h2>
              <p style={{ margin: 0, fontSize: 12, color: '#555', marginTop: 2 }}>
                {loading ? 'Loading voices…' : `${filtered.length} of ${voices.length} voices`}
              </p>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => fetchVoices(true)}
              disabled={loading}
              title="Refresh voice list"
              style={{
                padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                backgroundColor: 'transparent', border: '1px solid #1E1E1E',
                color: '#666', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5,
              }}
            >
              <RefreshCw size={13} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
              Refresh
            </button>
            <button
              onClick={() => { stopAudio(); onClose(); }}
              style={{
                padding: '7px', borderRadius: 8,
                backgroundColor: 'transparent', border: '1px solid #1E1E1E',
                color: '#666', cursor: 'pointer', display: 'flex', alignItems: 'center',
              }}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* ── Filters ────────────────────────────────────────────────────── */}
        <div style={{
          padding: '14px 24px',
          borderBottom: '1px solid #141414',
          display: 'flex', gap: 10, flexWrap: 'wrap', flexShrink: 0,
          backgroundColor: '#0A0A0A',
        }}>
          {/* Search */}
          <div style={{ position: 'relative', flex: 1, minWidth: 180 }}>
            <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#444' }} />
            <input
              type="text"
              placeholder="Search by name, accent, use case…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%', padding: '8px 10px 8px 30px',
                backgroundColor: '#111', border: '1px solid #1E1E1E',
                borderRadius: 8, color: '#ddd', fontSize: 13, outline: 'none',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* Gender */}
          <div style={{ display: 'flex', gap: 4 }}>
            {(['all', 'female', 'male'] as const).map(g => (
              <button
                key={g}
                onClick={() => setGender(g)}
                style={{
                  padding: '7px 14px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                  backgroundColor: gender === g ? 'rgba(62,207,142,0.12)' : 'transparent',
                  border: `1px solid ${gender === g ? 'rgba(62,207,142,0.4)' : '#1E1E1E'}`,
                  color: gender === g ? '#3ECF8E' : '#666',
                  cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5,
                  transition: 'all 0.15s',
                }}
              >
                {g === 'all' ? <Users size={12} /> : <User size={12} />}
                {g === 'all' ? 'All' : g === 'female' ? 'Female' : 'Male'}
              </button>
            ))}
          </div>

          {/* Category */}
          <select
            value={category}
            onChange={e => setCategory(e.target.value)}
            style={{
              padding: '7px 10px', borderRadius: 8, fontSize: 12,
              backgroundColor: '#111', border: '1px solid #1E1E1E',
              color: '#888', outline: 'none', cursor: 'pointer',
            }}
          >
            {categories.map(c => (
              <option key={c} value={c}>
                {c === 'all' ? 'All categories' : c.charAt(0).toUpperCase() + c.slice(1)}
              </option>
            ))}
          </select>
        </div>

        {/* ── Voice Grid ─────────────────────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 24px' }}>
          {/* Error state */}
          {error && !loading && (
            <div style={{
              textAlign: 'center', padding: '60px 0', color: '#ef4444',
            }}>
              <VolumeX size={36} style={{ marginBottom: 12, opacity: 0.6 }} />
              <p style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Could not load voices</p>
              <p style={{ fontSize: 12, color: '#555', marginBottom: 16 }}>{error}</p>
              <button
                onClick={() => fetchVoices()}
                style={{
                  padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
                  backgroundColor: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
                  color: '#ef4444', cursor: 'pointer',
                }}
              >
                Try again
              </button>
            </div>
          )}

          {/* Loading skeleton */}
          {loading && !error && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {Array.from({ length: 12 }).map((_, i) => (
                <div
                  key={i}
                  style={{
                    height: 96, borderRadius: 12,
                    backgroundColor: '#111', border: '1px solid #1A1A1A',
                    animation: 'pulse 1.5s ease-in-out infinite',
                    animationDelay: `${i * 0.05}s`,
                  }}
                />
              ))}
            </div>
          )}

          {/* Empty state */}
          {!loading && !error && filtered.length === 0 && voices.length > 0 && (
            <div style={{ textAlign: 'center', padding: '60px 0', color: '#555' }}>
              <Mic size={32} style={{ marginBottom: 12 }} />
              <p style={{ fontSize: 14 }}>No voices match your filters</p>
              <button
                onClick={() => { setSearch(''); setGender('all'); setCategory('all'); }}
                style={{
                  marginTop: 12, padding: '7px 16px', borderRadius: 8, fontSize: 12,
                  backgroundColor: 'transparent', border: '1px solid #1E1E1E',
                  color: '#3ECF8E', cursor: 'pointer',
                }}
              >
                Clear filters
              </button>
            </div>
          )}

          {/* Voice cards */}
          {!loading && !error && filtered.length > 0 && (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
              gap: 12,
            }}>
              {filtered.map(voice => {
                const isSelected = voice.voice_id === selectedVoiceId;
                const isPlaying  = playingId === voice.voice_id;
                const isLoading  = loadingId === voice.voice_id;

                return (
                  <div
                    key={voice.voice_id}
                    style={{
                      backgroundColor: isSelected ? 'rgba(62,207,142,0.06)' : '#111',
                      border: `1px solid ${isSelected ? 'rgba(62,207,142,0.4)' : isPlaying ? 'rgba(62,207,142,0.2)' : '#1A1A1A'}`,
                      borderRadius: 12,
                      padding: '14px 16px',
                      display: 'flex', flexDirection: 'column', gap: 10,
                      transition: 'border-color 0.15s, background 0.15s',
                      position: 'relative',
                    }}
                  >
                    {/* Selected checkmark */}
                    {isSelected && (
                      <div style={{
                        position: 'absolute', top: 10, right: 10,
                        width: 20, height: 20, borderRadius: '50%',
                        backgroundColor: '#3ECF8E',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        <Check size={11} color="#000" strokeWidth={3} />
                      </div>
                    )}

                    {/* Voice identity */}
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, paddingRight: isSelected ? 24 : 0 }}>
                      <div style={{
                        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                        background: voice.gender === 'female'
                          ? 'linear-gradient(135deg, #8B5CF622, #EC489922)'
                          : 'linear-gradient(135deg, #3B82F622, #6366F122)',
                        border: `1px solid ${voice.gender === 'female' ? '#8B5CF633' : '#3B82F633'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 15, fontWeight: 700,
                        color: voice.gender === 'female' ? '#C084FC' : '#60A5FA',
                      }}>
                        {voice.name.charAt(0).toUpperCase()}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, color: '#e5e5e5', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {voice.name}
                        </div>
                        <div style={{ fontSize: 11, color: '#555', marginTop: 2, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          {voice.gender && (
                            <span style={{
                              padding: '1px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                              backgroundColor: voice.gender === 'female' ? 'rgba(192,132,252,0.1)' : 'rgba(96,165,250,0.1)',
                              color: voice.gender === 'female' ? '#C084FC' : '#60A5FA',
                            }}>
                              {voice.gender}
                            </span>
                          )}
                          {voice.accent && (
                            <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 10, backgroundColor: '#1A1A1A', color: '#666' }}>
                              {voice.accent}
                            </span>
                          )}
                          {voice.use_case && (
                            <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 10, backgroundColor: '#1A1A1A', color: '#666' }}>
                              {voice.use_case}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Description */}
                    {voice.description && (
                      <p style={{
                        margin: 0, fontSize: 11, color: '#4a4a4a', lineHeight: 1.4,
                        display: '-webkit-box', WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical' as const, overflow: 'hidden',
                      }}>
                        {voice.description}
                      </p>
                    )}

                    {/* Audio waveform visualiser while playing */}
                    {isPlaying && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 2, height: 16 }}>
                        {Array.from({ length: 16 }).map((_, i) => (
                          <div
                            key={i}
                            style={{
                              width: 2, borderRadius: 1,
                              backgroundColor: '#3ECF8E',
                              height: `${Math.random() * 12 + 4}px`,
                              animation: `bar-bounce 0.8s ease-in-out infinite`,
                              animationDelay: `${i * 0.05}s`,
                            }}
                          />
                        ))}
                      </div>
                    )}

                    {/* Actions */}
                    <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                      {/* Preview button */}
                      <button
                        onClick={() => handlePlayPause(voice)}
                        disabled={!voice.preview_url || isLoading}
                        title={voice.preview_url ? 'Play preview' : 'No preview available'}
                        style={{
                          flex: 1, padding: '7px 0', borderRadius: 8,
                          fontSize: 12, fontWeight: 500,
                          backgroundColor: isPlaying ? 'rgba(62,207,142,0.1)' : '#171717',
                          border: `1px solid ${isPlaying ? 'rgba(62,207,142,0.3)' : '#1E1E1E'}`,
                          color: isPlaying ? '#3ECF8E' : voice.preview_url ? '#888' : '#333',
                          cursor: voice.preview_url && !isLoading ? 'pointer' : 'not-allowed',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                          transition: 'all 0.15s',
                        }}
                      >
                        {isLoading ? (
                          <><Loader size={12} style={{ animation: 'spin 1s linear infinite' }} /> Loading…</>
                        ) : isPlaying ? (
                          <><Pause size={12} /> Stop</>
                        ) : (
                          <><Play size={12} /> Preview</>
                        )}
                      </button>

                      {/* Select button */}
                      <button
                        onClick={() => {
                          stopAudio();
                          onSelect({ voice_id: voice.voice_id, name: voice.name });
                          onClose();
                        }}
                        style={{
                          flex: 1, padding: '7px 0', borderRadius: 8,
                          fontSize: 12, fontWeight: 600,
                          backgroundColor: isSelected ? 'rgba(62,207,142,0.15)' : 'transparent',
                          border: `1px solid ${isSelected ? 'rgba(62,207,142,0.4)' : '#1E1E1E'}`,
                          color: isSelected ? '#3ECF8E' : '#666',
                          cursor: 'pointer',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                          transition: 'all 0.15s',
                        }}
                        onMouseEnter={e => {
                          if (!isSelected) {
                            (e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(62,207,142,0.08)';
                            (e.currentTarget as HTMLElement).style.borderColor = 'rgba(62,207,142,0.3)';
                            (e.currentTarget as HTMLElement).style.color = '#3ECF8E';
                          }
                        }}
                        onMouseLeave={e => {
                          if (!isSelected) {
                            (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent';
                            (e.currentTarget as HTMLElement).style.borderColor = '#1E1E1E';
                            (e.currentTarget as HTMLElement).style.color = '#666';
                          }
                        }}
                      >
                        {isSelected ? <><Check size={12} /> Selected</> : 'Select'}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <div style={{
          padding: '12px 24px',
          borderTop: '1px solid #141414',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0,
          backgroundColor: '#0A0A0A',
        }}>
          <p style={{ margin: 0, fontSize: 11, color: '#333' }}>
            Previews play directly from ElevenLabs — no credits used.
          </p>
          <button
            onClick={() => { stopAudio(); onClose(); }}
            style={{
              padding: '8px 20px', borderRadius: 8, fontSize: 13, fontWeight: 500,
              backgroundColor: 'transparent', border: '1px solid #1E1E1E',
              color: '#666', cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>
      </div>

      {/* Keyframe animations */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 0.8; }
        }
        @keyframes bar-bounce {
          0%, 100% { transform: scaleY(0.4); }
          50% { transform: scaleY(1); }
        }
      `}</style>
    </div>
  );
}
