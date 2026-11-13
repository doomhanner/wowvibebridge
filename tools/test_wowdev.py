import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wowdev  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def make_link(link, target):
    """Create a directory symlink, or a junction as a fallback.

    Returns a description, or None if the OS denies both.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
        return "junction"
    except (ImportError, AttributeError, OSError):
        return None


def remove_link(link):
    # Remove the link itself, never its target's contents.
    try:
        os.unlink(link)
    except OSError:
        os.rmdir(link)


class WowdevTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wowdev-test-")
        self.root = os.path.join(self.tmp, "proj")
        self.game = os.path.join(self.tmp, "game")
        self.addons_root = os.path.join(self.game, "Interface", "AddOns")
        os.makedirs(self.addons_root)
        os.makedirs(os.path.join(self.root, "addons"))
        write(os.path.join(self.root, "wowdev.json"), json.dumps({
            "game_dir": os.path.join(self.tmp, "nonexistent-real-dir"),
            "interface": 16001, "addons_src": "addons"}))
        self.links = []

    def tearDown(self):
        for link in self.links:
            if os.path.lexists(link):
                remove_link(link)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv, game=True):
        out = io.StringIO()
        err = io.StringIO()
        args = list(argv)
        if game:
            args += ["--game-dir", self.game]
        code = wowdev.main(args, project_root=self.root, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def set_config(self, **extra):
        data = {"game_dir": os.path.join(self.tmp, "nonexistent-real-dir"),
                "interface": 16001, "addons_src": "addons"}
        data.update(extra)
        write(os.path.join(self.root, "wowdev.json"), json.dumps(data))

    def make_addon(self, name, files=None, folder="addons"):
        base = os.path.join(self.root, folder, name)
        files = files or {name + ".toc": "## Interface: 16001\n" + name
                          + ".lua\n", name + ".lua": "print('hi')\n"}
        for rel, text in files.items():
            write(os.path.join(base, rel), text)
        return base

    def snapshot(self, path):
        out = {}
        for dp, dn, fn in os.walk(path):
            for f in fn:
                p = os.path.join(dp, f)
                out[os.path.relpath(p, path)] = read(p)
            for d in dn:
                out[os.path.relpath(os.path.join(dp, d), path) + os.sep] = ""
        return out


class TestConfig(WowdevTestBase):
    def test_missing_config_exit2(self):
        os.remove(os.path.join(self.root, "wowdev.json"))
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("config not found", err)

    def test_unparseable_config_exit2(self):
        write(os.path.join(self.root, "wowdev.json"), "{ not json")
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("config unreadable", err)

    def test_bad_field_exit2(self):
        write(os.path.join(self.root, "wowdev.json"),
              json.dumps({"game_dir": "x", "interface": "16001",
                          "addons_src": "addons"}))
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("interface", err)

    def test_optional_keys_checked(self):
        for extra, word in (({"dev_addons_src": ""}, "dev_addons_src"),
                            ({"dev_addons_src": 5}, "dev_addons_src"),
                            ({"dev_addons_src": "./addons/"}, "differ"),
                            ({"author": ""}, "author"),
                            ({"author": None}, "author")):
            self.set_config(**extra)
            code, out, err = self.run_cli("status")
            self.assertEqual(code, 2, extra)
            self.assertIn(word, err, extra)

    def test_optional_keys_absent(self):
        cfg = wowdev.load_config(self.root, self.game)
        self.assertIsNone(cfg.dev_addons_src)
        self.assertIsNone(cfg.author)
        self.assertEqual(cfg.source_dirs, [cfg.addons_dir])


class TestDevAddons(WowdevTestBase):
    """addons_src holds the player's addons; dev_addons_src the helpers."""

    def setUp(self):
        super().setUp()
        self.set_config(dev_addons_src="devaddons")

    def test_found_in_both_folders(self):
        self.make_addon("Mine")
        dev = self.make_addon("Helper", folder="devaddons")
        cfg = wowdev.load_config(self.root, self.game)
        self.assertEqual(wowdev.source_addons(cfg), ["Helper", "Mine"])
        self.assertEqual(wowdev.addon_source_path(cfg, "Helper"), dev)
        self.assertIsNone(wowdev.addon_source_path(cfg, "Nope"))

    def test_missing_dev_folder_counts_as_empty(self):
        self.make_addon("Mine")
        cfg = wowdev.load_config(self.root, self.game)
        self.assertEqual(wowdev.source_addons(cfg), ["Mine"])

    def test_duplicate_name_refused(self):
        a = self.make_addon("Twin")
        b = self.make_addon("Twin", folder="devaddons")
        for argv in (("status",), ("deploy",), ("deploy", "Twin"),
                     ("lint",)):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("more than one source folder", err, argv)
            self.assertIn(a, err, argv)
            self.assertIn(b, err, argv)
        self.assertEqual(os.listdir(self.addons_root), [])
        cfg = wowdev.load_config(self.root, self.game)
        with self.assertRaises(wowdev.WowdevError):
            wowdev.source_addons(cfg)

    def test_duplicate_name_differing_case_refused(self):
        self.make_addon("Twin")
        self.make_addon("twin", {"twin.toc": "x\n"}, folder="devaddons")
        if not os.path.isdir(os.path.join(self.root, "addons", "TWIN")):
            self.skipTest("case-sensitive file system")
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("more than one source folder", err)

    def test_deploy_and_status_dev_addon(self):
        self.make_addon("Mine")
        self.make_addon("Helper", {"Helper.toc": "toc\n",
                                   "Helper.lua": "h\n"}, folder="devaddons")
        code, out, err = self.run_cli("deploy", "Helper")
        self.assertEqual(code, 0, err)
        dst = os.path.join(self.addons_root, "Helper")
        self.assertEqual(read(os.path.join(dst, "Helper.lua")), "h\n")
        marker = json.loads(read(os.path.join(dst, wowdev.MARKER)))
        self.assertIn("devaddons", marker["source"])
        self.assertFalse(os.path.exists(os.path.join(self.addons_root,
                                                     "Mine")))
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 0, err)
        self.assertIn("source addons in addons/:\n  Mine: not deployed", out)
        self.assertIn("source addons in devaddons/:\n"
                      "  Helper: deployed (in sync)", out)
        # Deploy with no names covers both folders.
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(os.path.join(self.addons_root, "Mine")))

    def test_deploy_unknown_names_every_folder(self):
        code, out, err = self.run_cli("deploy", "Nope")
        self.assertEqual(code, 2)
        self.assertIn("addons/", err)
        self.assertIn("devaddons/", err)
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 0, err)
        self.assertIn(os.path.join(self.root, "devaddons"), out)


