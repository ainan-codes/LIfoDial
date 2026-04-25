// ── Fixture tenant ─────────────────────────────────────────────────────────
export const FIXTURE_TENANT = {
  id: 'e0f46c3b-d336-411a-85d1-13c5f516a7f0',
  clinic_name: 'Apollo Demo Clinic',
  language: 'hi-IN',
  ai_number: '+91 90001 23456',
  forwarding_number: '+91 22 6627 2000',
  setup_complete: false,
  forwarding_verified: false,
};

// ── Fixture doctors ────────────────────────────────────────────────────────
export interface Doctor {
  id: string;
  name: string;
  specialization: string;
  his_doctor_id: string;
  available: boolean;
}

export const FIXTURE_DOCTORS: Doctor[] = [
  { id: 'd-001', name: 'Dr. Suresh Menon',  specialization: 'Cardiologist',       his_doctor_id: 'HIS-D001', available: true  },
  { id: 'd-002', name: 'Dr. Priya Nair',    specialization: 'Dermatologist',      his_doctor_id: 'HIS-D002', available: true  },
  { id: 'd-003', name: 'Dr. Vikram Shah',   specialization: 'Orthopaedic',        his_doctor_id: 'HIS-D003', available: true  },
  { id: 'd-004', name: 'Dr. Ananya Rao',    specialization: 'General Physician',  his_doctor_id: 'HIS-D004', available: false },
  { id: 'd-005', name: 'Dr. Kavya Iyer',    specialization: 'Gynaecologist',      his_doctor_id: 'HIS-D005', available: true  },
  { id: 'd-006', name: 'Dr. Rohan Gupta',   specialization: 'ENT',                his_doctor_id: 'HIS-D006', available: true  },
];

// ── Fixture appointments ───────────────────────────────────────────────────
export interface Appointment {
  id: string;
  patient_phone: string;
  doctor: string;
  specialization: string;
  slot_time: string;
  booked_via: string;
  call_id: string;
  status: 'CONFIRMED' | 'CANCELLED' | 'PENDING';
}

export const FIXTURE_APPOINTMENTS: Appointment[] = [
  {
    id: 'APT-2847',
    patient_phone: '+91 98XX XXXX34',
    doctor: 'Dr. Suresh Menon',
    specialization: 'Cardiology',
    slot_time: 'Today, 11:00 AM',
    booked_via: 'AI Voice',
    call_id: 'call-001',
    status: 'CONFIRMED',
  },
  {
    id: 'APT-2848',
    patient_phone: '+91 77XX XXXX87',
    doctor: 'Dr. Priya Nair',
    specialization: 'Dermatology',
    slot_time: 'Tomorrow, 10:30 AM',
    booked_via: 'AI Voice',
    call_id: 'call-004',
    status: 'CONFIRMED',
  },
  {
    id: 'APT-2849',
    patient_phone: '+91 91XX XXXX55',
    doctor: 'Dr. Vikram Shah',
    specialization: 'Orthopaedic',
    slot_time: 'Today, 4:30 PM',
    booked_via: 'AI Voice',
    call_id: 'call-006',
    status: 'CONFIRMED',
  },
  {
    id: 'APT-2850',
    patient_phone: '+91 82XX XXXX11',
    doctor: 'Dr. Ananya Rao',
    specialization: 'General Physician',
    slot_time: 'Today, 2:00 PM',
    booked_via: 'AI Voice',
    call_id: 'call-007',
    status: 'CANCELLED',
  },
];

// ── Fixture call logs ──────────────────────────────────────────────────────
export interface CallLog {
  id: string;
  phone: string;
  date: string;
  duration: string;
  intent: 'Appointment' | 'Emergency' | 'General Query' | 'Cancellation';
  status: 'Booked' | 'Transferred' | 'Resolved' | 'Failed' | 'Pending';
  language: string;
  flag: string;
  transcript: { role: 'ai' | 'patient'; text: string; time: string }[];
}

