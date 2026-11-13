"""Parser for World of Warcraft SavedVariables files.

A SavedVariables file is a sequence of `Name = value` statements whose values
are Lua literals (nil, booleans, numbers, strings, table constructors).

parse_savedvariables(text) -> dict mapping each top-level name to a Python
value. Conversion rules:
  - nil -> None, true/false -> True/False, numbers -> int or float,
    strings -> str (escape bytes decoded as UTF-8, invalid bytes replaced).
  - a table whose keys are exactly the integers 1..n (n >= 1) -> list;
  - an empty table -> {};
  - any other table -> dict with keys converted to strings
    (5 -> "5", true -> "true"), insertion order preserved.
  - inf / -inf / nan become float('inf') etc.; jsonable() turns them into
    the strings "inf" / "-inf" / "nan" for JSON output.

The parser is iterative (explicit stack), so nesting depth is limited only by
memory. Errors raise LuaSVParseError carrying a 1-based line and column.
"""

import math
import re

__all__ = ["LuaSVParseError", "parse_savedvariables", "jsonable"]


class LuaSVParseError(ValueError):
    def __init__(self, message, line, column):
        super().__init__("line %d, column %d: %s" % (line, column, message))
        self.message = message
        self.line = line
        self.column = column


# Whitespace, `--[[ long ]]` comments and `--` line comments.
_SKIP_RE = re.compile(r"(?:\s+|--\[(=*)\[.*?\]\1\]|--[^\n]*)*", re.S)
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM_RE = re.compile(
    r"0[xX][0-9a-fA-F]+|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
_DQ_RE = re.compile(r'"((?:[^"\\\n]|\\(?:\r\n|\n\r|[\s\S]))*)"')
_SQ_RE = re.compile(r"'((?:[^'\\\n]|\\(?:\r\n|\n\r|[\s\S]))*)'")
_ESC_RE = re.compile(
    r"\\([0-9]{1,3}|x[0-9a-fA-F]{2}|z\s*|\r\n|\n\r|[\s\S])"
)
# `Name =` (but not `Name ==`) at a table field start.
_FIELD_NAME_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\s+|--[^\n]*)*=(?!=)"
)

_SIMPLE_ESC = {
    "n": "\n", "r": "\r", "t": "\t", "a": "\a", "b": "\b", "f": "\f",
    "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n", "\r": "\n",
    "\r\n": "\n", "\n\r": "\n",
}

_INF = float("inf")
_NAN = float("nan")
_WORDS = {"true": True, "false": False, "nil": None, "inf": _INF,
          "nan": _NAN}

# Sentinel meaning "the next value is positional".
_POSITIONAL = object()


def _unescape(raw):
    if "\\" not in raw:
        return raw
    # Escapes may produce arbitrary bytes (\ddd, \xhh) that form UTF-8
    # sequences together, so assemble bytes and decode once.
    parts = []
    last = 0
    has_bytes = False
    for m in _ESC_RE.finditer(raw):
        parts.append(raw[last:m.start()].encode("utf-8"))
        e = m.group(1)
        if e[0].isdigit():
            v = int(e)
            if v > 255:
                raise ValueError("decimal escape too large: \\" + e)
            parts.append(bytes([v]))
            has_bytes = has_bytes or v > 127
        elif e[0] == "x" and len(e) == 3:
            v = int(e[1:], 16)
            parts.append(bytes([v]))
            has_bytes = has_bytes or v > 127
        elif e[0] == "z":
            pass
        elif e in _SIMPLE_ESC:
            parts.append(_SIMPLE_ESC[e].encode("utf-8"))
        else:
            raise ValueError("invalid escape sequence: \\" + e)
        last = m.end()
    parts.append(raw[last:].encode("utf-8"))
    return b"".join(parts).decode("utf-8", errors="replace")


def _divide(a, b):
    if b == 0:
        if a == 0 or (isinstance(a, float) and math.isnan(a)):
            return _NAN
        return _INF if a > 0 else -_INF
    return a / b


