"""Byte-safe helpers for the Umineko pt-BR port.

The Catbox ``script.rb`` is *not* a plain UTF-8 text file: it is a Ruby source
file with raw UTF-8 bytes, embedded NUL bytes and single-quoted string
literals.  Every operation here deliberately works on bytes until a value is
known to be a decoded string, and never normalises unrelated bytes.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Files and hashes
# ---------------------------------------------------------------------------


def read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path) -> str:
    return sha256(read_bytes(path))


def atomic_write(path: str | Path, data: bytes) -> None:
    """Write bytes via a temporary file in the same directory, then rename."""
    target = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".umineko-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Ruby single-quoted string literals
# ---------------------------------------------------------------------------


def ruby_unescape(raw: bytes) -> bytes:
    """Decode a Ruby single-quoted literal body (without the quotes).

    Ruby only recognises ``\\'`` and ``\\\\`` inside single quotes.  Every other
    backslash sequence is literal, which is why a generic JSON/Python unescape
    would corrupt the file.
    """
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == 0x5C and i + 1 < len(raw) and raw[i + 1] in (0x27, 0x5C):
            out.append(raw[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return bytes(out)


def ruby_escape(data: bytes) -> bytes:
    """Encode arbitrary bytes as the body of a Ruby single-quoted literal."""
    return data.replace(b"\\", b"\\\\").replace(b"'", b"\\'")


# ---------------------------------------------------------------------------
# 0x86 instruction scanner
# ---------------------------------------------------------------------------

# Matches ``s.ins 0x86, ushort(N), byte(x), byte(y), '...'`` and the
# ``s.layout('...')`` variant.  The string body tolerates escaped quotes and
# backslashes.  Anchored to the start of a line by the caller where relevant.
INS86_RE = re.compile(
    rb"s\.ins 0x86, ushort\((\d+)\), byte\(([^)]*)\), byte\(([^)]*)\), "
    rb"((?:s\.layout\()?'(?:[^'\\]|\\.)*'\)?)"
)


@dataclass(frozen=True)
class Message:
    """One 0x86 instruction from a script.rb file."""

    ordinal: int          # index in the 0x86 sequence (stable identity)
    ushort: int           # the instruction's own (non-unique) ushort argument
    byte_a: bytes
    byte_b: bytes
    line: int             # 1-based physical line in the source file
    start: int            # byte offset of the whole statement
    end: int              # byte offset just after the statement
    raw_literal: bytes    # exact bytes of the string expression
    text: bytes           # decoded string bytes (Ruby unescaped)
    wrapped: bool         # True when s.layout('...') is used


def scan_messages(data: bytes, source_label: str = "") -> list[Message]:
    """Scan all 0x86 instructions without touching any other file region."""
    result: list[Message] = []
    line = 1
    scanned = 0
    for match in INS86_RE.finditer(data):
        literal = match.group(4)
        wrapped = literal.startswith(b"s.layout(")
        body = literal[len(b"s.layout(") : -1] if wrapped else literal
        # body includes the surrounding single quotes
        text = ruby_unescape(body[1:-1])
        start = match.start()
        line += data.count(b"\n", scanned, start)
        scanned = start
        result.append(
            Message(
                ordinal=len(result),
                ushort=int(match.group(1)),
                byte_a=match.group(2),
                byte_b=match.group(3),
                line=line,
                start=start,
                end=match.end(),
                raw_literal=literal,
                text=text,
                wrapped=wrapped,
            )
        )
    return result


# ---------------------------------------------------------------------------
# @-markup scanner
# ---------------------------------------------------------------------------

BARE_TAGS = {"r", "k", "e", "t", "y", "|"}
BEHAVIORAL = {"v", "w", "k", "e", "t", "y", "|", "r"}


@dataclass(frozen=True)
class Token:
    kind: str      # "text" | "bare" | "arg" | "group"
    value: str     # literal text (text) or full tag source (others)
    marker: str = ""


def tokenize_markup(text: str) -> list[Token]:
    """Split a message into literal text and typed @-tags.

    Unknown tags are preserved as ``arg`` tokens; they are never dropped.
    """
    tokens: list[Token] = []
    literal: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "@":
            literal.append(text[i])
            i += 1
            continue
        if literal:
            tokens.append(Token("text", "".join(literal)))
            literal = []
        j = i + 1
        if j >= n:
            tokens.append(Token("text", "@"))
            break
        ch = text[j]
        if ch in BARE_TAGS:
            tokens.append(Token("bare", "@" + ch, ch))
            i = j + 1
            continue
        if ch in "{[<":
            close = {"{": "@}", "[": "@]", "<": "@>"}[ch]
            end = text.find(close, j + 1)
            if end == -1:
                # Unbalanced group: keep the rest verbatim instead of deleting it.
                tokens.append(Token("text", text[i:]))
                break
            tag = text[i : end + len(close)]
            tokens.append(Token("group", tag, ch))
            i = end + len(close)
            continue
        # Argument tag: read through the terminating '.'.
        end = text.find(".", j)
        if end == -1:
            tokens.append(Token("text", text[i:]))
            break
        tag = text[i : end + 1]
        marker = re.match(r"@([A-Za-z|])", tag)
        tokens.append(Token("arg", tag, marker.group(1) if marker else ""))
        i = end + 1
    if literal:
        tokens.append(Token("text", "".join(literal)))
    return tokens


def tag_skeleton(text: str, behavioral_only: bool = False) -> list[str]:
    """Return the ordered tag sequence used to validate a substitution."""
    result = []
    for token in tokenize_markup(text):
        if token.kind == "text":
            continue
        if behavioral_only and token.marker not in BEHAVIORAL:
            continue
        result.append(token.value)
    return result


def visible_text(text: str) -> str:
    return "".join(t.value for t in tokenize_markup(text) if t.kind == "text")


def voice_ids(text: str) -> list[str]:
    return [
        t.value[1:-1]
        for t in tokenize_markup(text)
        if t.kind == "arg" and t.marker == "v"
    ]


# ---------------------------------------------------------------------------
# Normalisation used for matching only (never for rewriting translation text)
# ---------------------------------------------------------------------------

_FURIGANA_RE = re.compile(r"@b[^.]*\.@<([^@]*)@>")


def flatten_furigana(text: str) -> str:
    return _FURIGANA_RE.sub(lambda m: m.group(1), text)


def normalize_match(text: str, strip_tags: bool = True, nfkc: bool = True) -> str:
    """Normalise a string for *comparison only*."""
    text = flatten_furigana(text)
    if strip_tags:
        text = visible_text(text)
    text = text.replace("\u3000", " ")
    if nfkc:
        text = unicodedata.normalize("NFKC", text)
    # The snapshots contain private-use/icon glyphs (e.g. U+E110) that are
    # presentation markers, not dialogue.  Drop them for comparison only.
    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) not in {"Co", "Cf"}
    )
    return re.sub(r"\s+", " ", text).strip()


PURE_BRACE_COMMANDS = {"0", "n", "t", "s", "a:c:"}


def onscripter_commands(line: str) -> list[str]:
    """Return the raw brace commands of a Florescer line (for review only)."""
    return re.findall(r"\{[^{}\n]*\}", line)


def onscripter_clean(line: str) -> str:
    """Extract visible text from a Florescer ``jp``/``pt`` source line.

    ONScripter inline commands such as ``{i:text}``, ``{f:5:text}`` and
    ``{ruby:top:bottom}`` contain *visible* dialogue in their last field.
    Dropping the whole command would silently delete translated words, so the
    visible field is kept and only the command wrapper is removed.  Pure
    commands (``{0}``, ``{n}``, ``{t}``) are removed.
    """
    s = line.strip()
    if s.startswith("`"):
        s = s[1:]
    s = s.rstrip("`").strip()
    s = s.replace("{a:c:", "")
    # Resolve innermost braces first; Florescer contains nested commands.
    for _ in range(8):
        nxt = re.sub(
            r"\{([^{}]*)\}",
            lambda m: brace_visible(m.group(1)),
            s,
        )
        if nxt == s:
            break
        s = nxt
    s = s.rstrip("}").strip()
    # ONScripter's @^ is a line-break marker; the Switch grammar uses @r.
    s = s.replace("@^", "@r")
    s = s.rstrip("\\").strip()
    s = s.lstrip("\u3000")
    return s


def brace_visible(inner: str) -> str:
    """Return the visible field of a single (non-nested) brace command."""
    if inner in PURE_BRACE_COMMANDS or ":" not in inner:
        return ""
    field = inner.rsplit(":", 1)[-1]
    field = field.strip()
    if len(field) >= 2 and field[0] == field[-1] and field[0] in "\"'":
        field = field[1:-1]
    return field


def normalize_source_line(text: str) -> str:
    """Normalise a Florescer ONScripter line for comparison with an SNR message.

    ONScripter source lines use ``@`` as a line separator and a trailing ``\\``
    as a continuation marker, neither of which exists in the Switch message.
    """
    s = onscripter_clean(text)
    s = s.rstrip("\\").strip()
    s = re.sub(r"@(?![A-Za-z{|<])", " ", s)
    return normalize_match(s)


def decode_utf8(data: bytes) -> str:
    return data.decode("utf-8")


def decode_utf8_latin1(data: bytes) -> str:
    """Decode UTF-8 when possible, else Latin-1; used for inventory only."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def chunk(iterable: Iterable, size: int) -> Iterable[list]:
    batch: list = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
