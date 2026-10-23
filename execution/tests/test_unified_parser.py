import pytest

from execution.patches.unified import (
    PatchParseError,
    parse_unified_diff,
    reverse_patch,
)

MODIFY = """\
diff --git a/calc.py b/calc.py
index 111..222 100644
--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,3 @@
 def divide(a, b):
-    return a * b
+    return a / b
 # end
"""


def test_parses_git_style_modify():
    files = parse_unified_diff(MODIFY)

    assert len(files) == 1
    patch = files[0]
    assert patch.path == "calc.py"
    assert not patch.is_create and not patch.is_delete
    hunk = patch.hunks[0]
    assert (hunk.old_start, hunk.old_count) == (1, 3)
    assert [(l.op, l.text) for l in hunk.lines] == [
        (" ", "def divide(a, b):"),
        ("-", "    return a * b"),
        ("+", "    return a / b"),
        (" ", "# end"),
    ]


def test_parses_plain_diff_without_prefixes():
    text = "--- calc.py\n+++ calc.py\n@@ -1 +1 @@\n-x\n+y\n"

    assert parse_unified_diff(text)[0].path == "calc.py"


def test_keeps_a_directory_literally_named_a():
    text = "--- a/x.py\n+++ a/x.py\n@@ -1 +1 @@\n-x\n+y\n"

    assert parse_unified_diff(text)[0].path == "a/x.py"


def test_parses_create_and_delete():
    text = (
        "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+one\n+two\n"
        "--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
    )

    created, deleted = parse_unified_diff(text)

    assert created.is_create and created.path == "new.txt"
    assert deleted.is_delete and deleted.path == "old.txt"


def test_parses_multiple_files_and_hunks():
    text = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-1\n+one\n"
        "@@ -5 +5 @@\n-5\n+five\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-x\n+y\n"
    )

    files = parse_unified_diff(text)

    assert [f.path for f in files] == ["a.txt", "b.txt"]
    assert len(files[0].hunks) == 2


def test_no_newline_marker_mid_hunk_and_trailing():
    text = (
        "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-last\n"
        "\\ No newline at end of file\n+last\n"
    )

    lines = parse_unified_diff(text)[0].hunks[0].lines

    assert lines[0].no_newline is True
    assert lines[1].no_newline is False


def test_tolerates_markdown_code_fence_and_crlf():
    text = "```diff\r\n" + MODIFY.replace("\n", "\r\n") + "```\r\n"

    assert parse_unified_diff(text)[0].path == "calc.py"


def test_default_hunk_counts_are_one():
    text = "--- a/f\n+++ b/f\n@@ -3 +3 @@\n-a\n+b\n"

    hunk = parse_unified_diff(text)[0].hunks[0]

    assert (hunk.old_count, hunk.new_count) == (1, 1)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "no file changes"),
        ("just some prose\n", "unexpected content"),
        ("--- a/f\n+++ b/f\n", "no hunks"),
        ("--- a/f\n+++ b/f\n@@ -1,3 +1,2 @@\n-a\n+b\n", "ends early"),
        ("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n+c\n x\n", "unexpected"),
        ("--- a/f\n+++ b/g\n@@ -1 +1 @@\n-a\n+b\n", "renames"),
        ("rename from a\nrename to b\n", "renames"),
        ("old mode 100644\nnew mode 100755\n", "mode"),
        ("Binary files a/x and b/x differ\n", "binary"),
        ('--- "a/x y"\n+++ "b/x y"\n@@ -1 +1 @@\n-a\n+b\n', "quoted"),
        ("--- /dev/null\n+++ b/f\n@@ -0,1 +1 @@\n-bad\n+x\n", "only contain"),
        ("--- a/f\n+++ /dev/null\n@@ -1 +1 @@\n+bad\n-x\n", "only contain"),
        (
            "--- a/f\n+++ b/f\n@@ -5 +5 @@\n-a\n+b\n@@ -1 +1 @@\n-c\n+d\n",
            "out of order",
        ),
    ],
)
def test_rejects_malformed_or_unsupported(text, fragment):
    with pytest.raises(PatchParseError) as info:
        parse_unified_diff(text)

    assert fragment in str(info.value)


def test_reverse_swaps_direction():
    reversed_files = reverse_patch(parse_unified_diff(MODIFY))
    hunk = reversed_files[0].hunks[0]

    assert [(l.op, l.text) for l in hunk.lines] == [
        (" ", "def divide(a, b):"),
        ("+", "    return a * b"),
        ("-", "    return a / b"),
        (" ", "# end"),
    ]


def test_reverse_of_create_is_delete():
    text = "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+one\n"

    reversed_file = reverse_patch(parse_unified_diff(text))[0]

    assert reversed_file.is_delete
    assert reversed_file.path == "new.txt"


def test_pads_trimmed_trailing_blank_context_lines():
    text = "--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n-a\n+b\n c\n"

    hunk = parse_unified_diff(text)[0].hunks[0]

    assert len(hunk.lines) == 4
    assert hunk.lines[-1].op == " "
    assert hunk.lines[-1].text == ""
