import {
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
import { clearSession, isImpersonating, isSuperAdmin } from '../api/auth';
import ImpersonationBanner, { IMPERSONATION_BANNER_HEIGHT, exitImpersonation } from './ImpersonationBanner';

// Agent setup pending — will be enabled later
// To show the Agents nav item, set CLINIC_AGENT_NAV_ENABLED = true
const CLINIC_AGENT_NAV_ENABLED = false;

// `superadminOnly` entries are platform-team tooling, not clinic features:
// Voice Clone trains a custom TTS voice and Voice Library manages the shared
// voice catalogue (and shows which providers have keys) — a clinic admin should
// not be able to reach or change either. They stay fully available under
// /superadmin/*. Routes are guarded too (see App.tsx) so typing the URL by hand
// doesn't get around the hidden nav entry.
const nav = [
  { label: 'Dashboard',    icon: LayoutDashboard, to: '/dashboard',    hidden: false },
  { label: 'My Agent',     icon: Bot,             to: '/my-agent',     hidden: false },
  { label: 'Agents',       icon: Headphones,      to: '/agents',       hidden: !CLINIC_AGENT_NAV_ENABLED },
  { label: 'Call Logs',    icon: PhoneCall,        to: '/calls',        hidden: false },
  { label: 'Appointments', icon: CalendarCheck,    to: '/appointments', hidden: false },
  { label: 'Doctors',      icon: Users,            to: '/doctors',      hidden: false },
  // Analytics is gone as a separate destination — it is now the "Recent history"
  // section of Dashboard. /analytics redirects there (see App.tsx).
  { label: 'Voice Clone',  icon: Mic,              to: '/recorder',     hidden: false, superadminOnly: true },
  { label: 'Voice Library',icon: Music,            to: '/voice-library',hidden: false, superadminOnly: true },
  { label: 'Settings',     icon: Settings,         to: '/settings',     hidden: false },
];

// Bottom nav items shown on mobile (most important ones)
const bottomNav = [
  { label: 'Dashboard', icon: LayoutDashboard, to: '/dashboard' },
  { label: 'My Agent',  icon: Bot,             to: '/my-agent' },
  { label: 'Calls',     icon: PhoneCall,        to: '/calls' },
  // Analytics removed here too — the mobile bar is the easy one to forget, which
  // would have left a live link to a page that now only redirects.
  { label: 'Appointments', icon: CalendarCheck, to: '/appointments' },
  { label: 'Settings',  icon: Settings,         to: '/settings' },
];

export default function Layout() {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Superadmin impersonation ("Go to Clinic Admin Dashboard"). The banner lives
  // in THIS component because Layout is the shell for every clinic route, so
  // there is no clinic page that can render without it. During impersonation the
  // stored role is 'clinic', so isSuperAdmin() below is false and the
  // platform-only nav entries correctly stay hidden — the operator sees the
  // clinic's own dashboard, not a hybrid of both.
  const impersonating = isImpersonating();

  // Drop hidden/superadmin-only entries outright rather than rendering them with
  // display:none — a CSS-hidden NavLink is still in the DOM and still a live link.
  const showSuperadminOnly = isSuperAdmin();
  const visibleNav = nav.filter(
    item => !item.hidden && (!item.superadminOnly || showSuperadminOnly)
  );
  // The "Community" divider labels the Voice Clone/Voice Library group, so it
  // must not render when that whole group is hidden (it would orphan itself
  // above Settings).
  const communityHeadingBefore = visibleNav.find(i => i.superadminOnly)?.to;

  const closeSidebar = () => setSidebarOpen(false);

  return (
    <div
      className={`layout-shell${impersonating ? ' impersonating' : ''}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        overflow: 'hidden',
        backgroundColor: 'var(--bg-page)',
      }}
    >
      {/* Static row above the app, not an overlay inside it — it cannot be
          scrolled past, and the shell below simply gets shorter. Renders null
          when not impersonating, so the normal clinic layout is unchanged. */}
      <ImpersonationBanner />

      <div
        className="flex"
        style={{ flex: 1, minHeight: 0, overflow: 'hidden', backgroundColor: 'var(--bg-page)' }}
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
          {visibleNav.map(({ label, icon: Icon, to }) => (
            <React.Fragment key={to}>
              {to === communityHeadingBefore && (
                <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-muted)', padding: '12px 14px 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                   Community
                </div>
              )}
              <NavLink
                to={to}
                onClick={closeSidebar}
                className="flex items-center gap-3 mx-2 my-0.5 px-3 py-2 rounded-lg transition-all"
                style={({ isActive }) => ({
                  display: 'flex',
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
              // Signing out of an impersonated session must END that session, not
              // just forget the token locally — otherwise it stays live (and reads
              // as active in the audit trail) until its TTL expires.
              if (impersonating) {
                void exitImpersonation();
                return;
              }
              clearSession();
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
          /* The mobile sidebar is position:fixed from the viewport top, so while
             the impersonation banner is up the drawer would slide over it and
             hide the only way out. Push it down by exactly the banner's height. */
          .layout-shell.impersonating .layout-sidebar {
            top: ${IMPERSONATION_BANNER_HEIGHT}px !important;
            height: calc(100% - ${IMPERSONATION_BANNER_HEIGHT}px) !important;
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
