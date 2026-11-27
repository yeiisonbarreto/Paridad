ALTER TABLE review_notices
    ADD COLUMN similarity_at_notice_bp integer CHECK (similarity_at_notice_bp BETWEEN 0 AND 10000),
    ADD COLUMN similarity_threshold_at_notice smallint CHECK (similarity_threshold_at_notice BETWEEN 0 AND 100),
    ADD COLUMN ai_at_notice_bp integer CHECK (ai_at_notice_bp BETWEEN 0 AND 10000),
    ADD COLUMN ai_threshold_at_notice smallint CHECK (ai_threshold_at_notice BETWEEN 0 AND 100),
    ADD COLUMN related_submission_ids uuid[] NOT NULL DEFAULT '{}';
