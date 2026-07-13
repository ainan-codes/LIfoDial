import {
    Activity,
    BookOpen,
    Building2,
    CalendarCheck,
    ChevronLeft,
    ClipboardList,
    CreditCard,
    Headphones,
    IndianRupee,
    Key,
    LayoutDashboard,
    LogOut,
    Menu,
    Music,
    Phone
} from 'lucide-react';
import React, { useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { clearSession } from '../../api/auth';
import { ToastContainer } from './SAShared';

const SA_NAV = [
  // ─── ORG SETTINGS ───
  { path: '/superadmin/dashboard',    label: 'Overview',         icon: LayoutDashboard, section: 'ORG SETTINGS' },
  { path: '/superadmin/agents',       label: 'Agents',           icon: Headphones,      section: 'ORG SETTINGS' },
  { path: '/superadmin/clinics',      label: 'All Clinics',      icon: Building2,       section: 'ORG SETTINGS' },
  { path: '/superadmin/calls',        label: 'All Calls',        icon: Phone,           section: 'ORG SETTINGS' },
  { path: '/superadmin/phone-numbers',label: 'Phone Numbers',    icon: Phone,           section: 'ORG SETTINGS' },
  { path: '/superadmin/billing',      label: 'Billing',          icon: CreditCard,      section: 'ORG SETTINGS' },
  { path: '/superadmin/credits',      label: 'Credits',          icon: IndianRupee,     section: 'ORG SETTINGS' },
  // ─── COMMUNITY ───
  { path: '/superadmin/voice-library',label: 'Voice Library',    icon: Music,           section: 'COMMUNITY' },
  { path: '/superadmin/appointments', label: 'All Appointments', icon: CalendarCheck,   section: 'COMMUNITY' },
  { path: '/superadmin/requests',     label: 'Onboarding Reqs',  icon: ClipboardList,   section: 'COMMUNITY' },
  { path: '/superadmin/knowledge',    label: 'Knowledge Base',   icon: BookOpen,        section: 'COMMUNITY' },
  // ─── ACCOUNT ───
  { path: '/superadmin/system',       label: 'System Health',    icon: Activity,        section: 'ACCOUNT' },
  { path: '/superadmin/ai-platform',  label: 'Platform',         icon: Key,             section: 'ACCOUNT' },
];

/** Build breadcrumb segments from pathname */
function useBreadcrumbs() {
  const location = useLocation();
  const segments = location.pathname.split('/').filter(Boolean);
  // ['superadmin', 'agents', 'some-id', 'voice'] etc.
  const crumbs: { label: string; path: string }[] = [];
  let built = '';
  for (const seg of segments) {
    built += `/${seg}`;
    const nav = SA_NAV.find(n => n.path === built);
    const label = nav
      ? nav.label
      : seg.charAt(0).toUpperCase() + seg.slice(1).replace(/-/g, ' ');
    crumbs.push({ label, path: built });
  }
  return crumbs;
}

export default function SuperAdminLayout() {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const crumbs = useBreadcrumbs();

  const handleLogout = () => {
    clearSession();
    navigate('/');
  };

  const sidebarW = collapsed ? 56 : 220;

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: '#0A0A0A', fontFamily: "'Inter', 'Segoe UI', sans-serif" }}>

      {/* ══ Sidebar ══════════════════════════════════════════════════════════ */}
      <aside style={{
        width: sidebarW, flexShrink: 0,
        background: '#0F0F0F', borderRight: '1px solid #1A1A1A',
        display: 'flex', flexDirection: 'column',
        transition: 'width 0.2s cubic-bezier(0.4,0,0.2,1)',
        overflow: 'hidden',
        zIndex: 10,
      }}>

        {/* Toggle button */}
        <div style={{
          padding: collapsed ? '14px 0' : '14px 12px',
          borderBottom: '1px solid #1A1A1A',
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
        }}>
          {!collapsed && (
            <div>
              <div style={{ fontSize: '14px', fontWeight: 800, color: '#fff', letterSpacing: '-0.01em' }}>Lifodial</div>
              <div style={{ fontSize: '9px', fontWeight: 700, color: '#ef4444', background: '#ef444410', border: '1px solid #ef444430', borderRadius: '4px', padding: '1px 6px', display: 'inline-block', letterSpacing: '0.05em', marginTop: '2px' }}>
                SUPER ADMIN
              </div>
            </div>
          )}
          <button
            onClick={() => setCollapsed(c => !c)}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: '#555', padding: '4px', display: 'flex', alignItems: 'center',
              borderRadius: '6px', transition: 'color 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = '#fff')}
            onMouseLeave={e => (e.currentTarget.style.color = '#555')}
          >
            {collapsed ? <Menu size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: '8px 6px', overflowY: 'auto', overflowX: 'hidden', display: 'flex', flexDirection: 'column', gap: '2px' }}>
          {['ORG SETTINGS', 'COMMUNITY', 'ACCOUNT'].map(section => (
            <React.Fragment key={section}>
               {!collapsed && <div style={{ fontSize: '10px', fontWeight: 700, color: '#555', padding: '12px 12px 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{section}</div>}
               {SA_NAV.filter(link => link.section === section).map(link => {
                  const Icon = link.icon;
                  return (
                    <NavLink
                      key={link.path}
                      to={link.path}
                      title={collapsed ? link.label : undefined}
                      style={({ isActive }) => ({
                        display: 'flex', alignItems: 'center', gap: '10px', padding: collapsed ? '10px' : '8px 12px',
                        borderRadius: '9px', fontSize: '13px', fontWeight: isActive ? 600 : 500, textDecoration: 'none', transition: 'all 0.15s ease',
                        justifyContent: collapsed ? 'center' : 'flex-start', backgroundColor: isActive ? 'rgba(62,207,142,0.1)' : 'transparent',
                        color: isActive ? '#3ECF8E' : '#888',
                      })}
                      onMouseEnter={e => { if (!e.currentTarget.style.backgroundColor.includes('rgba')) { e.currentTarget.style.color = '#fff'; } }}
                      onMouseLeave={e => { if (!e.currentTarget.style.backgroundColor.includes('rgba')) { e.currentTarget.style.color = '#888'; } }}
                    >
                      {({ isActive }) => (
                        <>
                          <Icon size={15} color={isActive ? '#3ECF8E' : 'currentColor'} style={{ flexShrink: 0 }} />
                          {!collapsed && <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{link.label}</span>}
                        </>
                      )}
                    </NavLink>
                  );
               })}
            </React.Fragment>
          ))}
        </nav>

        {/* Logout */}
        <div style={{ padding: '8px 6px', borderTop: '1px solid #1A1A1A' }}>
          <button
            onClick={handleLogout}
            title={collapsed ? 'Sign out' : undefined}
            style={{
              width: '100%', display: 'flex', alignItems: 'center',
              gap: '10px', padding: collapsed ? '10px' : '9px 12px',
              justifyContent: collapsed ? 'center' : 'flex-start',
              borderRadius: '9px', fontSize: '13px', fontWeight: 400,
              background: 'transparent', border: 'none', color: '#555',
              cursor: 'pointer', textAlign: 'left', transition: 'all 0.15s',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = '#1A1A1A'; e.currentTarget.style.color = '#fff'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#555'; }}
          >
            <LogOut size={15} style={{ flexShrink: 0 }} />
            {!collapsed && 'Sign out'}
          </button>
        </div>
      </aside>

      {/* ══ Main Content ═════════════════════════════════════════════════════ */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>

        {/* Top bar with breadcrumb */}
        <header style={{
          height: '44px', flexShrink: 0,
          borderBottom: '1px solid #1A1A1A',
          display: 'flex', alignItems: 'center',
          padding: '0 20px', gap: '6px',
          background: '#0A0A0A',
        }}>
          {crumbs.map((c, i) => (
            <React.Fragment key={c.path}>
              {i > 0 && <span style={{ color: '#333', fontSize: '12px' }}>/</span>}
              <span
                onClick={() => i < crumbs.length - 1 && navigate(c.path)}
                style={{
                  fontSize: '12px',
                  fontWeight: i === crumbs.length - 1 ? 600 : 400,
                  color: i === crumbs.length - 1 ? '#ccc' : '#555',
                  cursor: i < crumbs.length - 1 ? 'pointer' : 'default',
                  textTransform: 'capitalize',
                }}
                onMouseEnter={e => { if (i < crumbs.length - 1) e.currentTarget.style.color = '#fff'; }}
                onMouseLeave={e => { if (i < crumbs.length - 1) e.currentTarget.style.color = '#555'; }}
              >
                {c.label}
              </span>
            </React.Fragment>
          ))}
        </header>

        {/* Page content */}
        <main style={{ flex: 1, overflowY: 'auto', background: '#0A0A0A' }}>
          <Outlet />
        </main>
      </div>

      <ToastContainer />
    </div>
  );
}