export const FIXTURE_CALL_LOGS: CallLog[] = [
  {
    id: 'CL-4921',
    phone: '+91 98XX XXXX34',
    date: '1 Apr 2026, 14:30',
    duration: '2:14',
    intent: 'Appointment',
    status: 'Booked',
    language: 'Hindi',
    flag: '🇮🇳',
    transcript: [
      { role: 'ai', text: 'Namaste! Apollo Clinic mein aapka swagat hai. Main aapki kaise madad kar sakta hoon?', time: '14:30:01' },
      { role: 'patient', text: 'Mujhe cardiologist se milna hai.', time: '14:30:08' },
      { role: 'ai', text: 'Dr. Suresh Menon kal 11:00 AM aur Guruwar 2:30 PM pe available hain. Kaunsa time theek rahega?', time: '14:30:14' },
      { role: 'patient', text: 'Kal 11 baje.', time: '14:30:20' },
      { role: 'ai', text: 'Dr. Menon ke saath kal 11:00 AM ki appointment confirm ho gayi. SMS confirmation bheja jayega.', time: '14:30:26' },
    ],
  },
  {
    id: 'CL-4920',
    phone: '+91 87XX XXXX21',
    date: '1 Apr 2026, 11:15',
    duration: '4:02',
    intent: 'Emergency',
    status: 'Transferred',
    language: 'English',
    flag: '🇬🇧',
    transcript: [
      { role: 'ai', text: 'Thank you for calling Apollo Clinic. How can I help you?', time: '11:15:00' },
      { role: 'patient', text: 'My father has severe chest pain!', time: '11:15:06' },
      { role: 'ai', text: 'I understand. Transferring you to our emergency line immediately. Please stay on the line.', time: '11:15:10' },
    ],
  },
  {
    id: 'CL-4919',
    phone: '+91 99XX XXXX09',
    date: '1 Apr 2026, 09:00',
    duration: '1:45',
    intent: 'General Query',
    status: 'Resolved',
    language: 'English',
    flag: '🇬🇧',
    transcript: [
      { role: 'ai', text: 'Thank you for calling Apollo Clinic. How can I help you?', time: '09:00:00' },
      { role: 'patient', text: 'What are your Saturday timings?', time: '09:00:08' },
      { role: 'ai', text: 'We are open Saturday 9:00 AM to 2:00 PM and closed on Sundays.', time: '09:00:14' },
    ],
  },
  {
    id: 'CL-4918',
    phone: '+91 77XX XXXX87',
    date: '1 Apr 2026, 08:22',
    duration: '3:11',
    intent: 'Appointment',
    status: 'Booked',
    language: 'Tamil',
    flag: '🇮🇳',
    transcript: [
      { role: 'ai', text: 'Apollo Clinic kku varavERkaReem. Engalukku eppadi udavi puriyalaam?', time: '08:22:00' },
      { role: 'patient', text: 'Ennakku ortho doctor paakanum.', time: '08:22:10' },
      { role: 'ai', text: 'Dr. Vikram Shah April 1 maalai 3:00 manikku irukkaaru. Book pannalaamaa?', time: '08:22:18' },
    ],
  },
  {
    id: 'CL-4917',
    phone: '+91 82XX XXXX11',
    date: '1 Apr 2026, 07:55',
    duration: '0:48',
    intent: 'Cancellation',
    status: 'Resolved',
    language: 'Hindi',
    flag: '🇮🇳',
    transcript: [
      { role: 'ai', text: 'Apollo Clinic, main aapki kaise sahayata kar sakta hoon?', time: '07:55:00' },
      { role: 'patient', text: 'Meri aaj ki appointment cancel karni hai.', time: '07:55:08' },
      { role: 'ai', text: 'APT-2850 cancel kar diya gaya hai. Koi aur madad?', time: '07:55:14' },
    ],
  },
];

// ── Specializations ────────────────────────────────────────────────────────
export const SPECIALIZATIONS = [
  'General Physician',
  'Cardiologist',
  'Dermatologist',
  'Orthopaedic',
  'Gynaecologist',
  'ENT',
  'Ophthalmologist',
  'Neurologist',
  'Psychiatrist',
  'Paediatrician',
];

// ── Super Admin Fixtures ───────────────────────────────────────────────────
export const FIXTURE_PLATFORM_STATS = {
  total_clinics: 24,
  active_this_month: 18,
  total_calls: 14847,
  total_bookings: 9234,
  mrr: 124000
};