class TestDiscovery(WowdevTestBase):
    def test_valid_addons(self):
        self.make_addon("Alpha")
        self.make_addon("Beta", {"Beta_Vanilla.toc": "x\n"})
        self.make_addon("NotAddon", {"Other.toc": "x\n"})
        self.make_addon("NoToc", {"a.lua": "x\n"})
        cfg = wowdev.load_config(self.root, self.game)
        self.assertEqual(wowdev.source_addons(cfg), ["Alpha", "Beta"])

    def test_named_invalid_is_error(self):
        self.make_addon("NoToc", {"a.lua": "x\n"})
        code, out, err = self.run_cli("deploy", "NoToc")
        self.assertEqual(code, 2)
        self.assertIn("not a valid source addon", err)
        self.assertEqual(os.listdir(self.addons_root), [])


class TestDeploy(WowdevTestBase):
    def test_fresh_deploy_and_marker(self):
        self.make_addon("Alpha", {"Alpha.toc": "toc\n", "Alpha.lua": "a\n",
                                  "sub/x.lua": "x\n", ".hidden": "h\n",
                                  "tests/t.lua": "t\n",
                                  "__pycache__/c.pyc": "c\n",
                                  ".git/config": "g\n"})
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 0, err)
        dst = os.path.join(self.addons_root, "Alpha")
        snap = self.snapshot(dst)
        self.assertEqual(
            sorted(k for k in snap if not k.endswith(os.sep)),
            sorted([wowdev.MARKER, "Alpha.toc", "Alpha.lua",
                    os.path.join("sub", "x.lua")]))
        marker = json.loads(read(os.path.join(dst, wowdev.MARKER)))
        self.assertIn("source", marker)
        self.assertIn("deployed_utc", marker)
        self.assertIn(wowdev.HINT_RESTART, out)

    def test_marker_guard_refuses_unmanaged(self):
        self.make_addon("Alpha")
        foreign = os.path.join(self.addons_root, "Alpha")
        write(os.path.join(foreign, "Alpha.toc"), "someone else\n")
        before = self.snapshot(foreign)
        code, out, err = self.run_cli("deploy")
        self.assertNotEqual(code, 0)
        self.assertIn("REFUSED", out)
        self.assertIn("not managed by wowdev", out)
        self.assertEqual(self.snapshot(foreign), before)

    def test_marker_guard_other_addons_still_deploy(self):
        self.make_addon("Alpha")
        self.make_addon("Beta")
        write(os.path.join(self.addons_root, "Alpha", "x.txt"), "x\n")
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 1)
        self.assertTrue(os.path.isfile(
            os.path.join(self.addons_root, "Beta", wowdev.MARKER)))

    def _link_case(self, inside):
        self.make_addon("Alpha")
        elsewhere = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(elsewhere, "precious.txt"), "keep me\n")
        dst = os.path.join(self.addons_root, "Alpha")
        if inside:
            code, _o, err = self.run_cli("deploy")
            self.assertEqual(code, 0, err)
            link = os.path.join(dst, "linked")
        else:
            link = dst
        kind = make_link(link, elsewhere)
        if kind is None:
            self.skipTest("OS denied both symlink and junction creation")
        self.links.append(link)
        return elsewhere, kind

    def test_link_guard_target_is_link(self):
        elsewhere, kind = self._link_case(inside=False)
        code, out, err = self.run_cli("deploy")
        self.assertNotEqual(code, 0, kind)
        self.assertIn("symlink/junction", out)
        self.assertEqual(os.listdir(elsewhere), ["precious.txt"])
        code, out, err = self.run_cli("undeploy", "Alpha")
        self.assertNotEqual(code, 0)
        self.assertIn("symlink/junction", err)
        self.assertEqual(read(os.path.join(elsewhere, "precious.txt")),
                         "keep me\n")

    def test_link_guard_link_inside_target(self):
        elsewhere, kind = self._link_case(inside=True)
        # Without the guard, mirroring would delete "linked" as stale.
        code, out, err = self.run_cli("deploy")
        self.assertNotEqual(code, 0, kind)
        self.assertIn("symlink/junction", out)
        code, out, err = self.run_cli("undeploy", "Alpha")
        self.assertNotEqual(code, 0)
        self.assertIn("symlink/junction", err)
        self.assertEqual(read(os.path.join(elsewhere, "precious.txt")),
                         "keep me\n")
        code, out, err = self.run_cli("status")
        self.assertIn("REFUSED", out)

    def test_link_guard_junction(self):
        try:
            import _winapi
            create = _winapi.CreateJunction
        except (ImportError, AttributeError):
            self.skipTest("_winapi.CreateJunction unavailable (not Windows)")
        self.make_addon("Alpha")
        self.assertEqual(self.run_cli("deploy")[0], 0)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(elsewhere, "precious.txt"), "keep me\n")
        link = os.path.join(self.addons_root, "Alpha", "junc")
        try:
            create(elsewhere, link)
        except OSError as exc:
            self.skipTest("OS denied junction creation: %s" % exc)
        self.links.append(link)
        self.assertTrue(wowdev.is_link(link))
        code, out, err = self.run_cli("deploy")
        self.assertNotEqual(code, 0)
        self.assertIn("symlink/junction", out)
        code, out, err = self.run_cli("undeploy", "Alpha")
        self.assertNotEqual(code, 0)
        self.assertEqual(read(os.path.join(elsewhere, "precious.txt")),
                         "keep me\n")

    def test_mirror_removes_stale(self):
        src = self.make_addon("Alpha", {"Alpha.toc": "toc\n",
                                        "Alpha.lua": "a\n",
                                        "old/gone.lua": "g\n"})
        self.assertEqual(self.run_cli("deploy")[0], 0)
        os.remove(os.path.join(src, "old", "gone.lua"))
        os.rmdir(os.path.join(src, "old"))
        dst = os.path.join(self.addons_root, "Alpha")
        write(os.path.join(dst, "stray.txt"), "stray\n")
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 0, err)
        self.assertIn("removed (2)", out)
        self.assertFalse(os.path.exists(os.path.join(dst, "old")))
        self.assertFalse(os.path.exists(os.path.join(dst, "stray.txt")))
        self.assertTrue(os.path.isfile(os.path.join(dst, wowdev.MARKER)))
        self.assertTrue(os.path.isfile(os.path.join(dst, "Alpha.lua")))

    def test_same_size_content_change_detected(self):
        src = self.make_addon("Alpha", {"Alpha.toc": "toc\n",
                                        "Alpha.lua": "aaaa\n"})
        self.assertEqual(self.run_cli("deploy")[0], 0)
        dst_file = os.path.join(self.addons_root, "Alpha", "Alpha.lua")
        st = os.stat(dst_file)
        write(os.path.join(src, "Alpha.lua"), "bbbb\n")
        os.utime(os.path.join(src, "Alpha.lua"), (st.st_atime, st.st_mtime))
        code, out, err = self.run_cli("deploy")
        self.assertIn("changed (1)", out)
        self.assertEqual(read(dst_file), "bbbb\n")

    def test_dry_run_writes_nothing(self):
        src = self.make_addon("Alpha")
        before = self.snapshot(self.game)
        code, out, err = self.run_cli("deploy", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("dry run", out)
        self.assertIn("added (2)", out)
        self.assertEqual(self.snapshot(self.game), before)
        # And on an existing deployment with pending changes.
        self.run_cli("deploy")
        write(os.path.join(src, "New.lua"), "n\n")
        before = self.snapshot(self.game)
        code, out, err = self.run_cli("deploy", "--dry-run")
        self.assertIn("added (1)", out)
        self.assertEqual(self.snapshot(self.game), before)

    def test_hint_reload_on_new_lua_file(self):
        src = self.make_addon("Alpha")
        self.run_cli("deploy")
        write(os.path.join(src, "Extra.lua"), "e\n")
        code, out, err = self.run_cli("deploy")
        self.assertIn(wowdev.HINT_RELOAD, out)
        self.assertNotIn("RESTART", out)

    def test_hint_restart_on_new_media_file(self):
        src = self.make_addon("Alpha")
        self.run_cli("deploy")
        write(os.path.join(src, "Icon.tga"), "x\n")
        code, out, err = self.run_cli("deploy")
        self.assertIn(wowdev.HINT_RESTART_MEDIA, out)

    def test_hint_reload_on_removed_file(self):
        src = self.make_addon("Alpha", {"Alpha.toc": "t\n", "A.lua": "a\n",
                                        "B.lua": "b\n"})
        self.run_cli("deploy")
        os.remove(os.path.join(src, "B.lua"))
        code, out, err = self.run_cli("deploy")
        self.assertIn(wowdev.HINT_RELOAD, out)

    def test_hint_reload_on_toc_change(self):
        src = self.make_addon("Alpha")
        self.run_cli("deploy")
        write(os.path.join(src, "Alpha.toc"), "## Interface: 16002\n")
        code, out, err = self.run_cli("deploy")
        self.assertIn("changed (1)", out)
        self.assertIn(wowdev.HINT_RELOAD, out)

    def test_hint_restart_on_first_deploy(self):
        self.make_addon("Alpha")
        code, out, err = self.run_cli("deploy")
        self.assertIn(wowdev.HINT_RESTART_NEW, out)

    def test_hint_reload_on_existing_change(self):
        src = self.make_addon("Alpha")
        self.run_cli("deploy")
        write(os.path.join(src, "Alpha.lua"), "print('changed')\n")
        code, out, err = self.run_cli("deploy")
        self.assertIn(wowdev.HINT_RELOAD, out)
        self.assertNotIn("RESTART", out)

    def test_hint_up_to_date(self):
        self.make_addon("Alpha")
        self.run_cli("deploy")
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 0)
        self.assertIn(wowdev.HINT_NONE, out)
        self.assertNotIn("RESTART", out)
        self.assertNotIn("/reload", out)


