import { Eye, LogOut } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import fetchWithAuth from '../api/client';
import { endImpersonation, getImpersonation, type Impersonation } from '../api/auth';

/**
 * The "you are viewing someone else's dashboard" banner.
 *
 * Rendered by components/Layout.tsx, which is the shell for EVERY clinic route,
 * so there is no clinic page an impersonated session can reach without it. It is
 * intentionally:
 *
 *   • not dismissible — no close button, and nothing clears the record except a
 *     real exit. An impersonated session must never be able to look like the
 *     clinic's own genuine session.
 *   • not scrollable away — it is a static row at the top of the shell's flex
 *     column, not an overlay inside the scrolling page area.
 *   • counting down — the session has a hard server-side TTL, and a banner that
 *     showed no time left would leave the operator guessing why the dashboard
 *     suddenly logged them out.
 *
 * Exit does the server call FIRST (that is what actually revokes the token) and
 * tears down the client session second.
 */

/** Height in px. Layout.tsx offsets the mobile sidebar by exactly this. */
export const IMPERSONATION_BANNER_HEIGHT = 44;

const AMBER = '#f59e0b';

/**
 * End the impersonation session and leave — server first, then client.
 *
 * Exported because "Exit to Superadmin" is not the only way out: signing out from
 * the clinic sidebar while impersonating must also END the session server-side,
 * not merely discard the token locally. A discarded-but-live session would sit in
 * the audit trail looking active until its TTL ran out.
 */
export async function exitImpersonation(): Promise<void> {
  try {
    // This is what actually revokes the token; after it the token is dead even
    // if a copy lingered anywhere.
    await fetchWithAuth('/admin/impersonation/end', { method: 'POST' });
  } catch {
    // Already ended, or the backend is unreachable. Leave anyway — the session's
    // TTL is the backstop, and trapping the operator in someone else's dashboard
    // because a POST failed would be worse.
  }

  const restored = endImpersonation();
  // Full page load, not a client-side navigate: it drops every react-query cache
  // holding clinic-scoped data fetched with the impersonation token.
  window.location.href = restored ? '/superadmin/clinics' : '/superadmin/login';
}

function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function Banner({ imp }: { imp: Impersonation }) {
  // A deadline that failed to parse must neither render as "NaN:NaN" nor read as
  // already-expired — the latter would eject the operator the instant they
  // arrived. Drop the countdown and let the server's TTL (and the 401 handler in
  // api/client.ts) end the session instead. The banner itself still shows.
  const hasDeadline = Number.isFinite(imp.expiresAt);
  const [remaining, setRemaining] = useState(() => imp.expiresAt - Date.now());
  const [exiting, setExiting] = useState(false);
  // Exit must run once. The countdown reaching zero and an impatient double-click
  // are both live paths into it.
  const exitingRef = useRef(false);

  const exit = useCallback(async () => {
    if (exitingRef.current) return;
    exitingRef.current = true;
    setExiting(true);
    await exitImpersonation();
  }, []);

  useEffect(() => {
    if (!hasDeadline) return;
    const tick = () => {
      const left = imp.expiresAt - Date.now();
      setRemaining(left);
      if (left <= 0) void exit();
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [hasDeadline, imp.expiresAt, exit]);

  const expired = hasDeadline && remaining <= 0;

  return (
    <div
      role="alert"
      style={{
        height: IMPERSONATION_BANNER_HEIGHT,
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '0 14px',
        backgroundColor: 'rgba(245,158,11,0.14)',
        borderBottom: `1px solid ${AMBER}`,
        color: AMBER,
        fontSize: '13px',
        fontWeight: 600,
        // Positioned so it paints above the mobile sidebar (z 50) and its
        // backdrop (z 40) — a drawer must not be able to cover the banner.
        position: 'relative',
        zIndex: 60,
      }}
    >
      <Eye size={15} style={{ flexShrink: 0 }} />
      <span
        style={{
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          minWidth: 0,
        }}
      >
        Viewing <strong>{imp.clinicName}</strong> as Superadmin
      </span>

      <span
        style={{
          fontFamily: 'monospace',
          fontWeight: 700,
          opacity: 0.85,
          flexShrink: 0,
          marginLeft: 'auto',
        }}
        title="Time left before this impersonation session expires"
      >
        {/* Kept even with no deadline to parse: marginLeft:auto on this element is
            what pins the exit button to the right edge. */}
        {!hasDeadline ? '' : expired ? 'session expired' : formatRemaining(remaining)}
      </span>

      <button
        onClick={() => void exit()}
        disabled={exiting}
        style={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '5px 12px',
          borderRadius: '6px',
          border: `1px solid ${AMBER}`,
          backgroundColor: exiting ? 'transparent' : AMBER,
          color: exiting ? AMBER : '#000',
          fontSize: '12px',
          fontWeight: 700,
          cursor: exiting ? 'wait' : 'pointer',
        }}
      >
        <LogOut size={13} />
        {exiting ? 'Exiting…' : 'Exit to Superadmin'}
      </button>
    </div>
  );
}

export default function ImpersonationBanner() {
  // Read on every render rather than held in state: the record is written by
  // another page (the superadmin clinic panel) immediately before a full page
  // load, so there is nothing to subscribe to.
  const imp = getImpersonation();
  if (!imp) return null;
  // Keyed by session id so a second impersonation remounts with a fresh countdown.
  return <Banner key={imp.sessionId} imp={imp} />;
}
