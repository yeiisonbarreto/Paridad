import unittest

from paridad.analysis import tokenize
from paridad.grading import quality_points, static_rubric


class StaticRubricTest(unittest.TestCase):
    def rubric(self, source, language="c"):
        return static_rubric(source, language, tokenize(source, language, {}))

    def test_comments_assess_description_independently_of_language(self):
        code = "int sum_values(void) {\n    return 3;\n}\n"
        self.assertEqual(self.rubric(code)["comments"], 0)
        self.assertEqual(self.rubric("// Suma los valores\n" + code)["comments"], 5)
        self.assertEqual(self.rubric("// Return the total\n" + code)["comments"], 5)
        mixed = self.rubric("// Return the total\n// TODO finish this part\n" + code)
        self.assertEqual(mixed["comments"], 2)
        self.assertEqual((mixed["comments_descriptive"], mixed["comments_found"]), (1, 2))

    def test_structure_and_practices_report_independent_points(self):
        clean = self.rubric("int process_value(void) {\n    return 0;\n}\n")
        risky = self.rubric("int process_value(void) {\n\t gets(buffer); // TODO\n    return 0;\n")
        self.assertEqual(clean["structure"], 10)
        self.assertLess(risky["structure"], clean["structure"])
        self.assertLess(risky["practices"], clean["practices"])

    def test_assembly_comment_uses_the_language_profile(self):
        self.assertEqual(self.rubric("LOAD Count / Load the current count\nHALT", "marie")["comments"], 5)

    def test_quality_points_distinguish_readability_and_maintainability(self):
        clean = self.rubric("// Explain this fixture\nint process_value(void) { return 0; }")
        deep = self.rubric("int process_value(void) { " + "if (1) { " * 7 + "return 0; " + "}" * 8)
        wide = self.rubric("int process_value(void) { " + " " * 125 + "return 0; }")
        self.assertEqual(quality_points(clean), 35)
        self.assertLess(deep["maintainability"], clean["maintainability"])
        self.assertLess(wide["readability"], clean["readability"])
