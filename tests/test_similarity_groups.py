import unittest
from uuid import UUID

from paridad.similarity_groups import similarity_groups


def comparison(activity, language, left, right, score):
    return dict(activity_id=activity, language_id=language, left_id=UUID(int=left),
                right_id=UUID(int=right), score_bp=score)


class SimilarityGroupTests(unittest.TestCase):
    def test_connected_files_are_sorted_by_their_strongest_match(self):
        groups = list(similarity_groups([
            comparison(1, 2, 1, 2, 6400),
            comparison(1, 2, 3, 4, 7200),
            comparison(1, 2, 2, 3, 9300),
            comparison(1, 2, 5, 6, 8100),
        ]))
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].max_score, 9300)
        self.assertEqual([(member.int, score) for member, score in groups[0].members],
                         [(2, 9300), (3, 9300), (4, 7200), (1, 6400)])
        self.assertEqual([member.int for member, _ in groups[1].members], [5, 6])

    def test_scopes_do_not_merge(self):
        groups = list(similarity_groups([
            comparison(1, 2, 1, 2, 9000),
            comparison(1, 3, 2, 3, 8500),
            comparison(2, 3, 3, 4, 7500),
        ]))
        self.assertEqual([(g.activity_id, g.language_id) for g in groups], [(1, 2), (1, 3), (2, 3)])
        self.assertTrue(all(len(g.members) == 2 for g in groups))


if __name__ == "__main__":
    unittest.main()
