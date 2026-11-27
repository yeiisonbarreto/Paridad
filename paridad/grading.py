"""Explainable static rubric. Uploaded code is never executed."""

import re

WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
PLACEHOLDER = re.compile(r"\b(?:TODO|FIXME|HACK)\b", re.IGNORECASE)
CRITERIA = ("structure", "readability", "naming", "practices", "comments", "maintainability")
C_UNSAFE = {"ID:gets", "ID:strcpy", "ID:strcat", "ID:sprintf", "ID:atoi"}


def descriptive_comments(comments: list[str]) -> tuple[int, int]:
    descriptive = total = 0
    for comment in comments:
        words = {word.casefold() for word in WORDS.findall(comment)}
        if words:
            total += 1
            if len(words) >= 3 and not PLACEHOLDER.search(comment):
                descriptive += 1
    return descriptive, total


def quality_points(rubric: dict) -> int:
    return sum(rubric[criterion] for criterion in CRITERIA)


def static_rubric(source, profile, lexed):
    lines = source.split("\n")
    code_lines = [lines[index - 1] for index in sorted(set(lexed.lines))]
    indents = [line[:len(line) - len(line.lstrip())] for line in code_lines]
    styles = {"tab" if "\t" in prefix else "space" for prefix in indents if prefix}
    mixed = any("\t" in prefix and " " in prefix for prefix in indents)
    indentation = 5 if len(styles) <= 1 and not mixed else 2
    width = round(5 * sum(len(line.expandtabs(4)) <= 100 for line in code_lines) / len(code_lines))
    organization = 5
    maintainability = 5
    if profile in {"c", "verilog"}:
        depth = max_depth = 0
        for token in lexed.tokens:
            depth += (token == "P:{") - (token == "P:}")
            max_depth = max(max_depth, depth)
            if depth < 0:
                break
        organization = 5 if depth == 0 else 0
        maintainability -= min(5, max(0, max_depth - 4))
    elif len(code_lines) > 30:
        block = 0
        for line in lines:
            block = block + 1 if line.strip() else 0
            if block > 50:
                maintainability = 3
        organization = 5 if any(not line.strip() for line in lines[1:-1]) else 3

    naming = round(5 * sum(len(name.strip("._")) > 1 for name in lexed.identifiers) / max(1, len(lexed.identifiers)))
    if not lexed.identifiers:
        naming = 5
    unfinished = sum(bool(PLACEHOLDER.search(comment)) for comment in lexed.comments)
    unsafe = len(C_UNSAFE.intersection(lexed.tokens)) if profile == "c" else 0
    practices = max(0, 5 - 2 * unsafe - min(3, unfinished))
    descriptive, total = descriptive_comments(lexed.comments)
    comments = round(5 * descriptive / total) if total else 0
    return {
        "version": 2,
        "structure": indentation + organization,
        "readability": width,
        "naming": naming,
        "maintainability": maintainability,
        "practices": practices,
        "comments": comments,
        "comments_found": total,
        "comments_descriptive": descriptive,
    }
