"""Find connected groups from above-threshold comparisons in each scope."""

from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from uuid import UUID


@dataclass(frozen=True)
class SimilarityGroup:
    activity_id: int
    language_id: int
    members: tuple[tuple[UUID, int], ...]

    @property
    def max_score(self) -> int:
        return self.members[0][1]


def similarity_groups(comparisons):
    """Yield groups from rows ordered by activity and language."""
    for (activity_id, language_id), rows in groupby(comparisons, itemgetter("activity_id", "language_id")):
        parents = {}
        scores = {}

        def root(item):
            while parents[item] != item:
                parents[item] = parents[parents[item]]
                item = parents[item]
            return item

        for row in rows:
            left, right, score = row["left_id"], row["right_id"], row["score_bp"]
            for item in (left, right):
                parents.setdefault(item, item)
                scores[item] = max(scores.get(item, 0), score)
            parents[root(right)] = root(left)

        members = defaultdict(list)
        for item, score in scores.items():
            members[root(item)].append((item, score))
        for group in members.values():
            group.sort(key=lambda member: (-member[1], member[0]))
            yield SimilarityGroup(activity_id, language_id, tuple(group))
