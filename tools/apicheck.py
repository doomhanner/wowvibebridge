"""apicheck - does every WoW API call and event name in the addon source folders
(addons/, devaddons/) exist on Forever?

Why this exists: wowlua-ls (the type checker used by `wowdev lint`) ships
stubs that still list Classic-era globals such as GetSpellInfo, GetItemInfo
and UnitAura as callable, and its flavor filter does not take effect from the
command line, so it passes code that errors on the Forever client. This check
compares calls against a list of what the client really exposes.

Truth sources, best first:
  globals: data/api-inventory.json (taken in game by DevBridge, `/dvb api`,
           exported with `wowdev export-api`), else Ketho's
           BlizzardInterfaceResources "forever" branch (GlobalAPI.lua and
           FrameXML.lua) under tools/vendor/.
  events:  LiteralName entries in Blizzard_APIDocumentationGenerated from the
           Gethe/wow-ui-source "forever" clone, plus Ketho's Events.lua.
A missing truth source is a failure, never a silent pass.

What is checked (comments and strings are ignored for code references):
  - every C_Namespace.Function reference, called or not
  - calls to capitalised bare globals Name(...) that are not defined in this
    project (WoW API convention is CapitalCase; project locals are camelCase)
  - capitalised globals captured in a local (`local GetSpellInfo =
    GetSpellInfo`, `local f = _G.UnitAura`) and passed to pcall/xpcall
  - hooksecurefunc("GlobalName", ...)
  - event names: string literals given to RegisterEvent / RegisterUnitEvent /
    UnregisterEvent (with or without parentheses),
    EventRegistry:RegisterFrameEventAndCallback, FrameUtil.RegisterFrameForEvents
    tables, and literal lists looped over with ipairs to register events;
    plus events known never to fire for addons on Forever.

Not checked, by design: lowercase globals (strsplit, wipe, require, getglobal),
calls through variables whose source is not a plain global, and event names
built at runtime. Code that registers dynamic events should use pcall or
C_EventUtils.IsEventValid.
"""

import json
import os
import re
import subprocess

# Events that exist in the docs but never reach addons on Forever.
KNOWN_DEAD_EVENTS = {
    "COMBAT_LOG_EVENT_UNFILTERED": (
        "never fires for addons on Forever (Midnight addon restrictions); "
        "use C_DamageMeter or UNIT_COMBAT"),
}

REF_NS_RE = re.compile(r"(?<![\w.:])(C_\w+)\s*\.\s*(\w+)")
CALL_BARE_RE = re.compile(r"(?<![\w.:])([A-Z]\w*)\s*\(")
# local alias = Global | _G.Global | _G["Global"] | C_NS.Fn  (whole statement)
ALIAS_RE = re.compile(
    r"\blocal\s+([A-Za-z_]\w*)\s*=\s*"
    r"(?:_G\s*\.\s*|_G\s*\[\s*[\"'])?"
    r"([A-Za-z_]\w*(?:\s*\.\s*\w+)?)"
    r"\s*(?:[\"']\s*\])?[ \t]*(?=;|\n|$)")
FUNCREF_RE = re.compile(
    r"\b(?:pcall|xpcall|securecall|securecallfunction)\s*\(\s*([A-Z]\w*)\s*[,)]")
HOOK_RE = re.compile(r"\bhooksecurefunc\s*\(\s*[\"']([A-Za-z_]\w*)[\"']")
EVENT_RE = re.compile(
    r"\b(?:Register|Unregister)(?:Unit)?Event\s*"
    r"(?:\(\s*(?:[\w.]+\s*,\s*)?)?[\"']([A-Za-z0-9_]+)[\"']")
EVENT_CALLBACK_RE = re.compile(
    r"\b(?:Register|Unregister)FrameEventAndCallback\s*\(\s*"
    r"[\"']([A-Za-z0-9_]+)[\"']")
EVENT_TABLE_RE = re.compile(
    r"\bRegisterFrameForEvents\s*\(\s*[\w.:]+\s*,\s*\{([^}]*)\}")
EVENT_LOOP_RE = re.compile(
    r"\bin\s+ipairs\s*\(\s*\{([^}]*)\}\s*\)\s*do(.{0,400})", re.S)