class TestUndeploy(WowdevTestBase):
    def test_undeploy_managed(self):
        self.make_addon("Alpha")
        self.run_cli("deploy")
        code, out, err = self.run_cli("undeploy", "Alpha")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(
            os.path.join(self.addons_root, "Alpha")))

    def test_undeploy_refuses_unmanaged(self):
        foreign = os.path.join(self.addons_root, "RXPGuides")
        write(os.path.join(foreign, "RXPGuides.toc"), "x\n")
        code, out, err = self.run_cli("undeploy", "RXPGuides")
        self.assertNotEqual(code, 0)
        self.assertIn("not managed", err)
        self.assertTrue(os.path.isfile(os.path.join(foreign,
                                                    "RXPGuides.toc")))

    def test_undeploy_rejects_traversal(self):
        code, out, err = self.run_cli("undeploy", "..")
        self.assertEqual(code, 2)


class TestStatus(WowdevTestBase):
    def test_states(self):
        self.make_addon("Synced")
        stale = self.make_addon("Stale")
        self.make_addon("Fresh")
        self.make_addon("Taken")
        self.run_cli("deploy", "Synced", "Stale")
        write(os.path.join(stale, "Stale.lua"), "changed\n")
        write(os.path.join(stale, "More.lua"), "more\n")
        write(os.path.join(self.addons_root, "Taken", "Taken.toc"), "x\n")
        os.makedirs(os.path.join(self.game, "WTF", "Account", "ACC1",
                                 "SavedVariables"))
        before = self.snapshot(self.game)
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 0, err)
        self.assertIn("Synced: deployed (in sync)", out)
        self.assertIn("Stale: deployed (stale: 2 files differ)", out)
        self.assertIn("Fresh: not deployed", out)
        self.assertIn("Taken: CONFLICT (target exists, not managed by "
                      "wowdev)", out)
        self.assertIn("accounts: ACC1", out)
        self.assertIn("exists: yes", out)
        self.assertIn("Wow.exe running: ", out)
        self.assertEqual(self.snapshot(self.game), before)


