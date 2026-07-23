CREATE SCHEMA IF NOT EXISTS cec;
CREATE TABLE IF NOT EXISTS cec.work_items (
    id text PRIMARY KEY,
    program text NOT NULL,
    title text NOT NULL,
    task_class text NOT NULL,
    priority_class smallint NOT NULL CHECK (priority_class BETWEEN 0 AND 100),
    estimated_duration_seconds integer CHECK (estimated_duration_seconds > 0),
    deadline_at timestamptz,
    stage text NOT NULL CHECK (stage IN ('INTAKE','CONTRACTING','READY','EXECUTING','VERIFYING','ACCEPTING','PARKED','COMPLETE','CANCELLED')),
    wait_reason text NOT NULL CHECK (wait_reason IN ('NONE','WORKER','CI','REVIEW','MERGE','DEPLOY','HUMAN_DECISION','DEVICE_TEST','EXTERNAL_SERVICE','RETRY_BACKOFF','HELD_DEPENDENCY')),
    custodian_type text CHECK (custodian_type IS NULL OR custodian_type IN ('CONTROLLER','WORKER','EXTERNAL','HUMAN')),
    custodian_id text,
    lease_token uuid,
    lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at timestamptz,
    next_signal_type text,
    next_signal_key text,
    next_signal_deadline timestamptz,
    recovery_action jsonb,
    recovery_attempts integer NOT NULL DEFAULT 0 CHECK (recovery_attempts >= 0),
    max_recovery_attempts integer NOT NULL DEFAULT 3 CHECK (max_recovery_attempts > 0),
    CHECK (recovery_attempts <= max_recovery_attempts),
    desired_state jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(desired_state) = 'object'),
    evidence_state jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence_state) = 'object'),
    work_packet jsonb NOT NULL CHECK (jsonb_typeof(work_packet) = 'object'),
    external_refs jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(external_refs) = 'object'),
    authority_class text NOT NULL CHECK (authority_class IN ('ROUTINE','RESERVED','SCOTT_REQUIRED')),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    CONSTRAINT continuation_required CHECK (
        stage IN ('COMPLETE','CANCELLED') OR (
            custodian_type IS NOT NULL AND custodian_id IS NOT NULL
            AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL
            AND next_signal_type IS NOT NULL AND next_signal_deadline IS NOT NULL
            AND recovery_action IS NOT NULL AND jsonb_typeof(recovery_action) = 'object'
        )
    ),
    -- Write-time sanity only; reconciliation, not this CHECK, enforces liveness.
    CONSTRAINT continuation_deadlines_valid CHECK (
        stage IN ('COMPLETE','CANCELLED') OR (lease_expires_at > updated_at AND next_signal_deadline >= updated_at)
    ),
    CONSTRAINT terminal_completion_time CHECK (
        (stage = 'COMPLETE' AND completed_at IS NOT NULL) OR (stage <> 'COMPLETE' AND completed_at IS NULL)
    ),
    CONSTRAINT completion_requires_verified_evidence CHECK (
        stage <> 'COMPLETE' OR evidence_state @> '{"completion_verified": true}'::jsonb
    )
);
CREATE INDEX IF NOT EXISTS work_items_reconcile_due_idx ON cec.work_items (LEAST(lease_expires_at,next_signal_deadline)) WHERE stage NOT IN ('COMPLETE','CANCELLED');
CREATE INDEX IF NOT EXISTS work_items_ready_pull_idx ON cec.work_items (priority_class DESC,deadline_at ASC NULLS LAST,estimated_duration_seconds ASC NULLS LAST,created_at ASC) WHERE stage='READY';
CREATE INDEX IF NOT EXISTS work_items_custodian_idx ON cec.work_items (custodian_type,custodian_id) WHERE stage NOT IN ('COMPLETE','CANCELLED');
