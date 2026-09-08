"""Structured edits preserve the original document outside the selected key."""
import pytest

from src.core.mod_config_documents import DocumentError, edit_value, read_value


@pytest.mark.parametrize(("format", "content", "key", "value", "expected"), [
    ("json", b'{\r\n  "a": 1, "unrelated" : [2, 3]\r\n}\r\n', ("a",), 7, b'{\r\n  "a": 7, "unrelated" : [2, 3]\r\n}\r\n'),
    ("ini", b'; header\r\n[Graphics]\r\nQuality = low  ; keep comment\r\nOther=blue\r\n', ("Graphics", "Quality"), "high", b'; header\r\n[Graphics]\r\nQuality = high  ; keep comment\r\nOther=blue\r\n'),
    ("yaml", b'# header\r\nui:\r\n  size: 2 # preserve\r\n  name: "Pilot"\r\n', ("ui", "size"), 3, b'# header\r\nui:\r\n  size: 3 # preserve\r\n  name: "Pilot"\r\n'),
])
def test_key_replacement_is_local(format, content, key, value, expected):
    assert edit_value(content, format, key, value) == expected
    assert read_value(expected, format, key).value == value


@pytest.mark.parametrize(("format", "content", "key", "value"), [
    ("json", b'{ "a" : 1.0, "s": "\\u0061" }', ("s",), "a"),
    ("ini", b'[GENERAL]\r\nValue = yes   ; comment\r\n', ("GENERAL", "Value"), "yes"),
    ("yaml", b"# comment\na: 'some text'\n", ("a",), "some text"),
])
def test_semantic_noop_preserves_exact_bytes(format, content, key, value):
    assert edit_value(content, format, key, value) == content


@pytest.mark.parametrize("codec,bom", [("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
def test_byte_order_mark_and_encoding_survive(codec, bom):
    original = bom + '[Graphics]\r\nLabel=caf\u00e9\r\nValue=1\r\n'.encode(codec)
    expected = bom + '[Graphics]\r\nLabel=caf\u00e9\r\nValue=2\r\n'.encode(codec)
    assert edit_value(original, "ini", ("Graphics", "Value"), "2") == expected


def test_explicit_legacy_encoding_is_preserved_and_unrepresentable_values_fail():
    original = "name=caf\u00e9\r\nvalue=1\r\n".encode("cp1252")
    assert edit_value(original, "ini", ("value",), "2", encoding="cp1252") == original.replace(b"value=1", b"value=2")
    with pytest.raises(DocumentError):
        edit_value(original, "ini", ("name",), "\u3042", encoding="cp1252")


@pytest.mark.parametrize("format", ["json", "ini", "yaml"])
def test_create_nested_key_and_delete_only_that_key(format):
    original = {"json": b'{"other": 4}', "ini": b'root=4\n', "yaml": b'other: 4\n'}[format]
    changed = edit_value(original, format, ("Graphics", "Quality"), "high")
    assert read_value(changed, format, ("Graphics", "Quality")).value == "high"
    restored = edit_value(changed, format, ("Graphics", "Quality"), delete=True)
    assert not read_value(restored, format, ("Graphics", "Quality")).present
    assert read_value(restored, format, ("root" if format == "ini" else "other",)).value in ("4", 4)


def test_ini_root_key_is_inserted_before_sections_and_author_case_is_kept():
    original = b'; heading\n[GENERAL]\nexisting=yes\n'
    changed = edit_value(original, "ini", ("RootSetting",), "on")
    assert changed.index(b"RootSetting=on") < changed.index(b"[GENERAL]")
    changed = edit_value(changed, "ini", ("NewSection", "NewValue"), "enabled")
    assert b"[NewSection]\nNewValue=enabled\n" in changed
    assert read_value(changed, "ini", ("RootSetting",)).value == "on"


def test_yaml_opaque_unrelated_branch_survives_and_targeted_opaque_value_is_rejected():
    original = b'description: |\n  keep: this literal text\nui:\n  names:\n    - ["Pilot"]\n  scale: 1\n'
    assert edit_value(original, "yaml", ("ui", "scale"), 2) == original.replace(b"scale: 1", b"scale: 2")
    for key in (("description",), ("ui", "names")):
        with pytest.raises(DocumentError):
            edit_value(original, "yaml", key, "new")


@pytest.mark.parametrize(("format", "content", "key"), [
    ("json", b'{"a": 1, "a": 2}', ("a",)),
    ("json", b'', ("a",)),
    ("ini", b'[S]\na=1\na=2\n', ("S", "a")),
    ("yaml", b'a: &anchor\n  b: 1\n', ("a", "b")),
    ("yaml", b'a: on\n', ("a",)),
    ("yaml", b'---\na: 1\n---\nb: 2\n', ("a",)),
])
def test_ambiguous_target_documents_are_rejected(format, content, key):
    with pytest.raises(DocumentError):
        edit_value(content, format, key, 7)


def test_yaml_indented_continuation_is_not_mistaken_for_a_sibling_key():
    original = b'ui:\n  a: text\n    continuation: text\n  unrelated: 1\n'
    with pytest.raises(DocumentError, match="editable scalar"):
        edit_value(original, "yaml", ("ui", "a"), "replacement")
    assert not read_value(original, "yaml", ("ui", "continuation")).present
    assert edit_value(original, "yaml", ("ui", "unrelated"), 2) == original.replace(b"unrelated: 1", b"unrelated: 2")


def test_yaml_inconsistent_sibling_indentation_is_rejected():
    with pytest.raises(DocumentError, match="indentation"):
        edit_value(b'ui:\n    a: 1\n  b: 2\n', "yaml", ("ui", "b"), 3)


def test_ini_new_section_does_not_mark_the_previous_sections_value_as_multiline():
    original = b'[first]\nvalue=1\n[second]\n  an opaque line\nother=2\n'
    assert edit_value(original, "ini", ("first", "value"), "3") == original.replace(b"value=1", b"value=3")
