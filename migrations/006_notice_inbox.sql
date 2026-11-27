DROP INDEX review_notices_pending_idx;

ALTER TABLE review_notices
    DROP COLUMN available_at,
    DROP COLUMN attempts,
    DROP COLUMN status,
    ADD COLUMN read_at timestamptz;

CREATE INDEX review_notices_unread_idx ON review_notices(student_id) WHERE read_at IS NULL;
