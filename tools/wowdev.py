"""wowdev - deploy and read-back tooling for WoW addons.

Commands: deploy, undeploy, status, sv, new, lint. Run with -h for usage.
Configuration comes from wowdev.json at the project root (the parent of
tools/). Every command accepts --game-dir PATH to override game_dir.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import apicheck  # noqa: E402
import luasv  # noqa: E402

DEFAULT_PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
MARKER = ".wowdev-managed"
SKIP_DIRS = ("__pycache__", "tests")
NEW_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,40}$")
TARGET_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")

HINT_RESTART = "RESTART the game client"
HINT_RESTART_NEW = (HINT_RESTART + ": this addon folder is new to the game, "
                    "and /reload does not discover new addon folders")
HINT_RESTART_MEDIA = (HINT_RESTART + ": new image, sound or font files are "
                      "only found when the client starts")
HINT_RELOAD = "/reload in game is enough"
HINT_NONE = "up to date"
# Files the client indexes once at startup; adding one needs a restart.
MEDIA_EXTENSIONS = (".blp", ".tga", ".png", ".jpg", ".jpeg", ".ogg", ".mp3",
                    ".wav", ".ttf", ".otf", ".m2")


class WowdevError(Exception):
    """A user-facing error with an exit code."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- config

class Config(object):
    def __init__(self, project_root, game_dir, interface, addons_src,
                 dev_addons_src=None, author=None):
        self.project_root = project_root
        self.game_dir = game_dir
        self.interface = interface
        self.addons_src = addons_src
        # Optional second source folder for helper addons (DevBridge), kept
        # apart from the player's own addons in addons_src.
        self.dev_addons_src = dev_addons_src
        self.author = author

    @property
    def addons_dir(self):
        return os.path.join(self.project_root, self.addons_src)

    @property
    def source_srcs(self):
        """Configured source folders, relative to the project, lookup order."""
        srcs = [self.addons_src]
        if self.dev_addons_src:
            srcs.append(self.dev_addons_src)
        return srcs

    @property
    def source_dirs(self):
        """Absolute paths of the configured source folders, lookup order."""
        return [os.path.join(self.project_root, s) for s in self.source_srcs]

    def source_dirs_text(self):
        """Every configured source folder, for messages."""
        return ", ".join(self.source_dirs)

    @property
    def target_root(self):
        return os.path.join(self.game_dir, "Interface", "AddOns")

    @property
    def account_root(self):
        return os.path.join(self.game_dir, "WTF", "Account")


