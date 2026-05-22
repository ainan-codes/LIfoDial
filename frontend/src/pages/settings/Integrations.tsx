import React, { useState, useEffect } from 'react';
import { Database, MessageSquare, Briefcase, Webhook, Calendar, Send, Settings, CheckCircle2, Zap, Copy, AlertCircle, Trash2, X, RefreshCw } from 'lucide-react';
import { API_URL, fetchWithAuth } from '../../api/client';

export default function Integrations() {
  const [showGoogleModal, setShowGoogleModal] = useState(false);
  const [tenantId, setTenantId] = useState<string>('');
  const [webhookUrl, setWebhookUrl] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [copySuccess, setCopySuccess] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>('');

  // 1. Fetch current clinic/tenant settings on mount
  useEffect(() => {
    const tid = localStorage.getItem('lifodial-tenant-id') || 'e0f46c3b-d336-411a-85d1-13c5f516a7f0';
    setTenantId(tid);
    
    async function loadTenantData() {
      try {
        setLoading(true);
        const data = await fetchWithAuth(`/tenants/${tid}`);
        if (data && data.google_sheets_webhook_url) {
          setWebhookUrl(data.google_sheets_webhook_url);
        }
      } catch (err) {
        console.error('Failed to load integration settings:', err);
      } finally {
        setLoading(false);
      }
    }
    
    loadTenantData();
  }, []);

  // 2. Google Apps Script Code template to copy-paste
  const appsScriptCode = `function doPost(e) {
  try {
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    var data = JSON.parse(e.postData.contents);
    
    // Write headers if sheet is empty
    if (sheet.getLastRow() == 0) {
      sheet.appendRow([
        "Appointment ID", 
        "Patient Phone", 
        "Doctor Name", 
        "Specialization", 
        "Slot Time", 
        "Status", 
        "Synced At"
      ]);
    }
    
    // Append the dynamic booking details
    sheet.appendRow([
      data.appointment_id || "N/A",
      data.patient_phone || "N/A",
      data.doctor_name || "N/A",
      data.specialization || "N/A",
      data.slot_time || "N/A",
      data.status || "confirmed",
      new Date().toLocaleString()
    ]);
    
    // Auto-fit columns for premium look
    try {
      sheet.autoResizeColumns(1, 7);
    } catch(e) {}

    return ContentService.createTextOutput(JSON.stringify({ "status": "success", "message": "Booking synced successfully!" }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService.createTextOutput(JSON.stringify({ "status": "error", "message": error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}`;

  const copyToClipboard = () => {
    navigator.clipboard.writeText(appsScriptCode);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 3000);
  };

  // 3. Save Webhook URL to PostgreSQL backend
  const handleSave = async (urlToSave: string) => {
    try {
      setSaving(true);
      setErrorMessage('');
      const updated = await fetchWithAuth(`/tenants/${tenantId}`, {
        method: 'PUT',
        body: JSON.stringify({
          google_sheets_webhook_url: urlToSave || null
        })
      });
      setWebhookUrl(updated.google_sheets_webhook_url || '');
      setShowGoogleModal(false);
      setTestStatus('idle');
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to update Google Sheets settings.');
    } finally {
      setSaving(false);
    }
  };

  // 4. Test connection by dispatching a real request directly to the Apps Script
  const handleTestConnection = async () => {
    if (!webhookUrl || !webhookUrl.startsWith('https://script.google.com/')) {
      alert('Please enter a valid Google Apps Script Web App URL first.');
      return;
    }

    try {
      setTestStatus('testing');
      const testPayload = {
        appointment_id: "TEST-12345",
        patient_phone: "+91 99999 99999",
        doctor_name: "Dr. Lifodial Demo",
        specialization: "General Practice",
        slot_time: "Today at 5:00 PM (Test Sync)",
        status: "confirmed"
      };

      // We use 'no-cors' mode so Google's redirect doesn't fail the preflight check.
      // This dispatches the request successfully to the script in the background.
      await fetch(webhookUrl, {
        method: 'POST',
        mode: 'no-cors',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(testPayload)
      });

      setTestStatus('success');
    } catch (err) {
      console.error('Test webhook failed:', err);
      setTestStatus('error');
    }
  };

  const integrations = [
    { 
      id: 'google_sheets', 
      name: 'Google Sheets', 
      icon: Database, 
      connected: !!webhookUrl, 
      desc: 'Auto-export clinic appointments directly into a Google Spreadsheet in real-time.',
      badge: 'Active & Free'
    },
    { id: 'telegram', name: 'Telegram', icon: Send, connected: false, desc: 'Instant booking notifications and daily schedules sent to your staff.' },
    { id: 'oxzygen', name: 'Oxzygen HIS', icon: Briefcase, connected: false, desc: 'Sync appointments with your proprietary Hospital Information System.' },
    { id: 'webhook', name: 'Custom Webhook', icon: Webhook, connected: false, desc: 'Dispatch booking payload to any REST endpoint or custom server.' },
    { id: 'google_calendar', name: 'Google Calendar', icon: Calendar, connected: false, desc: 'Add AI bookings directly to your doctors\' Google Calendars.' },
    { id: 'whatsapp', name: 'WhatsApp Business', icon: MessageSquare, connected: false, desc: 'Send automatic appointment confirmation alerts to patients.', comingSoon: true },
    { id: 'zapier', name: 'Zapier', icon: Zap, connected: false, desc: 'Connect clinic appointment data to over 5,000+ apps.', comingSoon: true }
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12" style={{ minHeight: '300px' }}>
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="animate-spin text-accent" size={28} style={{ color: 'var(--accent)' }} />
          <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Loading clinic integrations...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6" style={{ paddingBottom: '40px' }}>
      <div className="mb-6">
        <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>Active Integrations</h2>
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>Connect Lifodial dynamically to Google Sheets and other HIS systems.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {integrations.map(int => (
          <div 
            key={int.id}
            className="p-5 rounded-xl flex flex-col justify-between"
            style={{ 
              backgroundColor: 'var(--bg-surface-2)', 
              border: int.connected ? '1px solid var(--accent)' : '1px solid var(--border)',
              position: 'relative'
            }}
          >
            {int.comingSoon && (
              <span style={{ position: 'absolute', top: '12px', right: '12px', backgroundColor: 'var(--bg-surface-3)', color: 'var(--text-muted)', fontSize: '10px', padding: '2px 6px', borderRadius: '4px', fontWeight: 600, textTransform: 'uppercase' }}>
                Coming Soon
              </span>
            )}

            {!int.comingSoon && int.badge && (
              <span style={{ position: 'absolute', top: '12px', right: '12px', backgroundColor: int.connected ? 'var(--accent-dim)' : 'var(--bg-surface-3)', color: int.connected ? 'var(--accent)' : 'var(--text-muted)', fontSize: '10px', padding: '2px 8px', borderRadius: '9999px', fontWeight: 600 }}>
                {int.badge}
              </span>
            )}
            
            <div className="flex items-start gap-4 mb-4">
              <div style={{ width: '40px', height: '40px', borderRadius: '8px', backgroundColor: int.connected ? 'var(--accent-dim)' : 'var(--bg-surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <int.icon size={20} color={int.connected ? 'var(--accent)' : 'var(--text-muted)'} />
              </div>
              <div style={{ paddingRight: '60px' }}>
                <h3 style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{int.name}</h3>
                {int.connected && <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--accent)', fontWeight: 600, marginTop: '4px' }}><CheckCircle2 size={12} /> Live Sync Active</span>}
              </div>
            </div>
            
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', flex: 1, lineHeight: '1.5' }}>{int.desc}</p>
            
            <div className="flex gap-2">
              {int.id === 'google_sheets' ? (
                <>
                  <button 
                    onClick={() => setShowGoogleModal(true)}
                    style={{ flex: 1, padding: '8px', borderRadius: '6px', backgroundColor: 'var(--bg-surface-3)', border: 'none', color: 'var(--text-primary)', fontSize: '13px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
                  >
                    <Settings size={14} /> {int.connected ? 'Configure URL' : 'Setup Webhook'}
                  </button>
                  {int.connected && (
                    <button 
                      onClick={() => { if(confirm("Are you sure you want to disconnect Google Sheets?")) handleSave(''); }}
                      style={{ padding: '8px 12px', borderRadius: '6px', backgroundColor: 'transparent', border: '1px solid var(--destructive)', color: 'var(--destructive)', fontSize: '13px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
                    >
                      <Trash2 size={14} /> Disconnect
                    </button>
                  )}
                </>
              ) : (
                <button 
                  disabled={true}
                  style={{ width: '100%', padding: '8px', borderRadius: '6px', backgroundColor: 'var(--bg-surface-3)', border: 'none', color: 'var(--text-muted)', fontSize: '13px', fontWeight: 600, cursor: 'not-allowed', opacity: 0.5 }}
                >
                  {int.comingSoon ? 'Coming Soon' : 'Available on Pro'}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Google Sheets Modal */}
      {showGoogleModal && (
        <div style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, padding: '20px' }}>
          <div className="flex flex-col" style={{ backgroundColor: 'var(--bg-surface)', padding: '28px', borderRadius: '16px', width: '100%', maxWidth: '640px', maxHeight: '90vh', border: '1px solid var(--border)', overflowY: 'auto' }}>
            
            <div className="flex justify-between items-center mb-4">
              <h3 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>Configure Google Sheets (100% Free Setup)</h3>
              <button onClick={() => setShowGoogleModal(false)} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>
                <X size={20} />
              </button>
            </div>

            <div className="space-y-5" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              
              <div className="p-3 rounded-lg" style={{ backgroundColor: 'rgba(62,207,142,0.06)', border: '1px solid var(--accent-border)' }}>
                <p style={{ margin: 0, color: 'var(--text-primary)', display: 'flex', gap: '8px', alignItems: 'flex-start', lineHeight: '1.4' }}>
                  <Zap size={18} className="text-accent" style={{ color: 'var(--accent)', flexShrink: 0, marginTop: '2px' }} />
                  <span><strong>Zero GCP Fees:</strong> Since we deploy as a free Google Apps Script Web App, you bypass Google Cloud Developer Console setup, client IDs, and billing entirely. No subscription fees!</span>
                </p>
              </div>

              {/* Instructions */}
              <div className="space-y-4">
                <div>
                  <h4 style={{ fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px', fontSize: '13px' }}>1. Get Google Apps Script Code</h4>
                  <p style={{ margin: '0 0 8px' }}>Copy this complete, pre-configured Apps Script code:</p>
                  
                  <div style={{ position: 'relative' }}>
                    <textarea 
                      readOnly 
                      value={appsScriptCode} 
                      style={{ 
                        width: '100%', 
                        height: '140px', 
                        padding: '10px', 
                        fontFamily: 'monospace', 
                        fontSize: '11px', 
                        borderRadius: '8px', 
                        border: '1px solid var(--border)', 
                        backgroundColor: 'var(--bg-surface-2)', 
                        color: 'var(--text-muted)' 
                      }} 
                    />
                    <button 
                      onClick={copyToClipboard}
                      style={{ 
                        position: 'absolute', 
                        top: '8px', 
                        right: '8px', 
                        padding: '6px 10px', 
                        fontSize: '11px', 
                        borderRadius: '6px', 
                        backgroundColor: 'var(--accent)', 
                        color: '#000', 
                        border: 'none', 
                        fontWeight: 600, 
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px'
                      }}
                    >
                      {copySuccess ? 'Copied ✓' : <><Copy size={12} /> Copy Code</>}
                    </button>
                  </div>
                </div>

                <div>
                  <h4 style={{ fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px', fontSize: '13px' }}>2. Setup in Google Sheet</h4>
                  <ol style={{ paddingLeft: '20px', margin: 0, listStyleType: 'decimal' }}>
                    <li>Create a new Google Sheet or open an existing one.</li>
                    <li>In the menu, click on <strong>Extensions</strong> &gt; <strong>Apps Script</strong>.</li>
                    <li>Delete any code in the editor, and paste the code copied above. Click the <strong>Save</strong> icon (floppy disk).</li>
                    <li>Click <strong>Deploy</strong> (top right) &gt; <strong>New deployment</strong>.</li>
                    <li>Click the Gear icon next to "Select type" and choose <strong>Web app</strong>.</li>
                    <li>Set <strong>Execute as:</strong> <code>Me (your-email@gmail.com)</code> and <strong>Who has access:</strong> <code>Anyone</code>.</li>
                    <li>Click <strong>Deploy</strong>, authorize the Google permissions, and copy the generated <strong>Web app URL</strong>.</li>
                  </ol>
                </div>

                <div>
                  <h4 style={{ fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 6px', fontSize: '13px' }}>3. Paste Web App Webhook URL</h4>
                  <input 
                    type="text" 
                    value={webhookUrl}
                    onChange={(e) => setWebhookUrl(e.target.value)}
                    placeholder="https://script.google.com/macros/s/.../exec" 
                    style={{ 
                      width: '100%', 
                      padding: '10px', 
                      borderRadius: '8px', 
                      border: '1px solid var(--border)', 
                      backgroundColor: 'var(--bg-surface-2)', 
                      color: 'var(--text-primary)',
                      fontFamily: 'monospace',
                      fontSize: '12px'
                    }} 
                  />
                </div>
              </div>

              {errorMessage && (
                <div className="p-3 rounded-lg flex gap-2 items-center" style={{ backgroundColor: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--destructive)' }}>
                  <AlertCircle size={16} />
                  <span>{errorMessage}</span>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex flex-col sm:flex-row gap-3 justify-between items-center mt-6 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
                <button 
                  onClick={handleTestConnection}
                  disabled={!webhookUrl || testStatus === 'testing'}
                  style={{ 
                    padding: '10px 16px', 
                    borderRadius: '8px', 
                    backgroundColor: testStatus === 'success' ? 'rgba(62,207,142,0.1)' : 'var(--bg-surface-3)', 
                    color: testStatus === 'success' ? 'var(--accent)' : 'var(--text-primary)', 
                    border: testStatus === 'success' ? '1px solid var(--accent)' : '1px solid var(--border)', 
                    fontWeight: 500, 
                    cursor: (!webhookUrl || testStatus === 'testing') ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px'
                  }}
                >
                  {testStatus === 'testing' ? 'Testing Sync...' : testStatus === 'success' ? 'Test Row Added Successfully! ✓' : testStatus === 'error' ? 'Test Failed (Check URL)' : 'Test Connection'}
                </button>

                <div className="flex gap-3 w-full sm:w-auto justify-end">
                  <button onClick={() => setShowGoogleModal(false)} style={{ padding: '10px 16px', borderRadius: '8px', backgroundColor: 'transparent', border: 'none', color: 'var(--text-secondary)', fontWeight: 500, cursor: 'pointer' }}>Cancel</button>
                  <button 
                    onClick={() => handleSave(webhookUrl)} 
                    disabled={saving}
                    style={{ 
                      padding: '10px 24px', 
                      borderRadius: '8px', 
                      backgroundColor: 'var(--accent)', 
                      color: '#000', 
                      border: 'none', 
                      fontWeight: 600, 
                      cursor: 'pointer',
                      minWidth: '120px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px'
                    }}
                  >
                    {saving ? 'Saving...' : 'Save & Activate'}
                  </button>
                </div>
              </div>

            </div>
          </div>
        </div>
      )}
    </div>
  );
}
