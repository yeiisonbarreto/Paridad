CREATE TABLE users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
    display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 120),
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('ADMIN', 'STUDENT')),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_id);
CREATE INDEX sessions_expiry_idx ON sessions(expires_at);

CREATE TABLE rate_limits (
    bucket text NOT NULL,
    subject text NOT NULL,
    window_start timestamptz NOT NULL,
    hits integer NOT NULL CHECK (hits > 0),
    PRIMARY KEY (bucket, subject, window_start)
);
CREATE INDEX rate_limits_expiry_idx ON rate_limits(window_start);

CREATE TABLE settings (
    id integer PRIMARY KEY CHECK (id = 1),
    similarity_threshold smallint NOT NULL DEFAULT 40 CHECK (similarity_threshold BETWEEN 0 AND 100),
    ai_threshold smallint NOT NULL DEFAULT 30 CHECK (ai_threshold BETWEEN 0 AND 100)
);
INSERT INTO settings (id) VALUES (1);

CREATE TABLE languages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_-]{0,31}$'),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    profile text NOT NULL CHECK (profile IN ('c', 'marie', 'arm', 'verilog', 'vhdl', 'generic')),
    extensions text[] NOT NULL CHECK (cardinality(extensions) BETWEEN 1 AND 12),
    options jsonb NOT NULL DEFAULT '{}'
);
INSERT INTO languages (code, name, profile, extensions) VALUES
    ('c', 'C', 'c', ARRAY['.c', '.h']),
    ('marie', 'Ensamblador MARIE', 'marie', ARRAY['.mas']),
    ('arm', 'Ensamblador ARM', 'arm', ARRAY['.s', '.asm']),
    ('verilog', 'HDL · Verilog / SystemVerilog', 'verilog', ARRAY['.v', '.vh', '.sv', '.svh']),
    ('vhdl', 'HDL · VHDL', 'vhdl', ARRAY['.vhd', '.vhdl']);

CREATE TABLE activities (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_-]{0,31}$'),
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    is_open boolean NOT NULL DEFAULT true,
    similarity_threshold smallint CHECK (similarity_threshold BETWEEN 0 AND 100),
    ai_threshold smallint CHECK (ai_threshold BETWEEN 0 AND 100)
);

CREATE TABLE activity_languages (
    activity_id bigint NOT NULL REFERENCES activities(id),
    language_id bigint NOT NULL REFERENCES languages(id),
    PRIMARY KEY (activity_id, language_id)
);
CREATE INDEX activity_languages_language_idx ON activity_languages(language_id);

CREATE TABLE submissions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    activity_id bigint NOT NULL,
    language_id bigint NOT NULL,
    student_id bigint NOT NULL REFERENCES users(id),
    request_id uuid NOT NULL,
    filename text NOT NULL CHECK (char_length(filename) BETWEEN 1 AND 120),
    source text NOT NULL CHECK (octet_length(source) BETWEEN 1 AND 131072),
    ai_credit text CHECK (char_length(ai_credit) BETWEEN 1 AND 1000),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'completed', 'failed')),
    attempts smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    analyzed_at timestamptz,
    analysis jsonb,
    ai_bp integer CHECK (ai_bp BETWEEN 0 AND 10000),
    ai_features jsonb,
    analysis_version text,
    max_similarity_bp integer NOT NULL DEFAULT 0 CHECK (max_similarity_bp BETWEEN 0 AND 10000),
    FOREIGN KEY (activity_id, language_id) REFERENCES activity_languages(activity_id, language_id),
    UNIQUE (student_id, request_id),
    UNIQUE (activity_id, language_id, id, student_id),
    CHECK (
        (state = 'completed' AND analysis IS NOT NULL AND ai_bp IS NOT NULL
         AND ai_features IS NOT NULL AND analyzed_at IS NOT NULL AND analysis_version IS NOT NULL)
        OR
        (state <> 'completed' AND analysis IS NULL AND ai_bp IS NULL
         AND ai_features IS NULL AND analyzed_at IS NULL AND analysis_version IS NULL AND max_similarity_bp = 0)
    )
);
CREATE INDEX submissions_owner_history_idx ON submissions(student_id, created_at DESC, id DESC);
CREATE INDEX submissions_queue_idx ON submissions(activity_id, language_id, available_at, created_at, id)
    WHERE state = 'pending';
CREATE INDEX submissions_completed_idx ON submissions(activity_id, language_id, student_id)
    WHERE state = 'completed';

CREATE TABLE similarity_pairs (
    activity_id bigint NOT NULL,
    language_id bigint NOT NULL,
    left_id uuid NOT NULL,
    left_student_id bigint NOT NULL,
    right_id uuid NOT NULL,
    right_student_id bigint NOT NULL,
    score_bp integer NOT NULL CHECK (score_bp BETWEEN 0 AND 10000),
    metrics jsonb NOT NULL,
    fragments jsonb NOT NULL,
    analysis_version text NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (left_id, right_id),
    CHECK (left_id < right_id AND left_student_id <> right_student_id),
    FOREIGN KEY (activity_id, language_id, left_id, left_student_id)
        REFERENCES submissions(activity_id, language_id, id, student_id),
    FOREIGN KEY (activity_id, language_id, right_id, right_student_id)
        REFERENCES submissions(activity_id, language_id, id, student_id)
);
CREATE INDEX similarity_pairs_report_idx ON similarity_pairs(activity_id, language_id, score_bp DESC, left_id, right_id);
CREATE INDEX similarity_pairs_right_idx ON similarity_pairs(right_id);
