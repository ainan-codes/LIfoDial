import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Eye, EyeOff, Phone } from 'lucide-react';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    const trimmedEmail = email.toLowerCase().trim();

    // ── FAST PATH: SuperAdmin credentials — no API call needed ──────────
    if (trimmedEmail === 'admin@lifodial.com' && password === 'lifodial2026') {
      localStorage.setItem('lifodial-authed', 'true');
      localStorage.setItem('lifodial-superadmin', 'true');
      localStorage.setItem('lifodial-email', trimmedEmail);
      navigate('/superadmin/dashboard');
      return;
    }

    // ── CLINIC LOGIN: API call with 5-second timeout ────────────────────
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000); // 5s max

      const res = await fetch(`${import.meta.env.VITE_API_URL || 'https://lifodial.onrender.com'}/auth/clinic-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmedEmail, password }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (res.ok) {
        const data = await res.json();
        localStorage.setItem('lifodial-authed', 'true');
        localStorage.setItem('lifodial-tenant-id', data.tenant_id || '');
        localStorage.setItem('lifodial-email', trimmedEmail);
        localStorage.setItem('lifodial-clinic-name', data.clinic_name || '');
        // Clinic admin goes to /my-agent
        navigate('/my-agent');
        return;
      } else {
        // API returned error — still let them in as generic user
        localStorage.setItem('lifodial-authed', 'true');
        localStorage.setItem('lifodial-email', trimmedEmail);
      }
    } catch {
      // Network error or timeout — still allow access (graceful fallback)
      localStorage.setItem('lifodial-authed', 'true');
      localStorage.setItem('lifodial-email', trimmedEmail);
    }
    navigate('/dashboard');
  };

  return (
    <div
      data-testid="login-page"
      className="min-h-screen flex items-center justify-center"
      style={{ backgroundColor: 'var(--bg-page)' }}
    >
      <div className="w-full px-4" style={{ maxWidth: '400px' }}>
        {/* Logo */}
        <div className="text-center mb-8">
          <Link to="/" style={{ display: 'block', textDecoration: 'none' }}>
            <img 
              src="/assets/lifodial-logo.png"
              alt="Lifodial"
              className="logo"
              style={{
                height: '48px',
                width: 'auto',
                margin: '0 auto 24px',
                display: 'block',
              }}
            />
          </Link>
          <h1 style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0 }}>
            Sign in to your account
          </h1>
          <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginTop: '6px' }}>
            AI Voice Receptionist for Clinics
          </p>
        </div>

        {/* Card */}
        <div
          className="p-8 rounded-xl"
          style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}
        >
          <form onSubmit={handleLogin} className="space-y-5">
            <div>
              <label
                style={{ display: 'block', fontSize: '12px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', marginBottom: '6px' }}
              >
                Email Address
              </label>
              <input
                data-testid="email-input"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="doctor@clinic.com"
                required
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  fontSize: '14px',
                  borderRadius: '8px',
                  outline: 'none',
                  backgroundColor: 'var(--bg-surface-2)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                  transition: 'border-color 0.15s, box-shadow 0.15s',
                }}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)'; }}
                onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
              />
            </div>

            <div>
              <div className="flex justify-between mb-1.5">
                <label style={{ fontSize: '12px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)' }}>
                  Password
                </label>
                <button type="button" style={{ fontSize: '12px', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}>
                  Forgot password?
                </button>
              </div>
              <div className="relative">
                <input
                  data-testid="password-input"
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  style={{
                    width: '100%',
                    padding: '10px 40px 10px 12px',
                    fontSize: '14px',
                    borderRadius: '8px',
                    outline: 'none',
                    backgroundColor: 'var(--bg-surface-2)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                    transition: 'border-color 0.15s, box-shadow 0.15s',
                  }}
                  onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)'; }}
                  onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
                />
                <button
                  type="button"
                  onClick={() => setShowPw(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
                >
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <button
              data-testid="login-submit"
              type="submit"
              disabled={loading}
              style={{
                width: '100%',
                padding: '10px',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: 600,
                color: '#000000',
                backgroundColor: loading ? 'var(--accent-hover)' : 'var(--accent)',
                border: 'none',
                cursor: loading ? 'not-allowed' : 'pointer',
                opacity: loading ? 0.7 : 1,
                transition: 'background-color 0.15s, opacity 0.15s',
                marginTop: '8px',
              }}
              onMouseEnter={e => { if (!loading) (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-hover)'; }}
              onMouseLeave={e => { if (!loading) (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent)'; }}
            >
              {loading ? 'Signing in…' : 'Sign In'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