class _Table(object):
    __slots__ = ("items", "next_index", "pending")

    def __init__(self):
        self.items = {}       # tagged key -> (python key, value)
        self.next_index = 1
        self.pending = None


def _tag(key):
    # Python treats True == 1 and 1.0 == 1 as the same dict key; Lua keeps
    # booleans distinct from numbers but unifies 1 and 1.0.
    if isinstance(key, bool):
        return ("b", key)
    if isinstance(key, float) and key.is_integer():
        return ("n", int(key))
    if isinstance(key, (int, float)):
        return ("n", key)
    return ("s", key)


def _key_str(key):
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, float):
        if math.isnan(key):
            return "nan"
        if math.isinf(key):
            return "inf" if key > 0 else "-inf"
        if key.is_integer():
            return str(int(key))
        return repr(key)
    return str(key)


def _finish(table):
    items = table.items
    n = len(items)
    if n == 0:
        return {}
    is_list = True
    expected = 1
    in_order = True
    for tag in items:
        if tag[0] != "n" or type(tag[1]) is not int or not 1 <= tag[1] <= n:
            is_list = False
            break
        if tag[1] != expected:
            in_order = False
        expected += 1
    if is_list:
        # n distinct integer keys all within 1..n means exactly 1..n.
        if in_order:
            return [v for (_k, v) in items.values()]
        return [items[("n", i)][1] for i in range(1, n + 1)]
    return {_key_str(k): v for (k, v) in items.values()}