def load_config(project_root, game_dir_override=None):
    path = os.path.join(project_root, "wowdev.json")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise WowdevError("config not found: %s" % path)
    except (OSError, ValueError) as exc:
        raise WowdevError("config unreadable: %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise WowdevError("config %s: top level must be an object" % path)
    game_dir = data.get("game_dir")
    interface = data.get("interface")
    addons_src = data.get("addons_src")
    if not isinstance(game_dir, str) or not game_dir:
        raise WowdevError("config %s: 'game_dir' missing or not a string"
                          % path)
    if not isinstance(interface, int) or isinstance(interface, bool):
        raise WowdevError("config %s: 'interface' missing or not an integer"
                          % path)
    if not isinstance(addons_src, str) or not addons_src:
        raise WowdevError("config %s: 'addons_src' missing or not a string"
                          % path)
    dev_addons_src = None
    if "dev_addons_src" in data:
        dev_addons_src = data["dev_addons_src"]
        if not isinstance(dev_addons_src, str) or not dev_addons_src:
            raise WowdevError("config %s: 'dev_addons_src' must be a non-empty "
                              "string" % path)
        if (os.path.normcase(os.path.normpath(dev_addons_src))
                == os.path.normcase(os.path.normpath(addons_src))):
            raise WowdevError("config %s: 'dev_addons_src' must differ from "
                              "'addons_src'" % path)
    author = None
    if "author" in data:
        author = data["author"]
        if not isinstance(author, str) or not author:
            raise WowdevError("config %s: 'author' must be a non-empty string"
                              % path)
    if game_dir_override:
        game_dir = game_dir_override
    if not os.path.isabs(game_dir):
        game_dir = os.path.join(project_root, game_dir)
    return Config(project_root, os.path.normpath(game_dir), interface,
                  addons_src, dev_addons_src, author)


# ---------------------------------------------------------------- helpers

def is_link(path):
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(path):
        return True
    return False


def find_links(root):
    """Return paths at or under root that are symlinks or junctions."""
    found = []
    if is_link(root):
        return [root]
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if is_link(p):
                found.append(p)
        # Never descend into a link.
        dirnames[:] = [d for d in dirnames
                       if not is_link(os.path.join(dirpath, d))]
    return found


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_valid_addon_dir(path, name):
    if not os.path.isdir(path):
        return False
    low = name.lower()
    for fn in os.listdir(path):
        fl = fn.lower()
        if not fl.endswith(".toc") or not os.path.isfile(
                os.path.join(path, fn)):
            continue
        stem = fl[:-4]
        if stem == low or stem.startswith(low + "_"):
            return True
    return False


def addon_sources(cfg):
    """Map name -> source path of every valid addon in the source folders.

    Folders are searched in order (addons_src, then dev_addons_src); a
    missing folder counts as empty. A folder name present in more than one
    source folder (compared case-insensitively on Windows, like the game's
    AddOns folder) is an error naming both paths, so it is never unclear
    which copy deploys.
    """
    seen = {}    # normcased name -> path, every non-hidden folder
    found = {}
    for d in cfg.source_dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.startswith("."):
                continue
            p = os.path.join(d, name)
            if not os.path.isdir(p):
                continue
            key = os.path.normcase(name)
            if key in seen:
                raise WowdevError("addon %r is in more than one source "
                                  "folder: %s and %s; keep one"
                                  % (name, seen[key], p))
            seen[key] = p
            if is_valid_addon_dir(p, name):
                found[name] = p
    return found


def source_addons(cfg):
    """Return sorted list of valid source addon folder names."""
    return sorted(addon_sources(cfg))


def addon_source_path(cfg, name):
    """The source folder of addon `name`, or None when it is not a valid
    source addon in any configured folder."""
    return addon_sources(cfg).get(name)


def source_files(src):
    """Map relpath -> abspath of files to deploy from src."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            p = os.path.join(dirpath, fn)
            out[os.path.relpath(p, src)] = p
    return out


def target_files(dst):
    out = {}
    if not os.path.isdir(dst):
        return out
    for dirpath, dirnames, filenames in os.walk(dst, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, dst)
            if rel == MARKER:
                continue
            out[rel] = p
    return out


def files_differ(a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        return True
    return sha1_of(a) != sha1_of(b)


class Plan(object):
    def __init__(self, name, src, dst):
        self.name = name
        self.src = src
        self.dst = dst
        self.refused = None      # reason string when refused
        self.conflict = False    # target exists but is not ours
        self.exists = False
        self.added = []
        self.changed = []
        self.removed = []

    @property
    def total(self):
        return len(self.added) + len(self.changed) + len(self.removed)

    def hint(self):
        # Sources (2026-11): /reload picks up edited and new .lua/.xml files
        # and TOC changes inside an addon the client already knows (since
        # 9.0.1), but a brand-new addon folder or new media file needs a
        # client restart. See docs/wiki/research/forever-addon-api.md.
        if not self.total:
            return HINT_NONE
        if not self.exists:
            return HINT_RESTART_NEW
        if any(p.lower().endswith(MEDIA_EXTENSIONS) for p in self.added):
            return HINT_RESTART_MEDIA
        return HINT_RELOAD


def make_plan(cfg, name, src=None):
    if src is None:
        src = addon_source_path(cfg, name)
    if src is None:
        raise WowdevError("not a valid source addon in %s: %s"
                          % (cfg.source_dirs_text(), name))
    dst = os.path.join(cfg.target_root, name)
    plan = Plan(name, src, dst)
    if os.path.lexists(dst):
        plan.exists = True
        links = find_links(dst)
        if links:
            plan.refused = ("target is or contains a symlink/junction (%s); "
                            "refusing" % links[0])
            return plan
        if not os.path.isdir(dst):
            plan.conflict = True
            plan.refused = ("target exists and is not a directory: %s"
                            % dst)
            return plan
        if not os.path.isfile(os.path.join(dst, MARKER)):
            plan.conflict = True
            plan.refused = ("target %s exists and is not managed by wowdev "
                            "(no %s marker); it belongs to someone else, "
                            "refusing" % (dst, MARKER))
            return plan
    sfiles = source_files(src)
    tfiles = target_files(dst)
    for rel in sorted(sfiles):
        if rel not in tfiles:
            plan.added.append(rel)
        elif files_differ(sfiles[rel], tfiles[rel]):
            plan.changed.append(rel)
    plan.removed = sorted(rel for rel in tfiles if rel not in sfiles)
    return plan


def apply_plan(plan):
    dst = plan.dst
    # Re-check right before writing (fail closed).
    if find_links(dst):
        raise WowdevError("link appeared under %s; refusing" % dst, 1)
    os.makedirs(dst, exist_ok=True)
    for rel in plan.removed:
        os.remove(os.path.join(dst, rel))
    # Prune empty directories bottom-up (never the target root).
    for dirpath, dirnames, filenames in os.walk(dst, topdown=False):
        if os.path.normcase(dirpath) == os.path.normcase(dst):
            continue
        if not os.listdir(dirpath):
            os.rmdir(dirpath)
    for rel in plan.added + plan.changed:
        s = os.path.join(plan.src, rel)
        t = os.path.join(dst, rel)
        if os.path.isdir(t) and not is_link(t):
            shutil.rmtree(t)
        os.makedirs(os.path.dirname(t), exist_ok=True)
        shutil.copy2(s, t)
    marker = {
        "source": os.path.abspath(plan.src),
        "deployed_utc": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(dst, MARKER), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(marker, f, indent=2)
        f.write("\n")


def require_addons_root(cfg):
    if not os.path.isdir(cfg.game_dir):
        raise WowdevError("game dir not found: %s" % cfg.game_dir)
    if not os.path.isdir(cfg.target_root):
        raise WowdevError("AddOns folder not found: %s" % cfg.target_root)
    if is_link(cfg.target_root):
        raise WowdevError("AddOns folder is a symlink/junction; refusing: %s"
                          % cfg.target_root)


# ---------------------------------------------------------------- commands

def cmd_deploy(cfg, args, out):
    sources = addon_sources(cfg)
    available = sorted(sources)
    if args.names:
        bad = [n for n in args.names if n not in sources]
        if bad:
            raise WowdevError(
                "not a valid source addon (needs <folder>/<Name>/<Name>.toc "
                "or <Name>_<Suffix>.toc, where <folder> is one of %s): %s"
                % (", ".join(s + "/" for s in cfg.source_srcs),
                   ", ".join(bad)))
        names = list(dict.fromkeys(args.names))
    else:
        # A worktree may be on an older branch; deploying everything from it
        # would replace newer copies of addons it is not working on.
        if main_checkout(cfg.project_root):
            raise WowdevError(
                "this is a git worktree: name the addons to deploy, e.g. "
                "'deploy MyAddon' (deploying all of them could replace newer "
                "copies from another branch)")
        names = available
    if not names:
        out.write("no source addons found in %s\n" % cfg.source_dirs_text())
        return 0
    require_addons_root(cfg)
    failed = False
    for name in names:
        plan = make_plan(cfg, name, sources[name])
        out.write("== %s -> %s\n" % (name, plan.dst))
        if plan.refused:
            out.write("  REFUSED: %s\n" % plan.refused)
            failed = True
            continue
        for label, items in (("added", plan.added),
                             ("changed", plan.changed),
                             ("removed", plan.removed)):
            out.write("  %s (%d)%s\n" % (label, len(items),
                                         ":" if items else ""))
            for rel in items:
                out.write("    %s\n" % rel)
        if args.dry_run:
            out.write("  (dry run, nothing written)\n")
        else:
            needs_marker = not os.path.isfile(os.path.join(plan.dst, MARKER))
            if plan.total or needs_marker:
                apply_plan(plan)
        out.write("  %s\n" % plan.hint())
    return 1 if failed else 0


def cmd_undeploy(cfg, args, out):
    name = args.name
    if not TARGET_NAME_RE.match(name) or name in (".", ".."):
        raise WowdevError("invalid addon name: %r" % name)
    require_addons_root(cfg)
    dst = os.path.join(cfg.target_root, name)
    if not os.path.lexists(dst):
        raise WowdevError("not deployed: %s" % dst, 1)
    links = find_links(dst)
    if links:
        raise WowdevError("target is or contains a symlink/junction (%s); "
                          "refusing" % links[0], 1)
    if not os.path.isdir(dst) or not os.path.isfile(
            os.path.join(dst, MARKER)):
        raise WowdevError("target %s is not managed by wowdev (no %s marker); "
                          "refusing" % (dst, MARKER), 1)
    shutil.rmtree(dst)
    out.write("removed %s\n" % dst)
    return 0


def wow_running():
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Wow.exe", "/NH"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if r.returncode != 0:
        return "unknown"
    return "yes" if "wow.exe" in r.stdout.lower() else "no"


def account_names(cfg):
    root = cfg.account_root
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root)
                  if n != "SavedVariables"
                  and os.path.isdir(os.path.join(root, n)))


def cmd_status(cfg, args, out):
    out.write("game dir: %s (exists: %s)\n"
              % (cfg.game_dir, "yes" if os.path.isdir(cfg.game_dir) else "no"))
    out.write("Wow.exe running: %s\n" % wow_running())
    accts = account_names(cfg)
    out.write("accounts: %s\n" % (", ".join(accts) if accts else "(none)"))
    sources = addon_sources(cfg)
    names = sorted(sources)
    if not names:
        out.write("source addons: (none in %s)\n" % cfg.source_dirs_text())
        return 0
    if cfg.dev_addons_src:
        # Group by folder so it is clear which copy each addon comes from.
        groups = []
        for src_rel, d in zip(cfg.source_srcs, cfg.source_dirs):
            in_dir = [n for n in names
                      if os.path.dirname(sources[n]) == d]
            groups.append(("source addons in %s/:" % src_rel, in_dir))
    else:
        groups = [("source addons:", names)]
    for header, group in groups:
        out.write("%s\n" % header)
        if not group:
            out.write("  (none)\n")
        for name in group:
            _status_line(cfg, name, sources[name], out)
    return 0


def _status_line(cfg, name, src, out):
    plan = make_plan(cfg, name, src)
    if plan.conflict:
        state = "CONFLICT (target exists, not managed by wowdev)"
    elif plan.refused:
        state = "REFUSED (%s)" % plan.refused
    elif not plan.exists:
        state = "not deployed"
    elif plan.total == 0:
        state = "deployed (in sync)"
    else:
        state = "deployed (stale: %d files differ)" % plan.total
    out.write("  %s: %s\n" % (name, state))


def _sv_header(path, err):
    mtime = os.path.getmtime(path)
    local = datetime.datetime.fromtimestamp(mtime).astimezone()
    age_min = (datetime.datetime.now().timestamp() - mtime) / 60.0
    err.write("file: %s\n" % path)
    err.write("modified: %s (age %.1f minutes)\n"
              % (local.isoformat(timespec="seconds"), age_min))
    err.write("note: the client writes SavedVariables only on /reload, "
              "logout or exit\n")


def _read_sv(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    try:
        return luasv.parse_savedvariables(text)
    except luasv.LuaSVParseError as exc:
        raise WowdevError("parse error in %s at line %d, column %d: %s"
                          % (path, exc.line, exc.column, exc.message), 2)


def cmd_sv(cfg, args, out, err):
    addon = args.addon
    if not TARGET_NAME_RE.match(addon) or addon in (".", ".."):
        raise WowdevError("invalid addon name: %r" % addon)
    fname = addon + ".lua"
    root = cfg.account_root
    if not os.path.isdir(root):
        raise WowdevError("WTF account folder not found: %s" % root, 1)
    accts = account_names(cfg)
    if args.account:
        if args.account not in accts:
            raise WowdevError("account %r not found; found: %s"
                              % (args.account, ", ".join(accts) or "(none)"))
        use_accts = [args.account]
    elif args.all:
        use_accts = accts
    else:
        if not accts:
            raise WowdevError("no account folders under %s" % root, 1)
        if len(accts) > 1:
            raise WowdevError("more than one account; pass --account. "
                              "found: %s" % ", ".join(accts))
        use_accts = accts

    if args.all:
        paths = []
        for acc in use_accts:
            ad = os.path.join(root, acc)
            p = os.path.join(ad, "SavedVariables", fname)
            if os.path.isfile(p):
                paths.append(p)
            for a in sorted(os.listdir(ad)):
                pa = os.path.join(ad, a)
                if a == "SavedVariables" or not os.path.isdir(pa):
                    continue
                for b in sorted(os.listdir(pa)):
                    p = os.path.join(pa, b, "SavedVariables", fname)
                    if os.path.isfile(p):
                        paths.append(p)
        if not paths:
            raise WowdevError("no %s found under %s" % (fname, root), 1)
    else:
        acc_dir = os.path.join(root, use_accts[0])
        if args.char:
            parts = [p for p in re.split(r"[\\/]", args.char) if p]
            if len(parts) != 2 or any(p in (".", "..") for p in parts):
                raise WowdevError("--char must look like A/B (e.g. 91/Name)")
            p = os.path.join(acc_dir, parts[0], parts[1], "SavedVariables",
                             fname)
        else:
            p = os.path.join(acc_dir, "SavedVariables", fname)
        if not os.path.isfile(p):
            raise WowdevError("SavedVariables file not found: %s" % p, 1)
        paths = [p]

    results = {}
    for p in paths:
        _sv_header(p, err)
        data = _read_sv(p)
        if args.var:
            if args.var not in data:
                if not args.all:
                    raise WowdevError("variable %r not in %s (has: %s)"
                                      % (args.var, p,
                                         ", ".join(data) or "(none)"), 1)
                data = None
            else:
                data = {args.var: data[args.var]}
        results[p] = data
    payload = results if args.all else results[paths[0]]
    text = json.dumps(luasv.jsonable(payload), indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        err.write("wrote %s\n" % args.out)
    else:
        out.write(text + "\n")
    return 0


def cmd_new(cfg, args, out):
    name = args.name
    if not NEW_NAME_RE.match(name):
        raise WowdevError("invalid name %r (must match %s)"
                          % (name, NEW_NAME_RE.pattern))
    title = args.title if args.title else name
    tpl = os.path.join(cfg.project_root, "templates", "addon")
    if not os.path.isdir(tpl):
        raise WowdevError("template not found: %s" % tpl)
    dest = os.path.join(cfg.addons_dir, name)
    for d in cfg.source_dirs:
        p = os.path.join(d, name)
        if os.path.lexists(p):
            raise WowdevError("already exists: %s" % p)
        # Also refuse a case-only variant (Windows folders ignore case).
        if os.path.isdir(d):
            for other in os.listdir(d):
                if os.path.normcase(other) == os.path.normcase(name):
                    raise WowdevError("already exists: %s"
                                      % os.path.join(d, other))
    subs = (("__UPPERNAME__", name.upper()),
            ("__LOWERNAME__", name.lower()),
            ("__NAME__", name), ("__TITLE__", title),
            ("__AUTHOR__", cfg.author or "Unknown"),
            ("__INTERFACE__", str(cfg.interface)))
    written = []
    for dirpath, dirnames, filenames in os.walk(tpl):
        dirnames.sort()
        for fn in sorted(filenames):
            sp = os.path.join(dirpath, fn)
            rel = os.path.relpath(sp, tpl)
            rel = os.sep.join(part.replace("__NAME__", name)
                              for part in rel.split(os.sep))
            dp = os.path.join(dest, rel)
            with open(sp, "r", encoding="utf-8") as f:
                content = f.read()
            for k, v in subs:
                content = content.replace(k, v)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            with open(dp, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            written.append(rel)
    out.write("created %s\n" % dest)
    for rel in written:
        out.write("  %s\n" % rel)
    _register_luacheck_globals(cfg, name, out)
    return 0


def _register_luacheck_globals(cfg, name, out):
    """Declare the template's globals for a new addon in .luacheckrc.

    Idempotent: skips when the addon's files[...] line is already present.
    """
    path = os.path.join(cfg.project_root, ".luacheckrc")
    if not os.path.isfile(path):
        out.write("note: no .luacheckrc; luacheck globals not registered\n")
        return
    key = 'files["%s/%s/**"]' % (cfg.addons_src, name)
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    if key in text:
        return
    line = '%s = { globals = { "%sDB", "SLASH_%s1" } }\n' % (
        key, name, name.upper())
    if text and not text.endswith("\n"):
        line = "\n" + line
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(line)
    out.write("registered %s's globals in .luacheckrc\n" % name)


def main_checkout(project_root):
    """The main checkout's folder when project_root is a linked git worktree,
    else None (also None when git is missing or the answer is unclear).

    App sessions usually run in a worktree, which has none of the gitignored
    downloads (tools/bin, tools/vendor) or game exports (data/).
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-dir",
             "--git-common-dir"],
            cwd=project_root, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.splitlines()
    if r.returncode != 0 or len(lines) != 2:
        return None
    git_dir, common = (os.path.normpath(p) for p in lines)
    if os.path.normcase(git_dir) == os.path.normcase(common):
        return None
    if os.path.basename(common).lower() != ".git":
        return None
    main = os.path.dirname(common)
    return main if os.path.isdir(main) else None


