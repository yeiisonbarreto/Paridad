ALTER TABLE submissions
    ADD COLUMN source_bytes bytea CHECK (octet_length(source_bytes) BETWEEN 1 AND 131072);