class TestSv(WowdevTestBase):
    def setUp(self):
        super().setUp()
        acc = os.path.join(self.game, "WTF", "Account", "ACC1")
        write(os.path.join(acc, "SavedVariables", "Foo.lua"),
              "FooDB = {\r\n[\"gameVersion\"] = 16001,\r\n"
              "[\"name\"] = \"caf\u00e9\",\r\n}\r\nOther = 1/0\r\n")
        write(os.path.join(acc, "SavedVariables", "Foo.lua.bak"), "junk")
        write(os.path.join(acc, "91", "Char-Realm", "SavedVariables",
                           "Foo.lua"), "FooCharDB = { 1, 2 }\n")
        write(os.path.join(acc, "SavedVariables", "Bad.lua"),
              "BadDB = {\n1,\n2\n3,\n}\n")

    def test_account_wide(self):
        code, out, err = self.run_cli("sv", "Foo")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["FooDB"]["gameVersion"], 16001)
        self.assertEqual(data["FooDB"]["name"], "caf\u00e9")
        self.assertEqual(data["Other"], "inf")
        self.assertIn("file: ", err)
        self.assertIn("modified: ", err)
        self.assertIn("minutes", err)

    def test_var_and_char(self):
        code, out, err = self.run_cli("sv", "Foo", "--var", "FooDB")
        self.assertEqual(list(json.loads(out)), ["FooDB"])
        code, out, err = self.run_cli("sv", "Foo", "--char", "91/Char-Realm")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"FooCharDB": [1, 2]})

    def test_all(self):
        code, out, err = self.run_cli("sv", "Foo", "--all")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data), 2)
        self.assertFalse(any(k.endswith(".bak") for k in data))

    def test_missing_file_exit1(self):
        code, out, err = self.run_cli("sv", "Nope")
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_parse_error_exit2(self):
        code, out, err = self.run_cli("sv", "Bad")
        self.assertEqual(code, 2)
        self.assertIn("line 4, column 1", err)

    def test_multiple_accounts_needs_flag(self):
        os.makedirs(os.path.join(self.game, "WTF", "Account", "ACC2"))
        code, out, err = self.run_cli("sv", "Foo")
        self.assertEqual(code, 2)
        self.assertIn("ACC1", err)
        self.assertIn("ACC2", err)
        code, out, err = self.run_cli("sv", "Foo", "--account", "ACC1")
        self.assertEqual(code, 0, err)

    def test_out_file(self):
        target = os.path.join(self.tmp, "out.json")
        code, out, err = self.run_cli("sv", "Foo", "--out", target)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(read(target))["FooDB"]["gameVersion"],
                         16001)