def _has_downloads(root):
    return any(os.path.isdir(os.path.join(root, "tools", d))
               for d in ("bin", "vendor"))


def downloads_root(cfg, out):
    """Where lint reads tools/bin, tools/vendor and data/ from.

    This checkout when it has them; otherwise, in a worktree, the main
    checkout (read only, and named in the output); otherwise this checkout,
    so the usual "not installed" and "missing" failures appear.
    """
    here = cfg.project_root
    if _has_downloads(here):
        return here
    main = main_checkout(here)
    if main and _has_downloads(main):
        out.write("note: git worktree; checkers and API lists are read from "
                  "the main checkout %s\n" % main)
        return main
    return here


def _find_tool(root, local_rel, exe_name):
    p = os.path.join(root, "tools", "bin", *local_rel)
    if os.path.isfile(p):
        return p
    return shutil.which(exe_name)


def cmd_lint(cfg, args, out):
    code = 0
    skipped = False
    tools_root = downloads_root(cfg, out)
    luacheck = _find_tool(tools_root, ["luacheck.exe"], "luacheck")
    sources = addon_sources(cfg)
    # Every existing source folder, in one luacheck run. When none exists,
    # still name addons_src so luacheck fails on it rather than checking
    # nothing.
    lint_srcs = [s for s, d in zip(cfg.source_srcs, cfg.source_dirs)
                 if os.path.isdir(d)] or [cfg.addons_src]
    if luacheck is None:
        out.write("SKIPPED: luacheck not installed (put luacheck.exe in "
                  "tools/bin/ or luacheck on PATH)\n")
        skipped = True
    else:
        out.write("== luacheck\n")
        out.flush()
        r = subprocess.run([luacheck] + lint_srcs + ["--codes"],
                           cwd=cfg.project_root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out.write(r.stdout)
        if r.stderr:
            out.write(r.stderr)
        if r.returncode != 0:
            code = max(code, 1)
    # wowlua-ls knows the WoW API per flavor (.wowluarc.json sets "forever"),
    # so it owns "does this API exist on Forever / is it called correctly".
    wowlua = _find_tool(tools_root, ["wowlua_ls.exe"], "wowlua_ls")
    if wowlua is None:
        out.write("SKIPPED: wowlua-ls not installed (put wowlua_ls.exe in "
                  "tools/bin/ or wowlua_ls on PATH)\n")
        skipped = True
    else:
        for name in sorted(sources):
            out.write("== wowlua-ls %s\n" % name)
            out.flush()
            r = subprocess.run(
                [wowlua, "check",
                 os.path.relpath(sources[name], cfg.project_root)],
                cwd=cfg.project_root, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            out.write(r.stdout)
            if r.stderr:
                out.write(r.stderr)
            if r.returncode != 0:
                code = max(code, 1)
            # A run that checked nothing must not look like a clean pass.
            # (wowlua-ls prints its summary on stderr.)
            m = re.search(r"Checked (\d+) files?", r.stdout + r.stderr)
            if not m or int(m.group(1)) == 0:
                out.write("FAILED: wowlua-ls reported no files checked for "
                          "%s\n" % name)
                code = max(code, 1)
    # apicheck owns "does this global / event exist on Forever", because
    # wowlua-ls's stubs still accept removed Classic-era globals.
    out.write("== apicheck\n")
    rc = apicheck.run(cfg.project_root, cfg.source_dirs, out,
                      truth_root=tools_root)
    if rc == 2:
        skipped = True
    elif rc:
        code = max(code, 1)
    if skipped:
        return 3
    return code


def cmd_export_api(cfg, args, out, err):
    """Write DevBridge's in-game API inventory to data/api-inventory.json."""
    accts = account_names(cfg)
    if len(accts) != 1 and not args.account:
        raise WowdevError("pass --account; found: %s"
                          % (", ".join(accts) or "(none)"))
    acc = args.account or accts[0]
    path = os.path.join(cfg.account_root, acc, "SavedVariables",
                        "DevBridge.lua")
    if not os.path.isfile(path):
        raise WowdevError("no DevBridge SavedVariables at %s" % path, 1)
    _sv_header(path, err)
    db = _read_sv(path).get("DevBridgeDB") or {}
    api = db.get("api")
    if not isinstance(api, dict) or not api.get("globals"):
        raise WowdevError("DevBridgeDB has no API inventory yet: type /dvb api "
                          "in game, then /reload, then run this again", 1)
    dest = os.path.join(cfg.project_root, "data", "api-inventory.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(luasv.jsonable(api), f, indent=1, sort_keys=True)
        f.write("\n")
    namespaces = api.get("namespaces") or {}
    out.write("wrote %s: build %s, %d globals, %d C_ namespaces\n"
              % (dest, api.get("build"), len(api.get("globals") or []),
                 len(namespaces)))
    return 0


# ---------------------------------------------------------------- main

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--game-dir", help="override game_dir from config")
    p = argparse.ArgumentParser(prog="wowdev", description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("deploy", parents=[common],
                       help="copy <Name> from the source folders (addons_src, "
                       "dev_addons_src) into Interface\\AddOns")
    d.add_argument("names", nargs="*", metavar="NAME")
    d.add_argument("--dry-run", action="store_true")

    u = sub.add_parser("undeploy", parents=[common],
                       help="remove a wowdev-managed deployed addon")
    u.add_argument("name", metavar="NAME")

    sub.add_parser("status", parents=[common], help="show deploy state")

    s = sub.add_parser("sv", parents=[common],
                       help="print SavedVariables as JSON")
    s.add_argument("addon", metavar="ADDON")
    s.add_argument("--var")
    s.add_argument("--account")
    s.add_argument("--char", metavar="A/B")
    s.add_argument("--out", metavar="FILE")
    s.add_argument("--all", action="store_true")

    n = sub.add_parser("new", parents=[common],
                       help="scaffold NAME in addons_src from templates/addon")
    n.add_argument("name", metavar="NAME")
    n.add_argument("--title")

    sub.add_parser("lint", parents=[common],
                   help="run luacheck, wowlua-ls and apicheck over every "
                   "source folder")

    e = sub.add_parser("export-api", parents=[common],
                       help="save DevBridge's in-game API inventory to "
                       "data/api-inventory.json")
    e.add_argument("--account")
    return p


def main(argv=None, project_root=None, out=None, err=None):
    if out is None:
        out = sys.stdout
    if err is None:
        err = sys.stderr
    for stream in (out, err):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is not None:
            try:
                reconf(encoding="utf-8")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args(argv)
    root = project_root or DEFAULT_PROJECT_ROOT
    try:
        cfg = load_config(root, args.game_dir)
        if args.command == "deploy":
            return cmd_deploy(cfg, args, out)
        if args.command == "undeploy":
            return cmd_undeploy(cfg, args, out)
        if args.command == "status":
            return cmd_status(cfg, args, out)
        if args.command == "sv":
            return cmd_sv(cfg, args, out, err)
        if args.command == "new":
            return cmd_new(cfg, args, out)
        if args.command == "lint":
            return cmd_lint(cfg, args, out)
        if args.command == "export-api":
            return cmd_export_api(cfg, args, out, err)
        raise WowdevError("unknown command %r" % args.command)
    except WowdevError as exc:
        out.flush()
        err.write("error: %s\n" % exc)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