class _Parser(object):
    def __init__(self, text):
        if text.startswith("\ufeff"):
            text = text[1:]
        self.text = text
        self.pos = 0
        self.n = len(text)

    # -- helpers ---------------------------------------------------------
    def error(self, message, pos=None):
        if pos is None:
            pos = self.pos
        line = self.text.count("\n", 0, pos) + 1
        col = pos - (self.text.rfind("\n", 0, pos) + 1) + 1
        raise LuaSVParseError(message, line, col)

    def skip(self):
        self.pos = _SKIP_RE.match(self.text, self.pos).end()

    def peek(self):
        if self.pos < self.n:
            return self.text[self.pos]
        return ""

    def expect(self, ch):
        self.skip()
        if self.pos >= self.n or self.text[self.pos] != ch:
            found = self.text[self.pos] if self.pos < self.n else "end of file"
            self.error("expected %r, found %r" % (ch, found))
        self.pos += 1

    # -- scalars ---------------------------------------------------------
    def number_body(self):
        """Parse an unsigned number or inf/nan word at pos."""
        text = self.text
        m = _NUM_RE.match(text, self.pos)
        if m:
            s = m.group(0)
            end = m.end()
            if end < self.n and (text[end].isalnum() or text[end] == "_"):
                self.error("malformed number", self.pos)
            self.pos = end
            if s[:2] in ("0x", "0X"):
                return int(s, 16)
            if "." in s or "e" in s or "E" in s:
                return float(s)
            return int(s)
        m = _NAME_RE.match(text, self.pos)
        if m and m.group(0) in ("inf", "nan"):
            self.pos = m.end()
            return _INF if m.group(0) == "inf" else _NAN
        self.error("expected a number")

    def signed_number(self):
        neg = False
        if self.peek() == "-":
            neg = True
            self.pos += 1
            self.skip()
        v = self.number_body()
        return -v if neg else v

    def number(self):
        v = self.signed_number()
        # Tolerate `1/0`, `-1/0`, `0/0` spellings.
        save = self.pos
        self.skip()
        if self.peek() == "/":
            self.pos += 1
            self.skip()
            d = self.signed_number()
            return _divide(v, d)
        self.pos = save
        return v

    def scalar(self):
        """Parse a non-table value at pos (after skip)."""
        text = self.text
        if self.pos >= self.n:
            self.error("unexpected end of file, expected a value")
        c = text[self.pos]
        if c == '"' or c == "'":
            m = (_DQ_RE if c == '"' else _SQ_RE).match(text, self.pos)
            if not m:
                self.error("unterminated string")
            try:
                v = _unescape(m.group(1))
            except ValueError as exc:
                self.error(str(exc))
            self.pos = m.end()
            return v
        if c.isdigit() or c == "-" or c == ".":
            return self.number()
        m = _NAME_RE.match(text, self.pos)
        if m:
            word = m.group(0)
            if word in _WORDS:
                if word in ("inf", "nan"):
                    return self.number()
                self.pos = m.end()
                return _WORDS[word]
            self.error("unexpected name %r, expected a value" % word)
        self.error("unexpected character %r, expected a value" % c)

    # -- values (iterative over tables) ----------------------------------
    def value(self):
        text = self.text
        stack = []
        while True:
            # Value position.
            self.skip()
            if self.pos < self.n and text[self.pos] == "{":
                self.pos += 1
                stack.append(_Table())
                have_value = False
                v = None
            else:
                v = self.scalar()
                have_value = True
            # Deliver finished values and read the next field start.
            while True:
                if have_value:
                    if not stack:
                        return v
                    t = stack[-1]
                    key = t.pending
                    if key is _POSITIONAL:
                        if v is not None:
                            t.items[("n", t.next_index)] = (t.next_index, v)
                        t.next_index += 1
                    else:
                        tag = _tag(key)
                        if v is None:
                            t.items.pop(tag, None)
                        else:
                            t.items[tag] = (key, v)
                    t.pending = None
                    self.skip()
                    c = text[self.pos] if self.pos < self.n else ""
                    if c == "," or c == ";":
                        self.pos += 1
                    elif c != "}":
                        self.error("expected ',' or '}' in table")
                    have_value = False
                # Field start in the innermost table.
                t = stack[-1]
                self.skip()
                if self.pos >= self.n:
                    self.error("unexpected end of file inside table")
                c = text[self.pos]
                if c == "}":
                    self.pos += 1
                    stack.pop()
                    v = _finish(t)
                    have_value = True
                    continue
                if c == "[":
                    kpos = self.pos
                    self.pos += 1
                    self.skip()
                    key = self.scalar()
                    if key is None:
                        self.error("table index is nil", kpos)
                    if isinstance(key, float) and math.isnan(key):
                        self.error("table index is NaN", kpos)
                    self.expect("]")
                    self.expect("=")
                    t.pending = key
                    break
                m = _FIELD_NAME_RE.match(text, self.pos)
                if m:
                    t.pending = m.group(1)
                    self.pos = m.end()
                    break
                t.pending = _POSITIONAL
                break

    # -- top level -------------------------------------------------------
    def parse(self):
        result = {}
        text = self.text
        while True:
            self.skip()
            if self.pos >= self.n:
                return result
            if text[self.pos] == ";":
                self.pos += 1
                continue
            m = _NAME_RE.match(text, self.pos)
            if not m:
                self.error("expected a variable name")
            name = m.group(0)
            self.pos = m.end()
            self.expect("=")
            result[name] = self.value()


def parse_savedvariables(text):
    """Parse SavedVariables source text into {name: value}."""
    return _Parser(text).parse()


def jsonable(obj):
    """Return a copy of obj with non-finite floats replaced by strings.

    Iterative, so it is safe for arbitrarily deep data.
    """
    def conv(x):
        if isinstance(x, float) and not math.isfinite(x):
            if math.isnan(x):
                return "nan"
            return "inf" if x > 0 else "-inf"
        return x

    if not isinstance(obj, (dict, list)):
        return conv(obj)
    root = {} if isinstance(obj, dict) else []
    stack = [(obj, root)]
    while stack:
        src, dst = stack.pop()
        items = src.items() if isinstance(src, dict) else enumerate(src)
        for k, v in items:
            if isinstance(v, dict):
                nv = {}
                stack.append((v, nv))
            elif isinstance(v, list):
                nv = []
                stack.append((v, nv))
            else:
                nv = conv(v)
            if isinstance(dst, dict):
                dst[k] = nv
            else:
                dst.append(nv)
    return root