class TestNew(WowdevTestBase):
    def setUp(self):
        super().setUp()
        real_tpl = os.path.join(wowdev.DEFAULT_PROJECT_ROOT, "templates",
                                "addon")
        shutil.copytree(real_tpl, os.path.join(self.root, "templates",
                                               "addon"))

    def test_new_scaffold(self):
        code, out, err = self.run_cli("new", "MyAddon", "--title", "My Addon")
        self.assertEqual(code, 0, err)
        base = os.path.join(self.root, "addons", "MyAddon")
        toc = read(os.path.join(base, "MyAddon.toc"))
        self.assertIn("## Interface: 16001", toc)
        self.assertIn("## Title: My Addon", toc)
        self.assertIn("## SavedVariables: MyAddonDB", toc)
        self.assertIn("MyAddon.lua", toc)
        self.assertNotIn("\r", toc)
        # The settings page loads after the main file, which sets ns.defaults.
        self.assertIn("\nOptions.lua", toc)
        self.assertLess(toc.index("MyAddon.lua"), toc.index("Options.lua"))
        self.assertEqual(sorted(os.listdir(base)),
                         ["MyAddon.lua", "MyAddon.toc", "Options.lua"])
        for fn in ("MyAddon.lua", "Options.lua"):
            lua = read(os.path.join(base, fn))
            self.assertNotIn("__", lua, fn)
            self.assertNotIn("\r", lua, fn)
            self.assertTrue(lua.isascii(), fn)
        options = read(os.path.join(base, "Options.lua"))
        self.assertIn('RegisterVerticalLayoutCategory("My Addon")', options)
        self.assertIn("/myaddon toggle", options)
        cfg = wowdev.load_config(self.root, self.game)
        self.assertEqual(wowdev.source_addons(cfg), ["MyAddon"])

    def test_new_author_default_and_configured(self):
        self.assertEqual(self.run_cli("new", "NoAuthor")[0], 0)
        toc = read(os.path.join(self.root, "addons", "NoAuthor",
                                "NoAuthor.toc"))
        self.assertIn("## Author: Unknown\n", toc)
        self.set_config(author="Somebody")
        self.assertEqual(self.run_cli("new", "WithAuthor")[0], 0)
        toc = read(os.path.join(self.root, "addons", "WithAuthor",
                                "WithAuthor.toc"))
        self.assertIn("## Author: Somebody\n", toc)
        self.assertNotIn("__", toc)

    def test_template_placeholders_all_known(self):
        # Every __WORD__ in the template is one cmd_new substitutes, so the
        # "no placeholders left" check above stays meaningful.
        import re
        tpl = os.path.join(self.root, "templates", "addon")
        known = {"__UPPERNAME__", "__LOWERNAME__", "__NAME__", "__TITLE__",
                 "__AUTHOR__", "__INTERFACE__"}
        found = set()
        for dp, dn, fns in os.walk(tpl):
            for fn in fns:
                found.update(re.findall(r"__[A-Z]+__", read(
                    os.path.join(dp, fn))))
        self.assertIn("__AUTHOR__", found)
        self.assertLessEqual(found, known)

    def test_new_refuses_name_in_dev_folder(self):
        self.set_config(dev_addons_src="devaddons")
        self.make_addon("Helper", folder="devaddons")
        code, out, err = self.run_cli("new", "Helper")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertIn("devaddons", err)
        self.assertFalse(os.path.exists(os.path.join(self.root, "addons",
                                                     "Helper")))

    def test_new_refuses_existing_and_bad_name(self):
        self.assertEqual(self.run_cli("new", "MyAddon")[0], 0)
        self.assertEqual(self.run_cli("new", "MyAddon")[0], 2)
        self.assertEqual(self.run_cli("new", "1bad")[0], 2)
        self.assertEqual(self.run_cli("new", "../evil")[0], 2)


