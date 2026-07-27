BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 1a2b3c4d5e6f

INSERT INTO alembic_version (version_num) VALUES ('1a2b3c4d5e6f') RETURNING alembic_version.version_num;

-- Running upgrade 1a2b3c4d5e6f -> 492f7296aff8

CREATE TABLE tenants (
    id VARCHAR(36) NOT NULL, 
    clinic_name VARCHAR(255) NOT NULL, 
    admin_name VARCHAR(255), 
    admin_email VARCHAR(255), 
    phone VARCHAR(30), 
    location VARCHAR(255), 
    status VARCHAR(20) NOT NULL, 
    admin_password VARCHAR(100), 
    ai_number VARCHAR(30), 
    language VARCHAR(10) NOT NULL, 
    is_active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (ai_number)
);

CREATE TABLE call_logs (
    id VARCHAR(36) NOT NULL, 
    tenant_id VARCHAR(36) NOT NULL, 
    call_id VARCHAR(100) NOT NULL, 
    caller_phone VARCHAR(30), 
    duration_secs INTEGER, 
    outcome VARCHAR(30) NOT NULL, 
    detected_lang VARCHAR(10), 
    turn_count INTEGER NOT NULL, 
    transcript_json JSON, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    ended_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_call_logs_call_id ON call_logs (call_id);

CREATE INDEX ix_call_logs_outcome ON call_logs (outcome);

CREATE INDEX ix_call_logs_tenant_id ON call_logs (tenant_id);

CREATE TABLE doctors (
    id VARCHAR(36) NOT NULL, 
    tenant_id VARCHAR(36) NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    specialization VARCHAR(255) NOT NULL, 
    his_doctor_id VARCHAR(100), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

CREATE INDEX ix_doctors_tenant_id ON doctors (tenant_id);

CREATE TABLE appointments (
    id VARCHAR(36) NOT NULL, 
    tenant_id VARCHAR(36) NOT NULL, 
    doctor_id VARCHAR(36) NOT NULL, 
    slot_time TIMESTAMP WITH TIME ZONE NOT NULL, 
    patient_phone VARCHAR(30) NOT NULL, 
    patient_name VARCHAR(255), 
    status VARCHAR(20) NOT NULL, 
    his_booking_id VARCHAR(100), 
    call_id VARCHAR(100), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(doctor_id) REFERENCES doctors (id) ON DELETE SET NULL, 
    FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

CREATE INDEX ix_appointments_status ON appointments (status);

CREATE INDEX ix_appointments_tenant_id ON appointments (tenant_id);

UPDATE alembic_version SET version_num='492f7296aff8' WHERE alembic_version.version_num = '1a2b3c4d5e6f';

-- Running upgrade 492f7296aff8 -> bbf25bb3c633

CREATE TABLE agent_configs (
    id VARCHAR(36) NOT NULL, 
    tenant_id VARCHAR(36) NOT NULL, 
    agent_name VARCHAR(255) NOT NULL, 
    template VARCHAR(50) NOT NULL, 
    first_message TEXT NOT NULL, 
    first_message_mode VARCHAR(50) NOT NULL, 
    system_prompt TEXT NOT NULL, 
    stt_provider VARCHAR(50) NOT NULL, 
    stt_model VARCHAR(100), 
    stt_language VARCHAR(20) NOT NULL, 
    transcriber_keywords TEXT, 
    fallback_transcribers TEXT, 
    tts_provider VARCHAR(50) NOT NULL, 
    tts_model VARCHAR(100), 
    tts_voice VARCHAR(50), 
    tts_language VARCHAR(20) NOT NULL, 
    tts_pitch FLOAT NOT NULL, 
    tts_pace FLOAT NOT NULL, 
    tts_loudness FLOAT NOT NULL, 
    tts_stability FLOAT NOT NULL, 
    tts_clarity FLOAT NOT NULL, 
    tts_speed FLOAT NOT NULL, 
    tts_style FLOAT NOT NULL, 
    tts_use_speaker_boost INTEGER NOT NULL, 
    tts_optimize_streaming_latency INTEGER NOT NULL, 
    tts_input_preprocessing INTEGER NOT NULL, 
    tts_filler_injection INTEGER NOT NULL, 
    add_voice_manually VARCHAR(255), 
    fallback_voices TEXT, 
    llm_provider VARCHAR(50) NOT NULL, 
    llm_model VARCHAR(100) NOT NULL, 
    llm_temperature FLOAT NOT NULL, 
    max_response_tokens INTEGER NOT NULL, 
    llm_max_tokens INTEGER NOT NULL, 
    llm_emotion_recognition INTEGER NOT NULL, 
    silence_timeout_seconds INTEGER NOT NULL, 
    max_duration_seconds INTEGER NOT NULL, 
    background_sound VARCHAR(50) NOT NULL, 
    background_denoising INTEGER NOT NULL, 
    model_output_in_realtime INTEGER NOT NULL, 
    record_calls INTEGER NOT NULL, 
    recording_consent_plan VARCHAR(50), 
    voicemail_detection_enabled INTEGER NOT NULL, 
    voicemail_message TEXT, 
    end_call_phrases TEXT, 
    end_call_message TEXT, 
    summary_enabled INTEGER NOT NULL, 
    success_evaluation_enabled INTEGER NOT NULL, 
    structured_output_enabled INTEGER NOT NULL, 
    tools_enabled TEXT, 
    predefined_functions TEXT, 
    custom_functions TEXT, 
    keypad_input_enabled INTEGER NOT NULL, 
    keypad_timeout INTEGER NOT NULL, 
    sms_enabled INTEGER NOT NULL, 
    sms_provider VARCHAR(50), 
    sms_message_template TEXT, 
    hipaa_enabled INTEGER NOT NULL, 
    pii_redaction_enabled INTEGER NOT NULL, 
    telephony_option VARCHAR(20) NOT NULL, 
    country_code VARCHAR(5), 
    ai_number VARCHAR(30), 
    sip_provider VARCHAR(50), 
    sip_account_sid VARCHAR(255), 
    sip_auth_token VARCHAR(255), 
    sip_domain VARCHAR(255), 
    livekit_url VARCHAR(255), 
    livekit_api_key VARCHAR(255), 
    livekit_api_secret VARCHAR(255), 
    existing_clinic_number VARCHAR(30), 
    status VARCHAR(20) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_configs_status ON agent_configs (status);

CREATE INDEX ix_agent_configs_tenant_id ON agent_configs (tenant_id);

UPDATE alembic_version SET version_num='bbf25bb3c633' WHERE alembic_version.version_num = '492f7296aff8';

COMMIT;

