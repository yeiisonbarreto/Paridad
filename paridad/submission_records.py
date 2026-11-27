"""Remove submissions and their pair results while keeping surviving scores accurate."""

from uuid import UUID


def lock_submission_scopes(conn, submission_ids: list[UUID]) -> None:
    conn.execute(
        """SELECT al.activity_id, al.language_id FROM activity_languages al
           WHERE EXISTS (SELECT 1 FROM submissions s WHERE s.id = ANY(%s)
               AND s.activity_id = al.activity_id AND s.language_id = al.language_id)
           ORDER BY al.activity_id, al.language_id FOR NO KEY UPDATE OF al""",
        (submission_ids,),
    ).fetchall()


def delete_submissions(conn, submission_ids: list[UUID]) -> None:
    """The caller holds the activity/language locks for these submissions."""
    if not submission_ids:
        return
    pairs = conn.execute(
        """DELETE FROM similarity_pairs WHERE left_id = ANY(%s) OR right_id = ANY(%s)
           RETURNING left_id, right_id""",
        (submission_ids, submission_ids),
    ).fetchall()
    affected = {item for pair in pairs for item in (pair["left_id"], pair["right_id"])}
    affected.difference_update(submission_ids)
    conn.execute("DELETE FROM submissions WHERE id = ANY(%s)", (submission_ids,))
    if affected:
        conn.execute(
            """UPDATE submissions s SET max_similarity_bp = COALESCE(
                    (SELECT max(p.score_bp) FROM similarity_pairs p
                     WHERE p.left_id = s.id OR p.right_id = s.id), 0)
               WHERE s.id = ANY(%s)""",
            (list(affected),),
        )
