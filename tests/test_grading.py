import unittest

from paridad.analysis import tokenize
from paridad.grading import static_rubric


class StaticRubricTest(unittest.TestCase):
    def rubric(self, source, language="c"):
        return static_rubric(source, language, tokenize(source, language, {}))

    def test_comments_must_be_present_and_in_english(self):
        code = "int sum_values(void) {\n    return 3;\n}\n"
        self.assertEqual(self.rubric(code)["english_comments"], 0)
        self.assertEqual(self.rubric("// Suma los valores\n" + code)["english_comments"], 0)
        self.assertEqual(self.rubric("// Return the total\n" + code)["english_comments"], 10)
        mixed = self.rubric("// Return the total\n// Guarda los valores\n" + code)
        self.assertEqual(mixed["english_comments"], 5)
        self.assertEqual((mixed["comments_english"], mixed["comments_found"]), (1, 2))

    def test_structure_and_practices_report_independent_points(self):
        clean = self.rubric("int process_value(void) {\n    return 0;\n}\n")
        risky = self.rubric("int process_value(void) {\n\t gets(buffer); // TODO\n    return 0;\n")
        self.assertEqual(clean["structure"], 15)
        self.assertLess(risky["structure"], clean["structure"])
        self.assertLess(risky["practices"], clean["practices"])

    def test_assembly_comment_uses_the_language_profile(self):
        self.assertEqual(self.rubric("LOAD Count / Load the current count\nHALT", "marie")["english_comments"], 10)