EVENT_LOOP_BODY_RE = re.compile(r"Register(?:Unit)?Event\s*\(")
DEF_RES = (
    re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\("),
    re.compile(r"\blocal\s+function\s+([A-Za-z_]\w*)"),
    # Global assignment at column 0 (Name = ..., MyFn = MyFn or function()).
    # Indented `Name = ...` is usually a table field, so it does not count.
    re.compile(r"^([A-Za-z_]\w*)\s*=(?!=)", re.M),
    # Explicit _G.Name = ... at any indentation.
    re.compile(r"^[ \t]*_G\s*\.\s*([A-Za-z_]\w*)\s*=(?!=)", re.M),
)
LOCAL_LIST_RE = re.compile(r"\blocal\s+([A-Za-z_][\w\s,]*?)\s*(?:=|$|\n)")
PARAMS_RE = re.compile(r"\bfunction\b[^(]*\(([^)]*)\)")
FOR_RE = re.compile(r"\bfor\s+([A-Za-z_][\w\s,]*?)\s+(?:in|=)")
QUOTED_RE = re.compile(r'"([^"\n]+)"')
ANY_QUOTED_RE = re.compile(r"[\"']([A-Za-z0-9_]+)[\"']")


class Finding(object):
    def __init__(self, path, line, name, message):
        self.path = path
        self.line = line
        self.name = name
        self.message = message

    def __str__(self):
        return "%s:%d: %s" % (self.path, self.line, self.message)


def strip_lua(text, keep_strings=False):
    """Blank out comments (and string contents unless keep_strings).

    Newlines are preserved so line numbers stay correct.
    """
    out = []
    i = 0
    n = len(text)

    def blank(segment):
        return "".join("\n" if ch == "\n" else " " for ch in segment)

    while i < n:
        ch = text[i]
        if ch == "-" and text.startswith("--", i):
            m = re.match(r"--\[(=*)\[", text[i:])
            if m:
                close = "]" + m.group(1) + "]"
                end = text.find(close, i + len(m.group(0)))
                end = n if end < 0 else end + len(close)
            else:
                end = text.find("\n", i)
                end = n if end < 0 else end
            out.append(blank(text[i:end]))
            i = end
            continue
        if ch == "[":
            m = re.match(r"\[(=*)\[", text[i:])
            if m:
                close = "]" + m.group(1) + "]"
                end = text.find(close, i + len(m.group(0)))
                end = n if end < 0 else end + len(close)
                out.append(text[i:end] if keep_strings else blank(text[i:end]))
                i = end
                continue
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
            if keep_strings:
                out.append(text[i:end])
            else:
                out.append(ch + blank(text[i + 1:end - 1]) + ch
                           if end - i >= 2 else blank(text[i:end]))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _quoted_names(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return set(QUOTED_RE.findall(f.read()))


def _git_head(path):
    try:
        r = subprocess.run(["git", "-C", path, "log", "-1", "--format=%s"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else "unknown build"
    except (OSError, subprocess.SubprocessError):
        return "unknown build"


class Truth(object):
    def __init__(self):
        self.globals = set()
        self.namespaces = set()
        self.events = set()
        self.globals_source = None
        self.events_source = None


def load_truth(project_root):
    truth = Truth()
    vendor = os.path.join(project_root, "tools", "vendor")
    inventory = os.path.join(project_root, "data", "api-inventory.json")
    bir = os.path.join(vendor, "BlizzardInterfaceResources")
    bir_res = os.path.join(bir, "Resources")
    # Both sources are merged when present. The in-game inventory adds what
    # the exact client build has; Ketho's list covers Blizzard load-on-demand
    # addons that were not loaded when the inventory was taken.
    global_sources = []
    if os.path.isfile(inventory):
        with open(inventory, "r", encoding="utf-8") as f:
            inv = json.load(f)
        truth.globals.update(inv.get("globals") or [])
        for ns, fns in (inv.get("namespaces") or {}).items():
            truth.namespaces.add(ns)
            for fn in fns or []:
                truth.globals.add("%s.%s" % (ns, fn))
        global_sources.append("in-game inventory (build %s)"
                              % inv.get("build", "?"))
    if os.path.isfile(os.path.join(bir_res, "GlobalAPI.lua")):
        for name in ("GlobalAPI.lua", "FrameXML.lua"):
            p = os.path.join(bir_res, name)
            if os.path.isfile(p):
                truth.globals.update(_quoted_names(p))
        global_sources.append("BlizzardInterfaceResources forever (%s)"
                              % _git_head(bir))
    for g in truth.globals:
        if g.startswith("C_") and "." in g:
            truth.namespaces.add(g.split(".", 1)[0])
    if global_sources:
        truth.globals_source = " + ".join(global_sources)
    docs = os.path.join(vendor, "wow-ui-source", "Interface", "AddOns",
                        "Blizzard_APIDocumentationGenerated")
    sources = []
    if os.path.isdir(docs):
        lit = re.compile(r'LiteralName\s*=\s*"([A-Z0-9_]+)"')
        for fn in os.listdir(docs):
            if fn.endswith(".lua"):
                with open(os.path.join(docs, fn), "r", encoding="utf-8",
                          errors="replace") as f:
                    truth.events.update(lit.findall(f.read()))
        sources.append("wow-ui-source forever docs (%s)" % _git_head(
            os.path.join(vendor, "wow-ui-source")))
    events_lua = os.path.join(bir_res, "Events.lua")
    if os.path.isfile(events_lua):
        truth.events.update(n for n in _quoted_names(events_lua)
                            if re.match(r"^[A-Z0-9_]+$", n))
        sources.append("BlizzardInterfaceResources Events.lua")
    if sources:
        truth.events_source = " + ".join(sources)
    return truth


def _names_in_list(text):
    return [p.strip() for p in text.split(",") if p.strip()]


def _self_alias_blanked(code):
    """Blank `local X = X` / `local X = _G.X` so X is not treated as defined.

    Caching a global in a same-named local must not hide a missing global.
    """
    def repl(m):
        ref = re.sub(r"\s+", "", m.group(2))
        if ref == m.group(1):
            return " " * len(m.group(0))
        return m.group(0)
    return ALIAS_RE.sub(repl, code)


def defined_names(text):
    """Names a file defines or declares: functions, locals, params, loop vars."""
    code = _self_alias_blanked(strip_lua(text))
    names = set()
    for rx in DEF_RES:
        names.update(rx.findall(code))
    for m in LOCAL_LIST_RE.finditer(code):
        names.update(_names_in_list(m.group(1)))
    for m in PARAMS_RE.finditer(code):
        names.update(n for n in _names_in_list(m.group(1)) if n != "...")
    for m in FOR_RE.finditer(code):
        names.update(_names_in_list(m.group(1)))
    return names


def _line_of(text, index):
    return text.count("\n", 0, index) + 1


def scan_text(path, text, truth, project_defined):
    findings = []
    seen = set()

    def add(line, name, message):
        if (line, name) not in seen:
            seen.add((line, name))
            findings.append(Finding(path, line, name, message))

    def check_ns(line, ns, fn):
        full = "%s.%s" % (ns, fn)
        if full not in truth.globals:
            what = ("namespace %s does not exist" % ns
                    if ns not in truth.namespaces else
                    "%s has no function %s" % (ns, fn))
            add(line, full, "'%s' not found on Forever (%s)" % (full, what))

    def check_bare(line, name, defined):
        if name in defined or name in truth.globals:
            return
        add(line, name, "'%s' is not a known global on Forever" % name)

    code = strip_lua(text)
    local_defined = defined_names(text) | project_defined
    for m in REF_NS_RE.finditer(code):
        check_ns(_line_of(code, m.start()), m.group(1), m.group(2))
    for m in CALL_BARE_RE.finditer(code):
        check_bare(_line_of(code, m.start()), m.group(1), local_defined)
    for m in FUNCREF_RE.finditer(code):
        check_bare(_line_of(code, m.start()), m.group(1), local_defined)
    # Globals captured in locals: the source must exist even if the alias is
    # only called later (or under the same name).
    for m in ALIAS_RE.finditer(code):
        ref = re.sub(r"\s+", "", m.group(2))
        line = _line_of(code, m.start())
        if "." in ref:
            ns, fn = ref.split(".", 1)
            if ns.startswith("C_"):
                check_ns(line, ns, fn)
        elif ref[:1].isupper():
            # defined_names already ignores `local X = X`, so a same-named
            # alias does not count as a definition of X here.
            check_bare(line, ref, local_defined)

    with_strings = strip_lua(text, keep_strings=True)
    for m in HOOK_RE.finditer(with_strings):
        name = m.group(1)
        if name not in truth.globals and name not in project_defined:
            add(_line_of(with_strings, m.start()), name,
                "hooksecurefunc target '%s' is not a known global on Forever"
                % name)

    def check_event(line, event):
        if event in KNOWN_DEAD_EVENTS:
            add(line, event, "event %s %s" % (event, KNOWN_DEAD_EVENTS[event]))
        elif event not in truth.events:
            add(line, event,
                "event '%s' is not in the Forever API docs; registering an "
                "unknown event raises an error and stops the file" % event)

    for rx in (EVENT_RE, EVENT_CALLBACK_RE):
        for m in rx.finditer(with_strings):
            check_event(_line_of(with_strings, m.start()), m.group(1))
    for m in EVENT_TABLE_RE.finditer(with_strings):
        base = m.start(1)
        for q in ANY_QUOTED_RE.finditer(m.group(1)):
            check_event(_line_of(with_strings, base + q.start()), q.group(1))
    for m in EVENT_LOOP_RE.finditer(with_strings):
        if not EVENT_LOOP_BODY_RE.search(m.group(2)):
            continue
        base = m.start(1)
        for q in ANY_QUOTED_RE.finditer(m.group(1)):
            check_event(_line_of(with_strings, base + q.start()), q.group(1))
    findings.sort(key=lambda f: (f.line, f.name))
    return findings


def lua_files(addons_dir):
    for dirpath, dirnames, filenames in os.walk(addons_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.lower().endswith(".lua"):
                yield os.path.join(dirpath, fn)


def run(project_root, source_dirs, out, truth_root=None):
    """Print findings; return 0 clean, 1 findings, 2 missing truth source.

    source_dirs is a list of addon source folders (for example addons/ and
    devaddons/); every .lua file under each is checked, and a global defined
    in any of them counts as defined. A folder that does not exist is skipped.
    truth_root is where tools/vendor and data/ are read from (default
    project_root); a git worktree passes its main checkout.
    """
    if isinstance(source_dirs, str):
        raise TypeError("source_dirs must be a list of folders")
    truth = load_truth(truth_root or project_root)
    code = 0
    if not truth.globals_source:
        out.write("FAILED: no global API list. Expected data/api-inventory.json "
                  "or tools/vendor/BlizzardInterfaceResources.\n")
        code = 2
    if not truth.events_source:
        out.write("FAILED: no event list. Expected tools/vendor/wow-ui-source "
                  "or tools/vendor/BlizzardInterfaceResources.\n")
        code = 2
    if code:
        return code
    out.write("globals: %d names from %s\n" % (len(truth.globals),
                                              truth.globals_source))
    out.write("events: %d names from %s\n" % (len(truth.events),
                                             truth.events_source))
    texts = {}
    project_defined = set()
    paths = []
    for d in source_dirs:
        paths.extend(lua_files(d))
    for p in paths:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            texts[p] = f.read()
        code_only = _self_alias_blanked(strip_lua(texts[p]))
        # Global definitions anywhere in the project (function Name(),
        # Name = ..., _G.Name = ...). `local function` lines also match the
        # first pattern; that is slightly permissive and accepted.
        for rx in (DEF_RES[0], DEF_RES[2], DEF_RES[3]):
            project_defined.update(rx.findall(code_only))
    findings = []
    for p, text in texts.items():
        rel = os.path.relpath(p, project_root)
        findings.extend(scan_text(rel, text, truth, project_defined))
    out.write("checked %d files, %d finding(s)\n" % (len(texts), len(findings)))
    for f in findings:
        out.write("%s\n" % f)
    return 1 if findings else 0
