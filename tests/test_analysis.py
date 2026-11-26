import unittest

from paridad.analysis import Prepared, analyze_style, canonical, compare, tokenize


def prepare(source: str, profile: str = "c", options=None) -> Prepared:
    lexed = tokenize(source, profile, options or {})
    return Prepared.from_analysis({"tokens": lexed.tokens, "lines": lexed.lines})


class AnalysisTests(unittest.TestCase):
    def test_c_detects_renames_keeps_original_line_numbers(self):
        a = prepare("int calc(int x) {\n // a comment\n int total = x + 3;\n return total;\n}\n")
        b = prepare("/* another note */\nint run (int y) {\n int sum=y+99;\n return sum;\n}\n")
        score, metrics, fragments = compare(a, b)
        self.assertEqual(score, 10000)
        self.assertEqual(metrics["coverage_bp"], 10000)
        self.assertEqual(fragments, [{"left": [1, 5], "right": [2, 5], "tokens": len(a.tokens)}])

    def test_c_local_identity_and_comment_delimiters_inside_strings(self):
        profile = prepare('char *x = "/* guard */ //";\nint y=1;\n')
        self.assertIn("L:string", profile.tokens)
        self.assertIn("K:int", profile.tokens)
        self.assertNotEqual(canonical(["ID:a", "ID:a"]), canonical(["ID:a", "ID:b"]))

    def test_reordered_functions_survive(self):
        a = "int a(int x) { int q = x * x; return q + x; }\n"
        b = "int b(int y) { while (y > 0) { y--; } return !y; }\n"
        score, metrics, fragments = compare(prepare(a + b), prepare(b + a))
        self.assertGreater(score, 6500)
        self.assertGreaterEqual(metrics["block_count"], 2)
        self.assertTrue(any(p["left"][0] == 1 and p["right"][0] == 2 for p in fragments))

    def test_marie_labels_register_free_case_and_conditions(self):
        a = prepare("LoOp, LOAD Value / comment\nADD Value\nSKIPCOND 400\nJUMP Loop\nValue, DEC 6\n", "marie")
        b = prepare("start, load val\nadd val\nskipcond 400\njump start\nval, dec 9\n", "marie")
        self.assertEqual(compare(a, b)[0], 10000)
        self.assertIn("P:EOL", a.tokens)
        self.assertIn("K:skipcond/400", a.tokens)
        self.assertIn("K:skipcond/c00", prepare("SKIPCOND C00\n", "marie").tokens)

    def test_arm_registers_and_condition_codes(self):
        a = prepare("Loop: MOV r0, #1 @ comentario\nadds r1, r1, #2\nBNE Loop\n", "arm")
        b = prepare("next: mov R0, #5\nadds R1, R1, #9\nbne next\n", "arm")
        self.assertEqual(compare(a, b)[0], 10000)
        self.assertIn("REG:r0", a.tokens)
        self.assertIn("K:bne", a.tokens)
        self.assertIn("P:#", a.tokens)

    def test_verilog_hdl_and_literals(self):
        a = prepare("module top(input clk, output logic q);\nalways_ff @(posedge clk) q <= 1'b0;\nendmodule\n", "verilog")
        b = prepare("module other(input gate, output logic y); // note\nalways_ff @(posedge gate) y <= 1'b1;\nendmodule\n", "verilog")
        self.assertEqual(compare(a, b)[0], 10000)
        self.assertIn("K:posedge", a.tokens)
        self.assertEqual(a.tokens.count("L:number"), 1)

    def test_vhdl_case_signals_and_attribute(self):
        a = prepare("ENTITY a IS\n PORT(c: IN std_logic);\nEND; -- comment\nIF c'event THEN q <= '0'; END IF;\n", "vhdl")
        b = prepare("entity name2 is\n port(cl: in STD_LOGIC);\nend;\nif cl'event then z <= '1'; end if;\n", "vhdl")
        self.assertEqual(compare(a, b)[0], 10000)
        self.assertIn("K:event", a.tokens)
        self.assertIn("L:char", a.tokens)

    def test_configured_profile_and_empty_file(self):
        options = {"comments": "hash", "case_insensitive": True, "keywords": ["print"]}
        a = prepare("# comment\nPRINT(foo)\n", "generic", options)
        b = prepare("print(bar)\n", "generic", options)
        self.assertIn("K:print", a.tokens)
        self.assertEqual(compare(a, b)[0], 10000)
        with self.assertRaises(ValueError):
            prepare("/* comentario */", "c")

    def test_short_samples_and_unrelated_code(self):
        size_a = prepare("HALT", "marie")
        size_b = prepare("LOAD x", "marie")
        self.assertEqual(compare(size_a, size_a)[:2], (
            10000, {"short_sample": True, "dice_bp": 10000, "coverage_bp": 10000, "block_count": 1},
        ))
        self.assertEqual(compare(size_a, size_b)[0], 0)
        self.assertEqual(compare(prepare("int x = 2;"), prepare("char *y;"))[0], 0)

    def test_style_score_is_bounded_and_explained(self):
        short, evidence = analyze_style(tokenize("int x;", "c", {}))
        self.assertLess(short, 3000)
        self.assertLess(evidence["sample_factor"], 1)
        source = "int process_value(int initial_value) {\n" + (
            " // Step later, check the input value before returning.\n"
            " if (initial_value > 0) initial_value--;\n"
        ) * 12 + "return initial_value;\n}"
        long, evidence = analyze_style(tokenize(source, "c", {}))
        self.assertGreater(long, 3000)
        self.assertLessEqual(long, 10000)
        self.assertAlmostEqual(sum(x["points"] for x in evidence["features"]), long / 100, delta=0.03)


if __name__ == "__main__":
    unittest.main()
