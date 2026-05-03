import {
    BarChart2,
    Bot,
    CalendarCheck,
    Headphones,
    IndianRupee,
    LayoutDashboard,
    LogOut, Menu, Mic,
    Music,
    PhoneCall,
    Settings,
    Users,
    X
} from 'lucide-react';
import React, { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';

// Agent setup pending — will be enabled later
// To show the Agents nav item, set CLINIC_AGENT_NAV_ENABLED = true
const CLINIC_AGENT_NAV_ENABLED = false;

const nav = [
  { label: 'Dashboard',    icon: LayoutDashboard, to: '/dashboard',    hidden: false },
  { label: 'My Agent',     icon: Bot,             to: '/my-agent',     hidden: false },
  { label: 'Agents',       icon: Headphones,      to: '/agents',       hidden: !CLINIC_AGENT_NAV_ENABLED },
  { label: 'Call Logs',    icon: PhoneCall,        to: '/calls',        hidden: false },
  { label: 'Appointments', icon: CalendarCheck,    to: '/appointments', hidden: false },
  { label: 'Doctors',      icon: Users,            to: '/doctors',      hidden: false },
  { label: 'Analytics',    icon: BarChart2,        to: '/analytics',    hidden: false },
  { label: 'Voice Clone',  icon: Mic,              to: '/recorder',     hidden: false },
  { label: 'Voice Library',icon: Music,            to: '/voice-library',hidden: false },
  { label: 'Settings',     icon: Settings,         to: '/settings',     hidden: false },
];

// Bottom nav items shown on mobile (most important ones)
const bottomNav = [
  { label: 'Dashboard', icon: LayoutDashboard, to: '/dashboard' },
  { label: 'My Agent',  icon: Bot,             to: '/my-agent' },
  { label: 'Calls',     icon: PhoneCall,        to: '/calls' },
  { label: 'Analytics', icon: BarChart2,        to: '/analytics' },
  { label: 'Settings',  icon: Settings,         to: '/settings' },
];

export default function Layout() {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const closeSidebar = () => setSidebarOpen(false);

  return (
    <div
      className="flex h-screen overflow-hidden"
      style={{ backgroundColor: 'var(--bg-page)' }}
    >
      {/* ── Mobile overlay backdrop ── */}
      {sidebarOpen && (
        <div
          onClick={closeSidebar}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.6)',
            backdropFilter: 'blur(4px)',
            zIndex: 40,
          }}
        />
      )}

      {/* ── Sidebar ── */}
      <aside
        style={{
          width: '220px',
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--bg-surface)',
          borderRight: '1px solid var(--border)',
          // On mobile: fixed overlay that slides in
          position: 'fixed' as const,
          top: 0,
          left: 0,
          height: '100%',
          zIndex: 50,
          transform: sidebarOpen ? 'translateX(0)' : 'translateX(-100%)',
          transition: 'transform 0.28s cubic-bezier(0.4,0,0.2,1)',
        }}
        // On desktop: show always via media query override in CSS
        className="layout-sidebar"
      >
        {/* Brand */}
        <div
          className="px-4 py-4 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <div className="sidebar-logo">
            <img
              src="/assets/lifodial-logo.png"
              alt="Lifodial"
              style={{
                height: '28px',
                width: 'auto',
                mixBlendMode: 'lighten',
              }}
            />
          </div>
          {/* Close button only on mobile */}
          <button
            onClick={closeSidebar}
            className="sidebar-close-btn"
            style={{
              background: 'none', border: 'none', color: 'var(--text-muted)',
              cursor: 'pointer', padding: '4px', borderRadius: '6px',
              display: 'flex', alignItems: 'center',
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* AI Agent status */}
        <div
          className="flex items-center gap-2 px-4 py-2.5"
          style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--accent-dim)' }}
        >
          <div className="w-1.5 h-1.5 rounded-full dot-pulse" style={{ backgroundColor: 'var(--accent)' }} />
          <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--accent)' }}>Online</span>
        </div>

        {/* Nav links */}
        <nav className="flex-1 py-4 overflow-y-auto">
          {nav.map(({ label, icon: Icon, to, hidden }) => (
            <React.Fragment key={to}>
              {label === 'Voice Library' && (
                <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-muted)', padding: '12px 14px 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                   Community
                </div>
              )}
              <NavLink
                to={to}
                onClick={closeSidebar}
                className="flex items-center gap-3 mx-2 my-0.5 px-3 py-2 rounded-lg transition-all"
                style={({ isActive }) => ({
                  display: hidden ? 'none' : 'flex',
                  backgroundColor: isActive ? 'var(--accent-dim)' : 'transparent',
                  color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                  borderLeft: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                  fontSize: '14px',
                  fontWeight: 500,
                  textDecoration: 'none',
                })}
              >
                <Icon size={16} />
                {label}
              </NavLink>
            </React.Fragment>
          ))}
        </nav>

        {/* Bottom user section */}
        <div className="px-4 py-4" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2.5 mb-3">
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
              style={{
                backgroundColor: 'var(--accent-dim)',
                color: 'var(--accent)',
                border: '1px solid var(--accent-border)',
              }}
            >
              U
            </div>
            <div className="min-w-0">
              <div className="truncate" style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                Your Clinic
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Admin</div>
            </div>
          </div>
          <button
            onClick={() => {
              localStorage.removeItem('lifodial-authed');
              localStorage.removeItem('lifodial-superadmin');
              localStorage.removeItem('lifodial-tenant-id');
              localStorage.removeItem('lifodial-email');
              localStorage.removeItem('lifodial-clinic-name');
              navigate('/');
            }}
            className="flex items-center gap-2 w-full transition-colors"
            style={{ fontSize: '12px', color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-secondary)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          >
            <LogOut size={13} />
            Sign out
          </button>
        </div>
      </aside>

      {/* ── Main content area ── */}
      <div className="flex-1 flex flex-col min-w-0 layout-main">
        {/* ── Mobile top bar ── */}
        <header
          className="layout-mobile-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 16px',
            height: '56px',
            flexShrink: 0,
            backgroundColor: 'var(--bg-surface)',
            borderBottom: '1px solid var(--border)',
            zIndex: 30,
          }}
        >
          <button
            onClick={() => setSidebarOpen(true)}
            style={{
              background: 'none', border: '1px solid var(--border)',
              color: 'var(--text-secondary)', cursor: 'pointer',
              padding: '8px', borderRadius: '8px',
              display: 'flex', alignItems: 'center',
            }}
          >
            <Menu size={20} />
          </button>
          <img
            src="/assets/lifodial-logo.png"
            alt="Lifodial"
            style={{ height: '24px', width: 'auto', mixBlendMode: 'lighten' }}
          />
          <div style={{ width: '36px' }} /> {/* spacer */}
        </header>

        {/* ── Page content ── */}
        <main
          className="flex-1 overflow-y-auto layout-page-content"
          style={{ backgroundColor: 'var(--bg-page)' }}
        >
          <Outlet />
        </main>

        {/* ── Mobile bottom navigation ── */}
        <nav
          className="layout-bottom-nav"
          style={{
            display: 'flex',
            backgroundColor: 'var(--bg-surface)',
            borderTop: '1px solid var(--border)',
            flexShrink: 0,
            paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          }}
        >
          {bottomNav.map(({ label, icon: Icon, to }) => (
            <NavLink
              key={to}
              to={to}
              style={({ isActive }) => ({
                flex: 1,
                display: 'flex',
                flexDirection: 'column' as const,
                alignItems: 'center',
                justifyContent: 'center',
                gap: '3px',
                padding: '10px 4px 8px',
                color: isActive ? 'var(--accent)' : 'var(--text-muted)',
                textDecoration: 'none',
                fontSize: '10px',
                fontWeight: 600,
                transition: 'color 0.2s',
                borderTop: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                marginTop: '-1px',
              })}
            >
              <Icon size={20} />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>

      <style>{`
        /* ── Desktop: sidebar is always visible, static ── */
        @media (min-width: 768px) {
          .layout-sidebar {
            position: static !important;
            transform: none !important;
            flex-shrink: 0;
          }
          .sidebar-close-btn {
            display: none !important;
          }
          .layout-mobile-header {
            display: none !important;
          }
          .layout-bottom-nav {
            display: none !important;
          }
          .layout-main {
            /* On desktop, sidebar is static so main fills the rest */
          }
          .layout-page-content {
            /* normal scroll on desktop */
          }
        }

        /* ── Mobile: sidebar hidden by default (transform -100%) ── */
        @media (max-width: 767px) {
          .layout-sidebar {
            width: 260px !important;
          }
          .layout-page-content {
            /* Add bottom padding for the bottom nav */
            padding-bottom: 0;
          }
        }
      `}</style>
    </div>
  );
}
