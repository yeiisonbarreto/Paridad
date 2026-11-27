ALTER TABLE grade_clarifications
    ADD COLUMN grade_at_resolution smallint CHECK (grade_at_resolution BETWEEN 0 AND 50);

ALTER TABLE review_notices
    ADD COLUMN clarification_id uuid REFERENCES grade_clarifications(submission_id) ON DELETE CASCADE,
    ADD COLUMN activity_at timestamptz,
    ADD CHECK (clarification_id IS NULL OR submission_id IS NULL);

UPDATE review_notices SET activity_at = created_at;
ALTER TABLE review_notices
    ALTER COLUMN activity_at SET NOT NULL,
    ALTER COLUMN activity_at SET DEFAULT now();

CREATE UNIQUE INDEX review_notices_clarification_idx ON review_notices(clarification_id)
    WHERE clarification_id IS NOT NULL;
DROP INDEX review_notices_student_idx;
CREATE INDEX review_notices_student_idx ON review_notices(student_id, activity_at DESC, id DESC);

INSERT INTO review_notices (student_id, clarification_id, created_at, activity_at, read_at)
SELECT s.student_id, c.submission_id, c.created_at, COALESCE(c.resolved_at, c.created_at), c.resolved_at
FROM grade_clarifications c JOIN submissions s ON s.id = c.submission_id;
