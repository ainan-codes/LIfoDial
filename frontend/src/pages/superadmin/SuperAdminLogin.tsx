import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { API_URL } from '../../api/client';
import { setSession } from '../../api/auth';

export default function SuperAdminLogin() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/auth/superadmin-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });
      if (res.ok) {
        const data = await res.json();
        setSession({ token: data.access_token, role: 'superadmin', email: email.trim().toLowerCase() });
        navigate('/superadmin/dashboard', { replace: true });
      } else {
        setError('Invalid credentials');
      }
    } catch {
      setError('Could not reach the server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen w-full flex items-center justify-center p-4 bg-black">
      <div 
        className="max-w-[400px] w-full p-10 rounded-2xl relative overflow-hidden" 
        style={{ backgroundColor: '#0F0F0F', border: '1px solid #1A1A1A', zIndex: 1 }}
      >
        <div className="text-center mb-8 flex flex-col items-center">
          <img 
            src="/assets/lifodial-logo.png"
            alt="Lifodial"
            style={{
              height: '48px',
              width: 'auto',
              mixBlendMode: 'lighten',
              margin: '0 auto 16px',
              display: 'block',
            }}
          />
          <div style={{
            background: 'rgba(239,68,68,0.1)',
            border: '1px solid rgba(239,68,68,0.3)',
            color: '#EF4444',
            fontWeight: 500,
            fontSize: '12px',
            padding: '2px 8px',
            borderRadius: '999px',
            display: 'inline-block',
            letterSpacing: '0.05em'
          }}>
            INTERNAL
          </div>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-[#888] mb-2 uppercase tracking-wider">Work Email</label>
            <input 
              required type="email" value={email} onChange={e => setEmail(e.target.value)}
              className="w-full px-4 py-3 rounded-lg text-sm bg-[#1A1A1A] text-white border border-[#2E2E2E] focus:outline-none focus:border-[#3ECF8E] transition-colors"
              placeholder="name@lifodial.com"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-[#888] mb-2 uppercase tracking-wider">Password</label>
            <input 
              required type="password" value={password} onChange={e => setPassword(e.target.value)}
              className="w-full px-4 py-3 rounded-lg text-sm bg-[#1A1A1A] text-white border border-[#2E2E2E] focus:outline-none focus:border-[#3ECF8E] transition-colors"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div role="alert" className="text-xs text-[#EF4444] bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.3)] rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full mt-6 py-3 rounded-lg text-sm font-semibold bg-[#3ECF8E] text-black hover:bg-[#34B37A] transition-colors border-none cursor-pointer disabled:opacity-60"
          >
            {loading ? 'Authenticating…' : 'Authenticate'}
          </button>
        </form>
      </div>
    </div>
  );
}
