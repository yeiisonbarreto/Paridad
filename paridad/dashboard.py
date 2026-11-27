"""Aggregated administrative indicators for the selected delivery scope."""

SCOPE = """
    FROM submissions s JOIN activities a ON a.id = s.activity_id
    WHERE (%(module)s::bigint IS NULL OR a.module_id = %(module)s)
      AND (%(activity)s::bigint IS NULL OR a.id = %(activity)s)
      AND (%(language)s::bigint IS NULL OR s.language_id = %(language)s)
"""


def activity_metrics(conn, module_id: int | None, activity_id: int | None, language_id: int | None):
    scope = {"module": module_id, "activity": activity_id, "language": language_id}
    totals = conn.execute(
        """WITH scoped AS (
            SELECT s.student_id, s.state, s.ai_bp, s.max_similarity_bp, s.ai_credit,
                   COALESCE(s.grade_override, s.automatic_grade) AS grade,
                   a.similarity_threshold, a.ai_threshold""" + SCOPE + """
        )
        SELECT (SELECT count(*) FROM users WHERE role = 'STUDENT') AS enrolled,
               count(DISTINCT student_id) AS participants, count(*) AS submissions,
               count(*) FILTER (WHERE state = 'pending') AS pending,
               count(*) FILTER (WHERE state = 'failed') AS failed,
               count(*) FILTER (WHERE state = 'completed') AS analyzed,
               count(*) FILTER (WHERE state = 'completed' AND
                   max_similarity_bp > 100 * COALESCE(scoped.similarity_threshold, cfg.similarity_threshold)) AS similarity_alerts,
               count(*) FILTER (WHERE state = 'completed' AND ai_credit IS NULL AND
                   ai_bp > 100 * COALESCE(scoped.ai_threshold, cfg.ai_threshold)) AS ai_alerts,
               round(avg(max_similarity_bp) FILTER (WHERE state = 'completed') / 100.0, 1) AS similarity_mean,
               round(avg(ai_bp) FILTER (WHERE state = 'completed') / 100.0, 1) AS ai_mean,
               round(avg(grade) FILTER (WHERE state = 'completed'), 1) AS grade_mean,
               count(grade) FILTER (WHERE state = 'completed') AS graded
        FROM scoped CROSS JOIN settings cfg""", scope,
    ).fetchone()
    show_activities = module_id is not None or activity_id is not None
    key = "a.id" if show_activities else "a.module_id"
    catalog = (
        """SELECT a.id, a.code, a.title FROM activities a
           WHERE (%(module)s::bigint IS NULL OR a.module_id = %(module)s)
             AND (%(activity)s::bigint IS NULL OR a.id = %(activity)s)
             AND (%(language)s::bigint IS NULL OR EXISTS (
                 SELECT 1 FROM activity_languages al WHERE al.activity_id = a.id AND al.language_id = %(language)s))"""
        if show_activities else
        """SELECT m.id, m.code, m.title FROM modules m
           WHERE %(language)s::bigint IS NULL OR EXISTS (
               SELECT 1 FROM activities a JOIN activity_languages al ON al.activity_id = a.id
               WHERE a.module_id = m.id AND al.language_id = %(language)s)"""
    )
    participation = conn.execute(
        f"""WITH counts AS (
            SELECT {key} AS id, count(DISTINCT s.student_id) AS students, count(*) AS files
            {SCOPE} GROUP BY {key}
        ), catalog AS ({catalog})
        SELECT catalog.*, COALESCE(counts.students, 0) AS students, COALESCE(counts.files, 0) AS files
        FROM catalog LEFT JOIN counts USING (id) ORDER BY catalog.code""", scope,
    ).fetchall()
    return totals, participation
