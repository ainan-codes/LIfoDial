// ─── Centralized Super Admin Zustand Store ────────────────────────────────────
import { create } from 'zustand';

// ── Types ─────────────────────────────────────────────────────────────────────
export type PlanTier = 'Free' | 'Pro' | 'Enterprise';
export type ClinicStatus = 'Active' | 'Suspended' | 'Pending';

export interface Clinic {
  id: string;
  name: string;
  location: string;
  plan: PlanTier;
  joined: string;
  status: ClinicStatus;
  calls_month: number;
  bookings: number;
  res_rate: string;
  avg_latency: string;
  model_id: string;
  admin_email: string;
}

export type ModelTier = 'basic' | 'pro' | 'enterprise' | 'custom';

export interface LLMModel {
  id: string;
  name: string;
  tier: ModelTier;
  costPerMinute: number;
  maxConcurrentSessions: number;
  features: string[];
  assignedClinics: string[];
  calls_month: number;
  avg_latency: string;
  resolution_rate: string;
}

export interface BillingPlan {
  id: string;
  tier: PlanTier;
  price: number;
  call_minutes: number;
  max_concurrent: number;
  model_tier: ModelTier;
  overage_rate: number;
  available: boolean;
}

export interface Invoice {
  id: string;
  clinic_id: string;
  date: string;
  amount: number;
  status: 'Paid' | 'Overdue' | 'Pending';
}

export interface OnboardingRequest {
  id: string;
  clinic_name: string;
  contact_name: string;
  email: string;
  phone: string;
  plan: PlanTier;
  location: string;
  requested_at: string;
  status: 'Pending' | 'Approved' | 'Rejected';
  note?: string;
}

export interface AuditEntry {
  id: string;
  action: string;
  clinic_name: string;
  model_name: string;
  by: string;
  reason: string;
  timestamp: string;
}

export interface Toast {
  id: string;
  message: string;
  type: 'success' | 'error' | 'info';
}

export interface SystemAlert {
  id: string;
  severity: 'critical' | 'warning' | 'info';
  message: string;
  timestamp: string;
}

export interface CallLog {
  id: string;
  clinic_name: string;
  phone: string;
  date: string;
  duration: string;
  intent: string;
  status: string;
  language: string;
  transcript: { role: 'ai' | 'patient'; text: string; time: string }[];
}

// ── Initial Data ────────────────────────────────────────────────────────────
const MODELS_INITIAL: LLMModel[] = [
  { id: 'm1', name: 'Gemini 2.0 Flash', tier: 'basic', costPerMinute: 0.002, maxConcurrentSessions: 200, features: ['multilingual', 'low-latency', 'medical-ner'], assignedClinics: [], calls_month: 0, avg_latency: '720ms', resolution_rate: '87%' },
  { id: 'm2', name: 'GPT-4o mini', tier: 'pro', costPerMinute: 0.008, maxConcurrentSessions: 100, features: ['multilingual', 'medical-ner', 'reasoning'], assignedClinics: [], calls_month: 0, avg_latency: '850ms', resolution_rate: '91%' },
  { id: 'm4', name: 'GPT-4o', tier: 'enterprise', costPerMinute: 0.02, maxConcurrentSessions: 50, features: ['multilingual', 'medical-ner', 'reasoning', 'custom-prompt'], assignedClinics: [], calls_month: 0, avg_latency: '780ms', resolution_rate: '94%' },
];

const PLANS_INITIAL: BillingPlan[] = [
  { id: 'bp1', tier: 'Free', price: 0, call_minutes: 500, max_concurrent: 2, model_tier: 'basic', overage_rate: 0.5, available: true },
  { id: 'bp2', tier: 'Pro', price: 4999, call_minutes: 3000, max_concurrent: 10, model_tier: 'pro', overage_rate: 1.2, available: true },
  { id: 'bp3', tier: 'Enterprise', price: 19999, call_minutes: 20000, max_concurrent: 50, model_tier: 'enterprise', overage_rate: 0.8, available: true },
];

// ── Store Interface ─────────────────────────────────────────────────────────
interface SAStore {
  clinics: Clinic[];
  models: LLMModel[];
  billingPlans: BillingPlan[];
  auditLog: AuditEntry[];
  toasts: Toast[];
  systemAlerts: SystemAlert[];
  callLogs: CallLog[];
  invoices: Invoice[];
  onboardingRequests: OnboardingRequest[];
  
  setClinics: (clinics: Clinic[]) => void;
  addClinic: (clinic: Clinic) => void;
  toggleSuspend: (id: string) => void;
  updateClinicModel: (clinicId: string, modelId: string, reason: string) => void;
  updatePlanPrice: (planId: string, price: number) => void;
  togglePlanAvailability: (planId: string) => void;
  approveRequest: (id: string) => void;
  rejectRequest: (id: string, reason: string) => void;
  addToast: (message: string, type: Toast['type']) => void;
  removeToast: (id: string) => void;
  getActiveCount: () => number;
  getMRR: () => number;
  getTotalCalls: () => number;
  getTotalBookings: () => number;

