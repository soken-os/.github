CREATE TABLE IF NOT EXISTS cec.events (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_item_id        text NOT NULL REFERENCES cec.work_items(id),
    source              text NOT NULL,
    source_event_id     text NOT NULL,
    event_type          text NOT NULL,
    from_version        bigint NOT NULL CHECK (from_version > 0),
    to_version          bigint NOT NULL CHECK (to_version = from_version + 1),
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb
                        CHECK (jsonb_typeof(payload) = 'object'),
    observed_at         timestamptz NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (source, source_event_id)
);

CREATE OR REPLACE FUNCTION cec.reject_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'cec.events is append-only';
END;
$$;

DROP TRIGGER IF EXISTS events_append_only ON cec.events;
CREATE TRIGGER events_append_only
BEFORE UPDATE OR DELETE ON cec.events
FOR EACH ROW EXECUTE FUNCTION cec.reject_event_mutation();

CREATE OR REPLACE VIEW cec.stale_nonterminal_items AS
SELECT
    id,
    stage,
    wait_reason,
    custodian_type,
    custodian_id,
    lease_expires_at,
    next_signal_type,
    next_signal_deadline,
    recovery_action,
    CASE
        WHEN lease_expires_at < clock_timestamp() - interval '60 seconds'
            THEN 'LEASE_OVERDUE'
        ELSE 'SIGNAL_OVERDUE'
    END AS freshness_violation,
    60::integer AS grace_seconds
FROM cec.work_items
WHERE stage NOT IN ('COMPLETE', 'CANCELLED')
  AND (
      lease_expires_at < clock_timestamp() - interval '60 seconds'
      OR next_signal_deadline < clock_timestamp() - interval '60 seconds'
  );
