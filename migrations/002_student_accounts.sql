ALTER TABLE users DROP CONSTRAINT users_username_check;
ALTER TABLE users ADD CONSTRAINT users_username_check CHECK (username ~ '^[a-z0-9][a-z0-9._-]{0,63}$');
ALTER TABLE users ADD COLUMN email text CHECK (char_length(email) BETWEEN 3 AND 254);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email)) WHERE email IS NOT NULL;

CREATE TABLE student_invitations (
    user_id bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    expires_at timestamptz NOT NULL
);
CREATE INDEX student_invitations_expiry_idx ON student_invitations(expires_at);
