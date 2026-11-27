import unittest

from paridad.reports import FRAGMENT_PAGE_LINES, fragment_window


class FragmentWindowTest(unittest.TestCase):
    def test_highlights_only_the_match_and_preserves_long_lines(self):
        lines = [f"code {number}" for number in range(1, 101)]
        lines[10] = "    " + ("long_name" * 30)
        source = "\n".join(lines)
        first_page, has_next = fragment_window(source, [10, 78], 1)
        self.assertEqual(len(first_page), FRAGMENT_PAGE_LINES)
        self.assertEqual(first_page[:4], [
            (7, "code 7", False), (8, "code 8", False), (9, "code 9", False), (10, "code 10", True),
        ])
        self.assertEqual(first_page[4], (11, lines[10], True))
        self.assertTrue(has_next)
        last_page, has_next = fragment_window(source, [10, 78], 2)
        self.assertEqual(last_page[0], (67, "code 67", True))
        self.assertEqual(last_page[-1], (81, "code 81", False))
        self.assertFalse(has_next)