class TestLint(WowdevTestBase):
    def test_missing_tools_exit3(self):
        old_which = wowdev.shutil.which
        wowdev.shutil.which = lambda name: None
        try:
            code, out, err = self.run_cli("lint")
        finally:
            wowdev.shutil.which = old_which
        self.assertEqual(code, 3)
        self.assertIn("SKIPPED: luacheck not installed", out)
        self.assertIn("SKIPPED: wowlua-ls not installed", out)
        self.assertIn("tools/bin/", out)

    def _lint_with_fake_tools(self):
        """Run lint with both checkers 'installed' and their runs recorded."""
        calls = []

        class Result(object):
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""

        def fake_run(argv, **kw):
            calls.append(list(argv))
            if argv[0] == "fake-wowlua":
                return Result("Checked 1 file\n")
            return Result("Total: 0 warnings / 0 errors\n")

        old = (wowdev._find_tool, wowdev.subprocess.run, wowdev.apicheck.run)
        wowdev._find_tool = lambda root, rel, exe: (
            "fake-luacheck" if exe == "luacheck" else "fake-wowlua")
        wowdev.subprocess.run = fake_run
        seen = {}

        def fake_apicheck(project_root, source_dirs, out, truth_root=None):
            seen["dirs"] = list(source_dirs)
            return 0
        wowdev.apicheck.run = fake_apicheck
        try:
            code, out, err = self.run_cli("lint")
        finally:
            (wowdev._find_tool, wowdev.subprocess.run,
             wowdev.apicheck.run) = old
        return code, out, err, calls, seen.get("dirs")

    def test_lint_passes_every_folder(self):
        self.set_config(dev_addons_src="devaddons")
        self.make_addon("Mine")
        self.make_addon("Helper", folder="devaddons")
        code, out, err, calls, dirs = self._lint_with_fake_tools()
        self.assertEqual(code, 0, out + err)
        lc = [c for c in calls if c[0] == "fake-luacheck"]
        self.assertEqual(lc, [["fake-luacheck", "addons", "devaddons",
                               "--codes"]])
        wl = [c for c in calls if c[0] == "fake-wowlua"]
        self.assertEqual(wl, [["fake-wowlua", "check",
                               os.path.join("devaddons", "Helper")],
                              ["fake-wowlua", "check",
                               os.path.join("addons", "Mine")]])
        self.assertEqual(dirs, [os.path.join(self.root, "addons"),
                                os.path.join(self.root, "devaddons")])

    def test_lint_without_dev_folder_as_before(self):
        self.make_addon("Mine")
        code, out, err, calls, dirs = self._lint_with_fake_tools()
        self.assertEqual(code, 0, out + err)
        self.assertIn(["fake-luacheck", "addons", "--codes"], calls)
        self.assertEqual(dirs, [os.path.join(self.root, "addons")])


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=wowdev-test",
         "-c", "user.email=wowdev-test@localhost"] + list(args),
        cwd=cwd, capture_output=True, text=True, timeout=60)


