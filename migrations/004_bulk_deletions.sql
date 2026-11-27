CREATE TABLE bulk_deletions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('accounts', 'submissions')),
    targets jsonb NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX bulk_deletions_expiry_idx ON bulk_deletions(expires_at);
