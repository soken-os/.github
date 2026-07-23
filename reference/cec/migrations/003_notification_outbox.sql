CREATE TABLE IF NOT EXISTS cec.notification_outbox (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_item_id        text NOT NULL REFERENCES cec.work_items(id),
    transition_event_id bigint NOT NULL UNIQUE REFERENCES cec.events(id),
    notification_type   text NOT NULL,
    destination         text NOT NULL,
    body_markdown       text NOT NULL,
    status              text NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','DELIVERED','ACKNOWLEDGED')),
    delivery_attempts   integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    delivered_path      text,
    created_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    delivered_at        timestamptz,
    acknowledged_at     timestamptz,
    acknowledged_by     text,
    CONSTRAINT notification_delivery_state CHECK (
        (status = 'PENDING' AND delivered_at IS NULL AND acknowledged_at IS NULL)
        OR (status = 'DELIVERED' AND delivered_at IS NOT NULL AND acknowledged_at IS NULL)
        OR (status = 'ACKNOWLEDGED' AND delivered_at IS NOT NULL
            AND acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION cec.enqueue_completion_notification()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_type = 'COMPLETE' AND NEW.payload ? 'notification_markdown' THEN
        INSERT INTO cec.notification_outbox
            (work_item_id,transition_event_id,notification_type,destination,body_markdown)
        VALUES
            (NEW.work_item_id,NEW.id,'COMPLETE','BRIDGE_MARKDOWN',
             NEW.payload->>'notification_markdown')
        ON CONFLICT (transition_event_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS completion_notification_outbox ON cec.events;
CREATE TRIGGER completion_notification_outbox
AFTER INSERT ON cec.events
FOR EACH ROW EXECUTE FUNCTION cec.enqueue_completion_notification();