def same_dir(a, b):
    return (os.path.normcase(os.path.realpath(a))
            == os.path.normcase(os.path.realpath(b)))


class TestWorktree(WowdevTestBase):
    """App sessions run in git worktrees, which lack the gitignored tools."""

    def setUp(self):
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.make_addon("Alpha")
        for args in (["init", "-q"], ["add", "-A"],
                     ["commit", "-q", "-m", "init"]):
            r = git(self.root, *args)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.wt = os.path.join(self.tmp, "wt")
        r = git(self.root, "worktree", "add", "-q", "-b", "wt", self.wt)
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_wt(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        code = wowdev.main(list(argv) + ["--game-dir", self.game],
                           project_root=self.wt, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_main_checkout_found_only_from_worktree(self):
        found = wowdev.main_checkout(self.wt)
        self.assertIsNotNone(found)
        self.assertTrue(same_dir(found, self.root))
        self.assertIsNone(wowdev.main_checkout(self.root))

    def test_downloads_read_from_main_checkout(self):
        os.makedirs(os.path.join(self.root, "tools", "vendor"))
        cfg = wowdev.load_config(self.wt, self.game)
        out = io.StringIO()
        self.assertTrue(same_dir(wowdev.downloads_root(cfg, out), self.root))
        self.assertIn("read from the main checkout", out.getvalue())

    def test_downloads_missing_everywhere_stays_local(self):
        cfg = wowdev.load_config(self.wt, self.game)
        out = io.StringIO()
        self.assertTrue(same_dir(wowdev.downloads_root(cfg, out), self.wt))
        self.assertEqual(out.getvalue(), "")

    def test_deploy_all_refused_in_worktree_named_allowed(self):
        code, out, err = self.run_wt("deploy")
        self.assertEqual(code, 2)
        self.assertIn("git worktree", err)
        self.assertEqual(os.listdir(self.addons_root), [])
        code, out, err = self.run_wt("deploy", "Alpha")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(os.path.join(self.addons_root, "Alpha")))
        # The main checkout still deploys everything.
        code, out, err = self.run_cli("deploy")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
