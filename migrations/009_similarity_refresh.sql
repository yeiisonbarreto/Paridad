CREATE INDEX similarity_pairs_legacy_idx
    ON similarity_pairs(activity_id, language_id, left_id, right_id)
    WHERE analysis_version = 'lexical-1';

UPDATE users
SET display_name = 'Administrador'
WHERE role = 'ADMIN' AND lower(btrim(display_name)) = 'docente administrador';
