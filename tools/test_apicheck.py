"""Tests for apicheck: the Forever API-existence and event-name gate."""

import io
import os
import shutil
import tempfile
import unittest

import apicheck


def make_truth():
    t = apicheck.Truth()
    t.globals = {"CreateFrame", "GetBuildInfo", "C_Spell.GetSpellInfo",
                 "C_Timer.After"}
    t.namespaces = {"C_Spell", "C_Timer"}
    t.events = {"PLAYER_LOGIN", "ADDON_LOADED", "COMBAT_LOG_EVENT_UNFILTERED"}
    t.globals_source = "test"
    t.events_source = "test"
    return t


def names(findings):
    return sorted(f.name for f in findings)


class StripTests(unittest.TestCase):
    def test_line_comment_and_string_blanked(self):
        src = 'a = "X(1)" -- Y(2)\nZ(3)\n'
        out = apicheck.strip_lua(src)
        self.assertNotIn("X(", out)
        self.assertNotIn("Y(", out)
        self.assertIn("Z(3)", out)
        self.assertEqual(out.count("\n"), src.count("\n"))

    def test_long_comment_and_long_string(self):
        src = "--[[ A(1)\n B(2) ]]\nlocal s = [==[ C(3) ]==]\nD(4)\n"
        out = apicheck.strip_lua(src)
        for gone in ("A(", "B(", "C("):
            self.assertNotIn(gone, out)
        self.assertIn("D(4)", out)
        self.assertEqual(out.count("\n"), src.count("\n"))

    def test_keep_strings_keeps_contents(self):
        src = 'f:RegisterEvent("PLAYER_LOGIN") -- "GONE"\n'
        out = apicheck.strip_lua(src, keep_strings=True)
        self.assertIn('"PLAYER_LOGIN"', out)
        self.assertNotIn("GONE", out)

    def test_escaped_quote_inside_string(self):
        src = 'local s = "a \\" B(1)"\nC(2)\n'
        out = apicheck.strip_lua(src)
        self.assertNotIn("B(", out)
        self.assertIn("C(2)", out)


class ScanTests(unittest.TestCase):
    def scan(self, src, project_defined=frozenset()):
        return apicheck.scan_text("x.lua", src, make_truth(),
                                  set(project_defined))

    def test_known_calls_pass(self):
        src = ('local f = CreateFrame("Frame")\nGetBuildInfo()\n'
               'C_Spell.GetSpellInfo(1)\nC_Timer.After(1, print)\n'
               'f:RegisterEvent("PLAYER_LOGIN")\n')
        self.assertEqual(self.scan(src), [])

    def test_removed_global_flagged(self):
        self.assertEqual(names(self.scan("GetSpellInfo(133)\n")),
                         ["GetSpellInfo"])

    def test_namespace_function_and_namespace_flagged(self):
        found = self.scan("C_Spell.Nope(1)\nC_Fake.Thing()\n")
        self.assertEqual(names(found), ["C_Fake.Thing", "C_Spell.Nope"])
        msgs = " ".join(str(f) for f in found)
        self.assertIn("has no function Nope", msgs)
        self.assertIn("namespace C_Fake does not exist", msgs)

    def test_lowercase_and_method_calls_ignored(self):
        src = "print(1)\nlocal t = {}\nt.Foo(1)\nt:Bar(2)\nstring.format('%d', 1)\n"
        self.assertEqual(self.scan(src), [])

    def test_local_and_param_names_not_flagged(self):
        src = ("local Call = print\nCall(1)\nlocal function Helper() end\n"
               "Helper()\nlocal f = function(Callback) Callback() end\n"
               "for _, Fn in ipairs({}) do Fn() end\n")
        self.assertEqual(self.scan(src), [])

    def test_project_defined_global_not_flagged(self):
        self.assertEqual(self.scan("MyGlobalHelper()\n",
                                   {"MyGlobalHelper"}), [])

    def test_unknown_and_dead_events(self):
        src = ('f:RegisterEvent("NOT_REAL")\n'
               'f:RegisterUnitEvent("COMBAT_LOG_EVENT_UNFILTERED", "player")\n'
               '-- f:RegisterEvent("IN_A_COMMENT")\n')
        found = self.scan(src)
        self.assertEqual(names(found),
                         ["COMBAT_LOG_EVENT_UNFILTERED", "NOT_REAL"])
        self.assertEqual(sorted(f.line for f in found), [1, 2])

    def test_line_numbers(self):
        found = self.scan("\n\nGetSpellInfo(1)\n")
        self.assertEqual(found[0].line, 3)

    # Review findings, 2026-11-10

    def test_self_alias_does_not_hide_removed_global(self):
        found = self.scan("local GetSpellInfo = GetSpellInfo\nGetSpellInfo(1)\n")
        self.assertIn("GetSpellInfo", names(found))
        found = self.scan("local UnitAura = _G.UnitAura\n")
        self.assertEqual(names(found), ["UnitAura"])

    def test_alias_of_valid_global_passes(self):
        src = "local CreateFrame = CreateFrame\nCreateFrame('Frame')\n"
        self.assertEqual(self.scan(src), [])

    def test_alias_of_project_table_member_passes(self):
        src = "local Call = MyAddonTable.Call\nCall(1)\n"
        self.assertEqual(self.scan(src), [])

    def test_uncalled_namespace_reference_flagged(self):
        found = self.scan("local f = C_Spell.Bogus\nlocal ok = C_Spell.GetSpellInfo\n")
        self.assertEqual(names(found), ["C_Spell.Bogus"])

    def test_pcall_and_hook_targets_flagged(self):
        found = self.scan('pcall(UnitAura, "player", 1)\n'
                          'hooksecurefunc("NotAFunction", print)\n'
                          'hooksecurefunc("CreateFrame", print)\n')
        self.assertEqual(names(found), ["NotAFunction", "UnitAura"])

    def test_event_forms(self):
        src = ('f:RegisterEvent"BAD_A"\n'
               'EventRegistry:RegisterFrameEventAndCallback("BAD_B", cb)\n'
               'FrameUtil.RegisterFrameForEvents(f, {"PLAYER_LOGIN", "BAD_C"})\n'
               'for _, e in ipairs({"ADDON_LOADED", "BAD_D"}) do\n'
               '  f:RegisterEvent(e)\nend\n'
               'for _, x in ipairs({"NOT_AN_EVENT_LIST"}) do print(x) end\n')
        self.assertEqual(names(self.scan(src)),
                         ["BAD_A", "BAD_B", "BAD_C", "BAD_D"])

    def test_global_definitions_count_as_defined(self):
        src = ("_G.MyFn = function() end\nMyFn()\n"
               "OtherFn = OtherFn or function() end\nOtherFn()\n")
        self.assertEqual(self.scan(src), [])

    def test_indented_table_key_does_not_define_global(self):
        src = "local t = {\n  GetSpellInfo = function() end,\n}\nGetSpellInfo(1)\n"
        self.assertEqual(names(self.scan(src)), ["GetSpellInfo"])


