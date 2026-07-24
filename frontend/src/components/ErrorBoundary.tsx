import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Top-level error boundary. Before this existed, any render-time exception in
 * the SPA produced a blank white screen with no recovery path (audit: frontend
 * has no error boundary). This catches it, shows a recoverable fallback, and
 * logs to the console (and to Sentry if a browser SDK is ever wired up).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] Uncaught render error:', error, info.componentStack);
    // If a Sentry browser SDK is added later, report here:
    // (window as any).Sentry?.captureException?.(error);
  }

  private handleReload = () => {
    this.setState({ hasError: false, error: null });
    window.location.assign('/');
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px',
          background: '#0f172a',
          color: '#e2e8f0',
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: 420,
            textAlign: 'center',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 16,
            padding: '32px 28px',
          }}
        >
          <h1 style={{ fontSize: 20, margin: '0 0 8px', color: '#fff' }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: 'rgba(226,232,240,0.7)' }}>
            The page hit an unexpected error. Your data is safe — reloading
            usually fixes it. If it keeps happening, contact Lifodial support.
          </p>
          <button
            onClick={this.handleReload}
            style={{
              marginTop: 20,
              padding: '10px 20px',
              fontSize: 14,
              fontWeight: 600,
              color: '#0f172a',
              background: '#3ECF8E',
              border: 'none',
              borderRadius: 10,
              cursor: 'pointer',
            }}
          >
            Reload app
          </button>
        </div>
      </div>
    );
  }
}