export const FIXTURE_ALL_CLINICS = [
  { id: 'c1', name: 'Apollo Clinic', location: 'Mumbai', plan: 'Enterprise', joined: '10 Jan 2026', status: 'Active', calls_month: 1245, bookings: 890, res_rate: '94%', avg_latency: '780ms' },
  { id: 'c2', name: 'Fortis Healthcare', location: 'Delhi', plan: 'Enterprise', joined: '15 Jan 2026', status: 'Active', calls_month: 1024, bookings: 750, res_rate: '91%', avg_latency: '805ms' },
  { id: 'c3', name: 'Max Super Speciality', location: 'Gurugram', plan: 'Pro', joined: '22 Jan 2026', status: 'Active', calls_month: 856, bookings: 610, res_rate: '88%', avg_latency: '820ms' },
  { id: 'c4', name: 'Narayana Health', location: 'Bangalore', plan: 'Enterprise', joined: '05 Feb 2026', status: 'Active', calls_month: 2301, bookings: 1450, res_rate: '95%', avg_latency: '750ms' },
  { id: 'c5', name: 'Aster Medcity', location: 'Kochi', plan: 'Pro', joined: '18 Feb 2026', status: 'Active', calls_month: 620, bookings: 430, res_rate: '85%', avg_latency: '840ms' },
  { id: 'c6', name: 'Emirates Hospital', location: 'Dubai', plan: 'Enterprise', joined: '01 Mar 2026', status: 'Pending', calls_month: 0, bookings: 0, res_rate: '-', avg_latency: '-' },
  { id: 'c7', name: 'Cleveland Clinic', location: 'Abu Dhabi', plan: 'Enterprise', joined: '10 Mar 2026', status: 'Suspended', calls_month: 0, bookings: 0, res_rate: '-', avg_latency: '-' },
];

// ── Fixture agents ─────────────────────────────────────────────────────────
// IMPORTANT: These are fallback dev fixtures only.
// Real agents come from GET /agents. Stats always come from GET /agents/{id}/health.
// Hardcoded stats are intentionally 0 — real data is always fetched from the API.

export type AgentStatus = 'ACTIVE' | 'CONFIGURED' | 'ERROR' | 'INACTIVE';

export interface FixtureAgent {
  id: string;
  name: string;
  tenant_id: string;
  clinic_name: string;
  status: AgentStatus;
  template: string;
  first_message: string;
  stt_provider: string;
  stt_model: string;
  tts_provider: string;
  tts_model: string;
  tts_voice: string;
  tts_language: string;
  llm_provider: string;
  llm_model: string;
  ai_number: string;
  sip_provider: string;
  sip_status: string;
  calls_today: number;
  bookings_today: number;
  avg_latency_ms: number;
  resolution_rate: number;
  languages: string[];
}