class TruthMergeTests(unittest.TestCase):
    def test_inventory_and_ketho_lists_are_merged(self):
        root = tempfile.mkdtemp(prefix="apicheck-")
        try:
            res = os.path.join(root, "tools", "vendor",
                               "BlizzardInterfaceResources", "Resources")
            os.makedirs(res)
            with open(os.path.join(res, "GlobalAPI.lua"), "w") as f:
                f.write('local GlobalAPI = {\n\t"FromKetho",\n}\n')
            os.makedirs(os.path.join(root, "data"))
            with open(os.path.join(root, "data", "api-inventory.json"),
                      "w") as f:
                f.write('{"build": "1", "globals": ["FromGame"], '
                        '"namespaces": {"C_Game": ["Fn"]}}')
            truth = apicheck.load_truth(root)
            self.assertIn("FromKetho", truth.globals)
            self.assertIn("FromGame", truth.globals)
            self.assertIn("C_Game.Fn", truth.globals)
            self.assertIn("in-game inventory", truth.globals_source)
            self.assertIn("BlizzardInterfaceResources", truth.globals_source)
        finally:
            shutil.rmtree(root)


class RunTests(unittest.TestCase):
    def test_missing_truth_fails_closed(self):
        root = tempfile.mkdtemp(prefix="apicheck-")
        try:
            os.makedirs(os.path.join(root, "addons", "A"))
            out = io.StringIO()
            code = apicheck.run(root, [os.path.join(root, "addons")], out)
            self.assertEqual(code, 2)
            self.assertIn("FAILED: no global API list", out.getvalue())
            self.assertIn("FAILED: no event list", out.getvalue())
        finally:
            shutil.rmtree(root)

    def test_walks_every_source_folder(self):
        root = tempfile.mkdtemp(prefix="apicheck-")
        try:
            res = os.path.join(root, "tools", "vendor",
                               "BlizzardInterfaceResources", "Resources")
            os.makedirs(res)
            with open(os.path.join(res, "GlobalAPI.lua"), "w") as f:
                f.write('local GlobalAPI = {\n\t"CreateFrame",\n}\n')
            with open(os.path.join(res, "Events.lua"), "w") as f:
                f.write('local Events = {\n\t"PLAYER_LOGIN",\n}\n')
            for folder, src in (("addons", "CreateFrame()\nBogusOne()\n"),
                                ("devaddons", "BogusTwo()\n")):
                os.makedirs(os.path.join(root, folder, "A" + folder))
                with open(os.path.join(root, folder, "A" + folder, "x.lua"),
                          "w") as f:
                    f.write(src)
            out = io.StringIO()
            code = apicheck.run(root, [os.path.join(root, "addons"),
                                       os.path.join(root, "devaddons"),
                                       os.path.join(root, "missing")], out)
            text = out.getvalue()
            self.assertEqual(code, 1, text)
            self.assertIn("checked 2 files", text)
            self.assertIn("BogusOne", text)
            self.assertIn("BogusTwo", text)
            with self.assertRaises(TypeError):
                apicheck.run(root, os.path.join(root, "addons"), out)
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
