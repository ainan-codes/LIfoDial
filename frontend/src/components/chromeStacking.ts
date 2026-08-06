/**
 * Stacking order for the clinic shell's own chrome.
 *
 * Lives in its own module because both the shell (components/Layout.tsx) and the
 * impersonation banner it renders need the same numbers, and having the banner
 * import Layout would be a cycle.
 *
 * THE RULE: the app's navigation and session chrome sit above anything a page
 * renders on top of its content. A floating panel that swallows clicks on the nav
 * is a trap with no way out, so the nav outranks every overlay rather than each
 * overlay having to remember to dodge it.
 *
 * Panels a page opens must stay below CHROME_Z — the test-call panel is 45.
 * Full-screen modals that intentionally cover everything and are dismissible by
 * design (the voice picker, the outbound dialer) sit at 9998+.
 */

/** Impersonation banner, sidebar, mobile bottom nav. */
export const CHROME_Z = 60;

/** Mobile drawer backdrop: above page panels, below the drawer itself. */
export const CHROME_BACKDROP_Z = 55;

/** Banner height in px. The shell publishes this as --lfd-chrome-top. */
export const IMPERSONATION_BANNER_HEIGHT = 44;