  // Friendly labels for id URL segments (e.g. agentId → agent name) so the
  // breadcrumb shows a name instead of a raw UUID (audit P5).
  entityLabels: Record<string, string>;
  setEntityLabel: (id: string, label: string) => void;
}

let toastIdCounter = 0;

export const useSAStore = create<SAStore>((set, get) => ({
  clinics: [],
  models: MODELS_INITIAL,
  billingPlans: PLANS_INITIAL,
  auditLog: [],
  toasts: [],
  systemAlerts: [
    { id: 'al1', severity: 'warning', message: 'High latency detected in us-east-1 region', timestamp: '10 mins ago' },
    { id: 'al2', severity: 'critical', message: 'Database connection pool near capacity', timestamp: '1 hour ago' },
  ],
  // No fixture calls — the All Calls page (Calls.tsx) fetches real rows from
  // /api/call_logs (call_records) and shows an honest empty state when there
  // are none. The old City Dental / Metro Care rows were pure fixtures.
  callLogs: [],
  entityLabels: {},
  setEntityLabel: (id, label) =>
    set((s) => (s.entityLabels[id] === label
      ? s
      : { entityLabels: { ...s.entityLabels, [id]: label } })),
  invoices: [
    { id: 'inv1', clinic_id: '1', date: 'Oct 01, 2026', amount: 4999, status: 'Paid' },
    { id: 'inv2', clinic_id: '2', date: 'Oct 01, 2026', amount: 0, status: 'Paid' },
  ],
  onboardingRequests: [
    { id: 'req1', clinic_name: 'Sunrise Med', contact_name: 'Rahul V', email: 'rahul@sunrisemed.in', phone: '+91 80000 11111', plan: 'Enterprise', location: 'Delhi', requested_at: '2 hrs ago', status: 'Pending' },
  ],

  setClinics: (clinics) => set({ clinics }),

  addClinic: (clinic) => {
    set(s => ({ clinics: [clinic, ...s.clinics] }));
    get().addToast(`${clinic.name} added successfully`, 'success');
  },

  toggleSuspend: (id) => {
    set(s => ({
      clinics: s.clinics.map(c =>
        c.id === id
          ? { ...c, status: c.status === 'Suspended' ? 'Active' : 'Suspended' }
          : c
      ),
    }));
  },

  updatePlanPrice: (planId, price) => {
    set(s => ({
      billingPlans: s.billingPlans.map(p => p.id === planId ? { ...p, price } : p),
    }));
    get().addToast('Plan price updated', 'success');
  },

  togglePlanAvailability: (planId) => {
    set(s => ({
      billingPlans: s.billingPlans.map(p => p.id === planId ? { ...p, available: !p.available } : p),
    }));
  },

  approveRequest: (id) => {
    set(s => ({
      onboardingRequests: s.onboardingRequests.map(r =>
        r.id === id ? { ...r, status: 'Approved' as const } : r
      ),
    }));
    get().addToast('Request approved — clinic created', 'success');
  },

  rejectRequest: (id, reason) => {
    set(s => ({
      onboardingRequests: s.onboardingRequests.map(r =>
        r.id === id ? { ...r, status: 'Rejected' as const, note: reason } : r
      ),
    }));
    get().addToast('Request rejected', 'info');
  },

  updateClinicModel: (clinicId, modelId, reason) => {
    const clinic = get().clinics.find(c => c.id === clinicId);
    const model = get().models.find(m => m.id === modelId);
    if (!clinic || !model) return;

    set(s => ({
      clinics: s.clinics.map(c => c.id === clinicId ? { ...c, model_id: modelId } : c),
      auditLog: [
        {
          id: `AL-${Date.now()}`,
          action: 'Model Assigned',
          clinic_name: clinic.name,
          model_name: model.name,
          by: 'admin@lifodial.com',
          reason,
          timestamp: new Date().toLocaleString('en-IN'),
        },
        ...s.auditLog,
      ],
    }));
    get().addToast(`${model.name} assigned to ${clinic.name}`, 'success');
  },

  addToast: (message, type) => {
    const id = `toast-${++toastIdCounter}`;
    set(s => ({ toasts: [...s.toasts, { id, message, type }] }));
    setTimeout(() => get().removeToast(id), 4000);
  },

  removeToast: (id) => {
    set(s => ({ toasts: s.toasts.filter(t => t.id !== id) }));
  },

  getActiveCount: () => get().clinics.filter(c => c.status === 'Active').length,
  getMRR: () => {
    const { clinics, billingPlans } = get();
    return clinics.reduce((acc, clinic) => {
      if (clinic.status !== 'Active') return acc;
      const plan = billingPlans.find(p => p.tier === clinic.plan);
      return acc + (plan?.price || 0);
    }, 0);
  },
  getTotalCalls: () => get().clinics.reduce((acc, clinic) => acc + clinic.calls_month, 0),
  getTotalBookings: () => get().clinics.reduce((acc, clinic) => acc + clinic.bookings, 0),
}));
