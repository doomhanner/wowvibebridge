import json
import math
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from luasv import LuaSVParseError, jsonable, parse_savedvariables  # noqa


RXP_FIXTURE = """
RXPData = {
["maxLoadTime"] = 4000,
["gameVersion"] = 16001,
["realmData"] = {
["CAMWOW US2 Realm 8 PvP"] = {
},
},
}
RXPSettings = {
["framePositions"] = {
["RXPFrame"] = {
{
"BOTTOMRIGHT",
nil,
"BOTTOMRIGHT",
-293.1947937011719,
88.94409942626953,
},
},
},
}
"""


def p(text):
    return parse_savedvariables(text)


class TestFixture(unittest.TestCase):
    def test_rxp_shape_lf(self):
        d = p(RXP_FIXTURE)
        self.assertEqual(list(d), ["RXPData", "RXPSettings"])
        self.assertEqual(d["RXPData"]["gameVersion"], 16001)
        self.assertEqual(d["RXPData"]["maxLoadTime"], 4000)
        self.assertEqual(d["RXPData"]["realmData"],
                         {"CAMWOW US2 Realm 8 PvP": {}})
        pos = d["RXPSettings"]["framePositions"]["RXPFrame"]
        self.assertEqual(len(pos), 1)
        # nil at index 2 -> keys 1,3,4,5 -> dict, not list
        self.assertEqual(pos[0], {"1": "BOTTOMRIGHT", "3": "BOTTOMRIGHT",
                                  "4": -293.1947937011719,
                                  "5": 88.94409942626953})

    def test_crlf(self):
        self.assertEqual(p(RXP_FIXTURE.replace("\n", "\r\n")), p(RXP_FIXTURE))

    def test_bom(self):
        self.assertEqual(p("\ufeff" + RXP_FIXTURE), p(RXP_FIXTURE))

    def test_bom_crlf(self):
        self.assertEqual(p("\ufeff\r\nA = 1\r\n"), {"A": 1})

    def test_blank(self):
        self.assertEqual(p(""), {})
        self.assertEqual(p("   \r\n\t\n  "), {})
        self.assertEqual(p("\ufeff"), {})

    def test_json_roundtrip(self):
        json.dumps(jsonable(p(RXP_FIXTURE)))


class TestScalars(unittest.TestCase):
    def test_nil_true_false(self):
        self.assertEqual(p("a = nil b = true c = false"),
                         {"a": None, "b": True, "c": False})

    def test_integers(self):
        d = p("a = 0 b = -5 c = 2147483648 d = -2147483649 "
              "e = 9007199254740993")
        self.assertEqual(d, {"a": 0, "b": -5, "c": 2147483648,
                             "d": -2147483649, "e": 9007199254740993})
        self.assertIsInstance(d["c"], int)

    def test_floats(self):
        d = p("a = -111.7860260009766 b = 1e-05 c = 2.5E+3 d = .5 e = 3.")
        self.assertEqual(d["a"], -111.7860260009766)
        self.assertEqual(d["b"], 1e-05)
        self.assertEqual(d["c"], 2500.0)
        self.assertEqual(d["d"], 0.5)
        self.assertEqual(d["e"], 3.0)

    def test_hex(self):
        self.assertEqual(p("a = 0x1F b = 0XfF c = -0x10"),
                         {"a": 31, "b": 255, "c": -16})

    def test_special_numbers(self):
        d = p("a = inf b = -inf c = nan d = 1/0 e = -1/0 f = 0/0 "
              "g = 1 / 0")
        self.assertEqual(d["a"], float("inf"))
        self.assertEqual(d["b"], float("-inf"))
        self.assertTrue(math.isnan(d["c"]))
        self.assertEqual(d["d"], float("inf"))
        self.assertEqual(d["e"], float("-inf"))
        self.assertTrue(math.isnan(d["f"]))
        self.assertEqual(d["g"], float("inf"))
        j = json.loads(json.dumps(jsonable(d)))
        self.assertEqual(j, {"a": "inf", "b": "-inf", "c": "nan",
                             "d": "inf", "e": "-inf", "f": "nan",
                             "g": "inf"})

    def test_special_numbers_in_table(self):
        d = p("t = { inf, -1/0, [\"x\"] = 0/0 }")
        j = json.loads(json.dumps(jsonable(d)))
        self.assertEqual(j, {"t": {"1": "inf", "2": "-inf", "x": "nan"}})

    def test_string_escapes(self):
        src = r'a = "q\"b\\s\nn\rr\tt\aa\bb\ff\vv"'
        self.assertEqual(p(src)["a"], 'q"b\\s\nn\rr\tt\aa\bb\ff\vv')

    def test_decimal_escapes(self):
        self.assertEqual(p(r'a = "\65\066\0671"')["a"], "ABC1")
        self.assertEqual(p(r'a = "x\0y"')["a"], "x\x00y")
        # UTF-8 bytes via decimal escapes: e-acute = \195\169
        self.assertEqual(p(r'a = "caf\195\169"')["a"], "caf\u00e9")

    def test_backslash_newline(self):
        self.assertEqual(p('a = "one\\\ntwo"')["a"], "one\ntwo")
        self.assertEqual(p('a = "one\\\r\ntwo"')["a"], "one\ntwo")

    def test_single_quoted(self):
        self.assertEqual(p(r"a = 'it\'s' b = 'say \"hi\"'"),
                         {"a": "it's", "b": 'say "hi"'})

    def test_non_ascii_raw(self):
        self.assertEqual(p('a = "\u00fcber \u4e2d"')["a"], "\u00fcber \u4e2d")


