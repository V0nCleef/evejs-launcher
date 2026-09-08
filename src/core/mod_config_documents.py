"""Lossless key edits for JSON, INI and a conservative YAML subset.

Unchanged bytes, BOMs, newline styles and surrounding comments survive. YAML
supports space-indented block mappings with scalar or JSON-compatible flow
values. Block scalars/sequences, aliases, anchors, tags, merge keys and ambiguous
implicit scalars are not editable; unrelated opaque branches remain untouched.
This module does not reserialize a whole YAML document or infer an ANSI codec.
UTF-8 and BOM-marked UTF-16 are detected; callers can explicitly select cp1252.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
import binascii
import json
import re


class DocumentError(ValueError):
    """An edit cannot be represented without guessing at the document syntax."""


@dataclass(frozen=True)
class DocumentValue:
    present: bool
    value: object = None
    raw: str | None = None


@dataclass
class _Node:
    start: int
    end: int
    value_start: int
    value_end: int
    value: object = None
    children: dict[str, "_Node"] = field(default_factory=dict)
    mapping: bool = False
    unsupported: bool = False
    indent: int = -2


def _reject_constant(value: str):
    raise DocumentError(f"Non-finite JSON number is not supported: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DocumentError(f"Duplicate configuration key: {key}")
        result[key] = value
    return result


_JSON = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_reject_constant)
_YAML_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_CODECS = {"utf-8", "utf-16-le", "utf-16-be", "cp1252"}


def canonical_key(format: str, key: tuple[str, ...]) -> tuple[str, ...]:
    if format == "text":
        if (not isinstance(key, (tuple, list)) or len(key) != 2 or key[0] == key[1]
                or any(not isinstance(part, str) or not part or len(part) > 128 or "\0" in part for part in key)):
            raise DocumentError("A text region requires two distinct, bounded anchors.")
        return tuple(key)
    if format == "file":
        if not isinstance(key, (tuple, list)) or tuple(key) != ("$content",):
            raise DocumentError("A whole-file contribution uses the $content key.")
        return ("$content",)
    if format not in {"json", "ini", "yaml"}:
        raise DocumentError("Supported configuration formats are json, ini and yaml.")
    if not isinstance(key, (tuple, list)):
        raise DocumentError("A configuration key must be a tuple/list of components.")
    key = tuple(key)
    if not key or len(key) > 32 or any(
        not isinstance(part, str) or not part or any(c in part for c in "\x00\r\n")
        for part in key
    ):
        raise DocumentError("A configuration key must contain non-empty string components.")
    if format == "ini":
        if len(key) > 2 or any(any(c in part for c in "[]=:;") for part in key):
            raise DocumentError("INI keys are (key,) or (section, key).")
        return tuple(part.casefold() for part in key)
    return key


def values_equal(left: DocumentValue, right: DocumentValue) -> bool:
    """Compare values without treating JSON true and 1 as interchangeable."""
    if left.present != right.present:
        return False
    if not left.present:
        return True
    return json.dumps(left.value, sort_keys=True, allow_nan=False) == json.dumps(
        right.value, sort_keys=True, allow_nan=False,
    )


def _comment_start(value: str, markers: str) -> int:
    quote = ""
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif char in "\"'":
            quote = "" if quote == char else (char if not quote else quote)
        elif not quote and char in markers and (index == 0 or value[index - 1].isspace()):
            return index
    return len(value)


def _yaml_scalar(raw: str) -> object:
    if not raw:
        raise DocumentError("A YAML block mapping is not a scalar setting.")
    if raw.startswith("'") and raw.endswith("'"):
        interior = raw[1:-1]
        if "'" in interior.replace("''", ""):
            raise DocumentError("Unsupported YAML quoting.")
        return interior.replace("''", "'")
    if raw in {"null", "Null", "NULL", "~"}:
        return None
    if raw.casefold() in {"true", "false"}:
        return raw.casefold() == "true"
    if raw[0] in '"[{' or raw[0] in "-+0123456789":
        try:
            value, end = _JSON.raw_decode(raw)
            if end != len(raw):
                raise ValueError
            return value
        except (ValueError, DocumentError) as exc:
            raise DocumentError("This YAML value needs an explicit supported representation.") from exc
    if raw[0] in "&*!|>@`%" or ": " in raw or raw.casefold() in {"yes", "no", "on", "off", "y", "n", ".nan", ".inf"}:
        raise DocumentError("Unsupported or ambiguous YAML scalar; quote a literal string.")
    return raw


class _Document:
    def __init__(self, content: bytes | None, format: str, encoding: str | None):
        canonical_key(format, ("begin", "end") if format == "text" else ("check",))
        self.format = format
        self.exists = content is not None
        self.original = content or b""
        content = self.original
        self.bom = b""
        detected = None
        if content.startswith((b"\xff\xfe\0\0", b"\0\0\xfe\xff")):
            raise DocumentError("UTF-32 configuration documents are not supported.")
        for bom, codec in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
            if content.startswith(bom):
                self.bom, detected = bom, codec
                content = content[len(bom):]
                break
        self.encoding = encoding or detected or "utf-8"
        if self.encoding not in _CODECS or (detected and self.encoding != detected):
            raise DocumentError("Unsupported encoding or a conflicting byte-order mark.")
        try:
            self.text = content.decode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise DocumentError("Configuration encoding is not valid; specify its actual encoding.") from exc
        if "\0" in self.text:
            raise DocumentError("Binary configuration documents are not supported.")
        self.newline = "\r\n" if "\r\n" in self.text else "\n"
        self.sections: dict[str, _Node] = {}
        self.root = _Node(0, 0, 0, 0) if format == "text" else self._json_tree() if format == "json" else self._line_tree()

    def encode(self, text: str) -> bytes:
        try:
            return self.bom + text.encode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise DocumentError("The new value cannot be represented in the document encoding.") from exc

    def _json_tree(self) -> _Node:
        if not self.text.strip():
            if self.exists:
                raise DocumentError("An existing JSON document is empty.")
            self.text = "{}"
        try:
            parsed = _JSON.decode(self.text)
        except ValueError as exc:
            raise DocumentError(f"Invalid JSON configuration: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DocumentError("The JSON configuration root must be an object.")

        def whitespace(position):
            while position < len(self.text) and self.text[position].isspace():
                position += 1
            return position

        def node(position):
            start = whitespace(position)
            value, end = _JSON.raw_decode(self.text, start)
            current = _Node(start, end, start, end, value, mapping=isinstance(value, dict))
            if current.mapping:
                position = whitespace(start + 1)
                while self.text[position] != "}":
                    key_start = position
                    key, position = _JSON.raw_decode(self.text, position)
                    position = whitespace(position) + 1
                    child = node(position)
                    child.start = key_start
                    current.children[key] = child
                    position = whitespace(child.value_end)
                    if self.text[position] == ",":
                        position = whitespace(position + 1)
            return current
        return node(0)

    def _line_tree(self) -> _Node:
        root = _Node(0, len(self.text), 0, len(self.text), mapping=True)
        lines = self.text.splitlines(keepends=True)
        offset = 0
        section = root
        stack = [root]
        opaque_indent = None
        previous = None
        document_markers = 0
        for line in lines:
            body = line.rstrip("\r\n")
            stripped = body.strip()
            start, offset = offset, offset + len(line)
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if self.format == "ini":
                header = re.fullmatch(r"\s*\[([^\]]+)\]\s*(?:[;#].*)?", body)
                if header:
                    name = header[1].casefold()
                    if name in self.sections:
                        raise DocumentError(f"Duplicate INI section: {name}")
                    section.end = start
                    section = _Node(start, len(self.text), offset, offset, mapping=True)
                    self.sections[name] = section
                    previous = None
                    continue
                match = re.fullmatch(r"([ \t]*)([^=:]+?)([ \t]*[=:][ \t]*)(.*)", body)
                if not match:
                    if previous is not None and body[:1].isspace():
                        previous.unsupported = True
                    continue
                key = match[2].strip().casefold()
                raw_start = start + match.start(4)
                tail = match[4]
                raw = tail[:_comment_start(tail, "#;")].rstrip()
                if key in section.children:
                    raise DocumentError(f"Duplicate INI key: {key}")
                previous = _Node(start, offset, raw_start, raw_start + len(raw), raw)
                section.children[key] = previous
                continue

            indent = len(body) - len(body.lstrip(" "))
            if "\t" in body[:len(body) - len(body.lstrip())]:
                raise DocumentError("YAML indentation must use spaces.")
            if stripped == "---":
                document_markers += 1
                if document_markers > 1 or root.children:
                    raise DocumentError("Multiple YAML documents are not supported.")
                continue
            if stripped == "...":
                raise DocumentError("Explicit YAML document end markers are not supported.")
            if opaque_indent is not None and indent > opaque_indent:
                continue
            opaque_indent = None
            while len(stack) > 1 and indent <= stack[-1].indent:
                stack.pop()
            parent = stack[-1]
            if parent.children and indent != next(iter(parent.children.values())).indent:
                if previous is not None and not previous.mapping and indent > previous.indent:
                    # A continuation may itself look like a mapping. Do not
                    # invent a sibling key out of multiline/invalid YAML.
                    previous.unsupported = True
                    opaque_indent = previous.indent
                    continue
                raise DocumentError("Inconsistent YAML mapping indentation is not supported.")
            if stripped.startswith(("- ", "? ")):
                parent.unsupported = True
                opaque_indent = parent.indent
                continue
            match = re.match(r'(?P<key>"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_.-]*)\s*:(?:[ \t]+|$)', stripped)
            if not match:
                if previous is not None and indent > previous.indent:
                    previous.unsupported = True
                    opaque_indent = previous.indent
                    continue
                raise DocumentError("Unsupported YAML mapping syntax.")
            token = match["key"]
            key = json.loads(token) if token.startswith('"') else token
            if key == "<<":
                raise DocumentError("YAML merge keys are not supported.")
            if key in parent.children:
                raise DocumentError(f"Duplicate YAML key: {key}")
            raw_start = start + indent + match.end()
            tail = body[indent + match.end():]
            raw = tail[:_comment_start(tail, "#")].rstrip()
            child = _Node(start, offset, raw_start, raw_start + len(raw), indent=indent)
            if raw:
                try:
                    child.value = _yaml_scalar(raw)
                except DocumentError:
                    child.unsupported = True
                    opaque_indent = indent
            else:
                child.mapping = True
                stack.append(child)
            parent.children[key] = child
            previous = child

        def set_ends(parent):
            children = list(parent.children.values())
            for index, child in enumerate(children):
                if child.mapping:
                    child.end = children[index + 1].start if index + 1 < len(children) else parent.end
                    set_ends(child)
        if self.format == "yaml":
            set_ends(root)
        return root

    def find(self, key: tuple[str, ...]) -> tuple[_Node, _Node | None, int]:
        parent = self.root
        if self.format == "ini" and len(key) == 2:
            parent = self.sections.get(key[0])
            if parent is None:
                return self.root, None, 0
            child = parent.children.get(key[1])
            if child is not None and child.unsupported:
                raise DocumentError("Multiline INI values are not editable.")
            return parent, child, 1
        for index, part in enumerate(key):
            if parent.unsupported or not parent.mapping:
                raise DocumentError("The requested key passes through an unsupported value.")
            child = parent.children.get(part)
            if child is None:
                return parent, None, index
            if index == len(key) - 1:
                if child.unsupported or (self.format == "yaml" and child.mapping and child.children):
                    raise DocumentError("The requested YAML/INI value is not an editable scalar or flow value.")
                return parent, child, index
            parent = child
        raise DocumentError("A key is required.")


def _region_positions(text: str, key: tuple[str, ...]) -> tuple[int, int, int, int]:
    left, right = canonical_key("text", key)
    if text.count(left) != 1 or text.count(right) != 1:
        raise DocumentError("Text region anchors must each occur exactly once.")
    begin, end = text.index(left), text.index(right)
    if begin + len(left) > end:
        raise DocumentError("Text region anchors are out of order or overlap.")
    return begin, begin + len(left), end, end + len(right)


def text_regions_overlap(content: bytes | None, first: tuple[str, ...], second: tuple[str, ...], *, encoding: str | None = None) -> bool:
    document = _Document(content, "text", encoding)
    a, b = _region_positions(document.text, first), _region_positions(document.text, second)
    def intersects(body, full):
        return full[0] < body[0] < full[1] if body[0] == body[1] else max(body[0], full[0]) < min(body[1], full[1])
    return intersects(a[1:3], (b[0], b[3])) or intersects(b[1:3], (a[0], a[3]))


def read_value(content: bytes | None, format: str, key: tuple[str, ...], *, encoding: str | None = None) -> DocumentValue:
    key = canonical_key(format, key)
    if format == "file":
        return DocumentValue(False) if content is None else DocumentValue(True, base64.b64encode(content).decode("ascii"))
    document = _Document(content, format, encoding)
    if format == "text":
        _begin, start, end, _finish = _region_positions(document.text, key)
        return DocumentValue(True, document.text[start:end])
    _parent, child, _index = document.find(key)
    if child is None:
        return DocumentValue(False)
    return DocumentValue(True, child.value, document.text[child.value_start:child.value_end])


def edit_value(content: bytes | None, format: str, key: tuple[str, ...], value: object = None, *, delete: bool = False, encoding: str | None = None, raw_value: str | None = None) -> bytes | None:
    requested_key = tuple(key)
    key = canonical_key(format, key)
    if format == "file":
        if delete:
            return None
        if not isinstance(value, str):
            raise DocumentError("A whole-file contribution requires encoded bytes.")
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DocumentError("Invalid whole-file content encoding.") from exc
    document = _Document(content, format, encoding)
    if format == "text":
        _begin, start, end, _finish = _region_positions(document.text, key)
        value = "" if delete else value
        if not isinstance(value, str) or "\0" in value or any(anchor in value for anchor in key):
            raise DocumentError("A text replacement must preserve its unique anchors.")
        return document.encode(document.text[:start] + value + document.text[end:])
    parent, child, index = document.find(key)
    text = document.text
    if delete:
        if child is None:
            return document.original
        if format == "json":
            siblings = list(parent.children.values())
            position = siblings.index(child)
            if position + 1 < len(siblings):
                start, end = child.start, siblings[position + 1].start
            elif position:
                start, end = siblings[position - 1].value_end, child.value_end
            else:
                start, end = child.start, child.value_end
        else:
            start, end = child.start, child.end
        return document.encode(text[:start] + text[end:])

    try:
        rendered = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise DocumentError("The setting is not a finite JSON-compatible value.") from exc
    if format == "ini":
        if isinstance(value, (dict, list)) or any(c in str(value) for c in "\r\n\0"):
            raise DocumentError("INI values must be single-line scalars.")
        rendered = value if isinstance(value, str) else ("" if value is None else rendered)
        if _comment_start(rendered, "#;") != len(rendered) or rendered != rendered.strip():
            raise DocumentError("This INI value would be parsed as a comment or lose whitespace.")
        normalized = rendered
    else:
        normalized = value
    if child is not None and values_equal(DocumentValue(True, child.value), DocumentValue(True, normalized)):
        return document.original
    if raw_value is not None:
        parsed = raw_value if format == "ini" else (_yaml_scalar(raw_value) if format == "yaml" else _JSON.decode(raw_value))
        if not values_equal(DocumentValue(True, parsed), DocumentValue(True, normalized)):
            raise DocumentError("The requested original representation does not match its value.")
        rendered = raw_value
    if child is not None:
        return document.encode(text[:child.value_start] + rendered + text[child.value_end:])

    remaining = key[index:]
    if format == "json":
        nested = value
        for part in reversed(remaining[1:]):
            nested = {part: nested}
        member = json.dumps(remaining[0], ensure_ascii=False) + ": " + json.dumps(nested, ensure_ascii=False, allow_nan=False)
        children = list(parent.children.values())
        if children:
            position = children[-1].value_end
            first = children[0].start
            line_start = text.rfind("\n", 0, first) + 1
            indent = text[line_start:first]
            separator = document.newline + indent if indent.isspace() else " "
            member = "," + separator + member
        else:
            position = parent.value_end - 1
        return document.encode(text[:position] + member + text[position:])

    if format == "ini":
        remaining = requested_key[index:]
        position = len(text) if len(remaining) == 2 else parent.end
        addition = ""
        if len(remaining) == 2:
            addition += f"[{remaining[0]}]{document.newline}"
        addition += f"{remaining[-1]}={rendered}{document.newline}"
    else:
        position = parent.end
        indent = next(iter(parent.children.values())).indent if parent.children else parent.indent + 2
        lines = []
        for depth, part in enumerate(remaining):
            token = part if _YAML_KEY.fullmatch(part) else json.dumps(part, ensure_ascii=False)
            suffix = " " + rendered if depth == len(remaining) - 1 else ""
            lines.append(" " * (indent + depth * 2) + token + ":" + suffix + document.newline)
        addition = "".join(lines)
    if position and not text[:position].endswith(("\n", "\r")):
        addition = document.newline + addition
    result = document.encode(text[:position] + addition + text[position:])
    # An insertion must remain discoverable by exactly the same parser.
    observed = read_value(result, format, key, encoding=encoding)
    if not values_equal(observed, DocumentValue(True, normalized)):
        raise DocumentError("The requested key cannot be inserted unambiguously.")
    return result