export const FIXTURE_AGENTS: FixtureAgent[] = [
  {
    id: 'agent-001',
    name: 'Apollo Mumbai Receptionist',
    tenant_id: 'tenant-001',
    clinic_name: 'Apollo Multispeciality Mumbai',
    status: 'ACTIVE',
    template: 'clinic_receptionist',
    first_message: 'Namaste! Apollo Clinic mein aapka swagat hai. Main aapki receptionist hoon. Aaj main aapki kaise madad kar sakti hoon?',
    stt_provider: 'sarvam',
    stt_model: 'saarika:v2',
    tts_provider: 'sarvam',
    tts_model: 'bulbul:v3',
    tts_voice: 'meera',
    tts_language: 'hi-IN',
    llm_provider: 'groq',
    llm_model: 'llama-3.3-70b-versatile',
    ai_number: '+91 90001 23456',
    sip_provider: 'vobiz',
    sip_status: 'CONNECTED',
    calls_today: 0,
    bookings_today: 0,
    avg_latency_ms: 0,
    resolution_rate: 0,
    languages: ['Hindi', 'English'],
  },
  {
    id: 'agent-002',
    name: 'Aster Kochi Receptionist',
    tenant_id: 'tenant-002',
    clinic_name: 'Aster Medicity Kochi',
    status: 'ACTIVE',
    template: 'clinic_receptionist',
    first_message: 'നമസ്കാരം! Aster Medicity-ലേക്ക് സ്വാഗതം. ഞാൻ എങ്ങനെ സഹായിക്കാം?',
    stt_provider: 'sarvam',
    stt_model: 'saarika:v2',
    tts_provider: 'sarvam',
    tts_model: 'bulbul:v3',
    tts_voice: 'pavithra',
    tts_language: 'ml-IN',
    llm_provider: 'gemini',
    llm_model: 'gemini-2.0-flash',
    ai_number: '+91 90001 34567',
    sip_provider: 'vobiz',
    sip_status: 'CONNECTED',
    calls_today: 0,
    bookings_today: 0,
    avg_latency_ms: 0,
    resolution_rate: 0,
    languages: ['Malayalam', 'English'],
  },
  {
    id: 'agent-003',
    name: 'Al Zahra Dubai Receptionist',
    tenant_id: 'tenant-005',
    clinic_name: 'Al Zahra Hospital Dubai',
    status: 'CONFIGURED',
    template: 'clinic_receptionist',
    first_message: 'مرحباً! أهلاً وسهلاً بك في مستشفى الزهراء. كيف يمكنني مساعدتك؟',
    stt_provider: 'sarvam',
    stt_model: 'saarika:v2',
    tts_provider: 'sarvam',
    tts_model: 'bulbul:v3',
    tts_voice: 'amol',
    tts_language: 'ar-SA',
    llm_provider: 'gemini',
    llm_model: 'gemini-2.0-flash',
    ai_number: '+971 50001 12345',
    sip_provider: 'vobiz',
    sip_status: 'PENDING',
    calls_today: 0,
    bookings_today: 0,
    avg_latency_ms: 0,
    resolution_rate: 0,
    languages: ['Arabic', 'English'],
  },
];

export interface FixturePhoneNumber {
  id: string;
  tenant_id: string;
  agent_id: string | null;
  agent_name: string | null;
  number: string;
  country_code: string;
  country: string;
  provider: string;
  sip_uri: string;
  sip_username: string;
  sip_password: string;
  sip_domain: string;
  is_active: boolean;
  is_assigned: boolean;
  created_at: string;
}

export const FIXTURE_PHONE_NUMBERS: FixturePhoneNumber[] = [
  {
    id: 'pn-001',
    tenant_id: 'tenant-001',
    agent_id: 'agent-001',
    agent_name: 'Apollo Mumbai Receptionist',
    number: '+91 90001 23456',
    country_code: '+91',
    country: 'India',
    provider: 'Vobiz',
    sip_uri: 'sip:apollo-mumbai@lifodial.voice',
    sip_username: 'apollo_mumbai_main',
    sip_password: 'demo-pass-001',
    sip_domain: 'sip.vobiz.example',
    is_active: true,
    is_assigned: true,
    created_at: '2026-04-01T09:00:00Z',
  },
  {
    id: 'pn-002',
    tenant_id: 'tenant-002',
    agent_id: 'agent-002',
    agent_name: 'Aster Kochi Receptionist',
    number: '+91 90001 34567',
    country_code: '+91',
    country: 'India',
    provider: 'Exotel',
    sip_uri: 'sip:aster-kochi@lifodial.voice',
    sip_username: 'aster_kochi_frontdesk',
    sip_password: 'demo-pass-002',
    sip_domain: 'sip.exotel.example',
    is_active: true,
    is_assigned: true,
    created_at: '2026-04-03T11:30:00Z',
  },
  {
    id: 'pn-003',
    tenant_id: 'tenant-005',
    agent_id: null,
    agent_name: null,
    number: '+971 50001 12345',
    country_code: '+971',
    country: 'United Arab Emirates',
    provider: 'Custom SIP',
    sip_uri: 'sip:dubai-frontdesk@lifodial.voice',
    sip_username: 'dubai_frontdesk',
    sip_password: 'demo-pass-003',
    sip_domain: 'sip.custom.example',
    is_active: false,
    is_assigned: false,
    created_at: '2026-04-05T15:45:00Z',
  },
];