class TestTables(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(p("a = {}"), {"a": {}})
        self.assertEqual(p("a = {\n}"), {"a": {}})

    def test_list(self):
        self.assertEqual(p('a = { "x", "y", "z", }'), {"a": ["x", "y", "z"]})
        self.assertEqual(p("a = { [1] = 1, [2] = 2 }"), {"a": [1, 2]})
        self.assertEqual(p("a = { [2] = 'b', [1] = 'a' }"),
                         {"a": ["a", "b"]})

    def test_key_kinds(self):
        d = p('a = { [ "key" ] = 1, [123] = 2, [true] = 3, [false] = 4, '
              'ident = 5, [1.5] = 6, [-2] = 7 }')
        self.assertEqual(d["a"], {"key": 1, "123": 2, "true": 3,
                                  "false": 4, "ident": 5, "1.5": 6,
                                  "-2": 7})
        self.assertEqual(list(d["a"]),
                         ["key", "123", "true", "false", "ident", "1.5",
                          "-2"])

    def test_true_and_one_distinct(self):
        self.assertEqual(p("a = { [1] = 'one', [true] = 't' }")["a"],
                         {"1": "one", "true": "t"})

    def test_separators_and_comments(self):
        src = ('a = {\n"x", -- [1]\n"y"; -- [2]\n["k"] = 1; -- trailing\n'
               '--[[ long\ncomment ]]\n}\n-- end\n')
        self.assertEqual(p(src), {"a": {"1": "x", "2": "y", "k": 1}})

    def test_positional_comments_list(self):
        src = 'a = {\n"x", -- [1]\n"y", -- [2]\n}\n'
        self.assertEqual(p(src), {"a": ["x", "y"]})

    def test_sparse_nil(self):
        src = ("schoolColoring = { {1}, {2}, nil, {4}, [0] = {0}, "
               "[8] = {8} }")
        d = p(src)["schoolColoring"]
        self.assertEqual(d, {"1": [1], "2": [2], "4": [4], "0": [0],
                             "8": [8]})
        self.assertEqual(list(d), ["1", "2", "4", "0", "8"])

    def test_nil_middle_keys(self):
        self.assertEqual(p("a = { 'a', nil, 'c' }")["a"],
                         {"1": "a", "3": "c"})

    def test_trailing_nil_only(self):
        # {a, nil} has only key 1 -> list of one
        self.assertEqual(p("a = { 'a', nil }")["a"], ["a"])

    def test_mixed_zero_and_positional(self):
        d = p("t = { [0] = 'zero', 'one', 'two', name = 'n' }")["t"]
        self.assertEqual(d, {"0": "zero", "1": "one", "2": "two",
                             "name": "n"})

    def test_ident_vs_positional_name(self):
        self.assertEqual(p("a = { true, x = false }")["a"],
                         {"1": True, "x": False})

    def test_deep_nesting(self):
        depth = 150
        src = "a = " + "{" * depth + "1" + "}" * depth
        v = p(src)["a"]
        for _ in range(depth - 1):
            self.assertIsInstance(v, list)
            v = v[0]
        self.assertEqual(v, [1])
        json.dumps(jsonable(p(src)))

    def test_very_deep_nesting(self):
        depth = 5000
        src = "a = " + "{" * depth + "}" * depth
        v = p(src)["a"]
        self.assertIsInstance(v, list)

    def test_insertion_order(self):
        d = p("a = { z = 1, a = 2, m = 3 }")["a"]
        self.assertEqual(list(d), ["z", "a", "m"])


class TestErrors(unittest.TestCase):
    def assertErrAt(self, src, line, col=None):
        with self.assertRaises(LuaSVParseError) as cm:
            p(src)
        self.assertEqual(cm.exception.line, line, str(cm.exception))
        if col is not None:
            self.assertEqual(cm.exception.column, col, str(cm.exception))

    def test_missing_comma(self):
        self.assertErrAt('A = {\n["a"] = 1,\n["b"] = 2\n["c"] = 3,\n}\n',
                         4, 1)

    def test_bad_value_crlf(self):
        self.assertErrAt('A = {\r\n["a"] = 1,\r\n["b"] = @,\r\n}\r\n', 3, 9)

    def test_unterminated_table(self):
        self.assertErrAt("A = {\n1,\n", 3)

    def test_unterminated_string(self):
        self.assertErrAt('A = "abc\nB = 1', 1, 5)

    def test_missing_equals(self):
        self.assertErrAt("A 1", 1, 3)

    def test_nil_key(self):
        self.assertErrAt("A = { [nil] = 1 }", 1, 7)

    def test_message_has_position(self):
        with self.assertRaises(LuaSVParseError) as cm:
            p("\n\nA = ?")
        self.assertIn("line 3", str(cm.exception))


class TestPerformance(unittest.TestCase):
    def test_large_file(self):
        entry = ('["Some Key %d"] = {\n["x"] = -111.7860260009766,\n'
                 '["y"] = 2147483648,\n["name"] = "Hello \\"world\\"",\n'
                 '{\n"BOTTOMRIGHT",\nnil,\n1e-05,\n},\n},\n')
        parts = ["Big = {\n"]
        size = 0
        i = 0
        while size < 20 * 1024 * 1024:
            s = entry % i
            parts.append(s)
            size += len(s)
            i += 1
        parts.append("}\n")
        text = "".join(parts)
        t0 = time.perf_counter()
        d = p(text)
        elapsed = time.perf_counter() - t0
        sys.stderr.write("\n[perf] parsed %.1f MB (%d entries) in %.2f s\n"
                         % (len(text) / 1048576.0, i, elapsed))
        self.assertEqual(len(d["Big"]), i)
        self.assertLess(elapsed, 30.0)


if __name__ == "__main__":
    unittest.main()
