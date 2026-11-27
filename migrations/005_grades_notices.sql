ALTER TABLE submissions
    ADD COLUMN rubric jsonb,
    ADD COLUMN quality_points smallint CHECK (quality_points BETWEEN 0 AND 35),
    ADD COLUMN automatic_grade smallint GENERATED ALWAYS AS
        (quality_points + round(15 * (10000 - max_similarity_bp) / 10000.0)::smallint) STORED,
    ADD COLUMN grade_override smallint CHECK (grade_override BETWEEN 0 AND 50),
    ADD COLUMN grade_note text CHECK (char_length(grade_note) BETWEEN 1 AND 1500),
    ADD COLUMN noticed_similarity boolean NOT NULL DEFAULT false,
    ADD COLUMN noticed_ai boolean NOT NULL DEFAULT false;

CREATE INDEX submissions_rubric_pending_idx ON submissions(created_at, id)
    WHERE state = 'completed' AND rubric IS NULL;

CREATE TABLE grade_clarifications (
    submission_id uuid PRIMARY KEY REFERENCES submissions(id) ON DELETE CASCADE,
    grade_at_request smallint NOT NULL CHECK (grade_at_request BETWEEN 0 AND 50),
    reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 600),
    comments text CHECK (char_length(comments) BETWEEN 1 AND 800),
    created_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'rejected')),
    response text CHECK (char_length(response) BETWEEN 1 AND 1500),
    resolved_at timestamptz,
    CHECK ((status = 'pending' AND response IS NULL AND resolved_at IS NULL)
        OR (status <> 'pending' AND response IS NOT NULL AND resolved_at IS NOT NULL))
);
CREATE INDEX grade_clarifications_list_idx ON grade_clarifications(status, created_at DESC);

CREATE TABLE review_notices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    submission_id uuid REFERENCES submissions(id) ON DELETE CASCADE,
    similarity boolean NOT NULL DEFAULT false,
    ai boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    attempts smallint NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed', 'skipped')),
    CHECK ((submission_id IS NULL AND NOT similarity AND NOT ai)
        OR (submission_id IS NOT NULL AND (similarity OR ai)))
);
CREATE INDEX review_notices_pending_idx ON review_notices(available_at, created_at) WHERE status = 'queued';
CREATE INDEX review_notices_student_idx ON review_notices(student_id, created_at DESC);
