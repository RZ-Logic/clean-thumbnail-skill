"""Packaging and docs checks: the upload zip (tools/build_zip.py), the SKILL.md frontmatter that skill
uploaders enforce, and the Markdown tables in SKILL.md and references/. These never write into the checkout.
Run: python -m unittest discover -s tests"""
import contextlib
import glob
import hashlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skill", "clean-thumbnail")
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

import thumb  # noqa: E402


def load_build_zip():
    spec = importlib.util.spec_from_file_location("build_zip", os.path.join(ROOT, "tools", "build_zip.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def frontmatter(path):
    """The YAML frontmatter of a SKILL.md as {key: value}, with nested keys as "parent.key". Handles the
    plain, quoted and folded (>, |) scalars SKILL.md files use; no PyYAML needed."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        raise AssertionError("%s has no --- frontmatter --- block at the top" % path)
    out, parent, block = {}, None, None
    for line in m.group(1).splitlines():
        if block is not None and (line.startswith("  ") or not line.strip()):
            out[block] = (out[block] + " " + line.strip()).strip()
            continue
        block = None
        km = re.match(r"^( *)([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not km:
            raise AssertionError("unparsed frontmatter line: %r" % line)
        indent, key, value = km.groups()
        key = key if not indent else "%s.%s" % (parent, key)
        if not indent:
            parent = key
        if value in (">", ">-", "|", "|-"):
            out[key], block = "", key
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def table_rows(path):
    """[(line number, cells)] per Markdown table, header first; cells split on unescaped pipes."""
    tables, cur, fence = [], [], False
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fence = not fence
        s = line.strip()
        if not fence and s.startswith("|"):
            body = s[1:-1] if s.endswith("|") and not s.endswith("\\|") else s[1:]
            cur.append((i, re.split(r"(?<!\\)\|", body)))
            continue
        if cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


class Zip(unittest.TestCase):
    """tools/build_zip.py: the upload zip is deterministic and tests build it outside the checkout."""

    def test_build_writes_where_asked_and_never_into_dist(self):
        bz = load_build_zip()
        dist = os.path.join(ROOT, "dist", "clean-thumbnail.zip")
        before = os.stat(dist).st_mtime_ns if os.path.exists(dist) else None
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "sub", "c.zip")
            path, count = bz.build(out)
            self.assertEqual(os.path.abspath(path), os.path.abspath(out))
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            self.assertEqual(len(names), count)
            self.assertEqual(os.listdir(os.path.dirname(out)), ["c.zip"])  # no temp file left behind
        self.assertEqual(os.stat(dist).st_mtime_ns if os.path.exists(dist) else None, before)
        self.assertIn("clean-thumbnail/SKILL.md", names)
        self.assertIn("clean-thumbnail/scripts/thumb.py", names)
        self.assertIn("clean-thumbnail/LICENSE", names)
        self.assertEqual(len(names), len(set(names)))  # the LICENSE once, even when the skill folder has a copy
        self.assertEqual(names, sorted(names))
        self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])

    def test_entries_have_fixed_dates_and_permissions(self):
        bz = load_build_zip()
        with tempfile.TemporaryDirectory() as d:
            path, _ = bz.build(os.path.join(d, "c.zip"))
            with zipfile.ZipFile(path) as z:
                infos = z.infolist()
        self.assertTrue(infos)
        for info in infos:
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), info.filename)
            self.assertEqual(info.external_attr >> 16, 0o100644, info.filename)
            self.assertEqual(info.create_system, 3, info.filename)
            self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED, info.filename)

    def test_same_files_give_the_same_zip_whatever_their_dates(self):
        # a fresh clone gives every file a new mtime: the zip must not change with it
        bz = load_build_zip()
        with tempfile.TemporaryDirectory() as d:
            hashes = []
            for n, stamp in enumerate((315532800 + 86400 * 400, time.time() - 3600)):
                root = os.path.join(d, "r%d" % n)
                shutil.copytree(SKILL_DIR, os.path.join(root, "skill", "clean-thumbnail"),
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(root, "LICENSE"))
                for base, _, files in os.walk(root):
                    for f in files:
                        os.utime(os.path.join(base, f), (stamp, stamp))
                bz.ROOT, bz.SKILL = root, os.path.join(root, "skill", "clean-thumbnail")
                path, _ = bz.build(os.path.join(d, "z%d.zip" % n))
                hashes.append(sha(path))
            self.assertEqual(hashes[0], hashes[1])

    def test_unsafe_path_refuses_to_build(self):
        bz = load_build_zip()
        with tempfile.TemporaryDirectory() as d:
            skill = os.path.join(d, "skill", "clean-thumbnail")
            os.makedirs(os.path.join(skill, "fonts"))
            for name in ("SKILL.md", os.path.join("fonts", "Font[wght].ttf")):
                with open(os.path.join(skill, name), "w") as f:
                    f.write("x")
            with open(os.path.join(d, "LICENSE"), "w") as f:
                f.write("MIT")
            bz.ROOT, bz.SKILL = d, skill
            out = os.path.join(d, "out.zip")
            with self.assertRaisesRegex(ValueError, r"fonts/Font\[wght\]\.ttf"):
                bz.build(out)
            self.assertFalse(os.path.exists(out))
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(bz.main([out]), 1)
            self.assertIn("refusing to build", printed.getvalue())


class Frontmatter(unittest.TestCase):
    """SKILL.md frontmatter within the limits claude.ai and Codex enforce on upload."""

    def setUp(self):
        self.fm = frontmatter(os.path.join(SKILL_DIR, "SKILL.md"))

    def test_name_matches_the_folder(self):
        name = self.fm.get("name", "")
        self.assertEqual(name, os.path.basename(SKILL_DIR))
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)

    def test_description_and_compatibility_fit_their_limits(self):
        desc, compat = self.fm.get("description", ""), self.fm.get("compatibility", "")
        self.assertTrue(desc.strip())
        self.assertLessEqual(len(desc), 1024, "description is %d characters" % len(desc))
        self.assertNotRegex(desc, r"[<>]")  # uploaders refuse angle brackets in the description
        self.assertLessEqual(len(compat), 500, "compatibility is %d characters" % len(compat))

    def test_metadata_version_is_present(self):
        version = self.fm.get("metadata.version", "")
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        # the release line matches the renderer's (the patch number is bumped together at release)
        self.assertEqual(version, thumb.__version__)

    def test_parser_reads_folded_and_quoted_values(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "SKILL.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write('---\nname: x\ndescription: >-\n  one\n  two\nmetadata:\n  version: "1.2.3"\n---\nbody\n')
            fm = frontmatter(path)
        self.assertEqual((fm["description"], fm["metadata.version"]), ("one two", "1.2.3"))


class DocTables(unittest.TestCase):
    """Every Markdown table row in SKILL.md and references/*.md has as many cells as its header (a missing or
    extra pipe shifts a whole row of a flag table into the wrong column)."""

    def files(self):
        return [os.path.join(SKILL_DIR, "SKILL.md")] + sorted(glob.glob(os.path.join(SKILL_DIR, "references", "*.md")))

    def test_rows_match_their_header(self):
        bad, count = [], 0
        for path in self.files():
            for table in table_rows(path):
                count += 1
                (_, head), rows = table[0], table[1:]
                if rows and not all(re.fullmatch(r"\s*:?-{3,}:?\s*", c) for c in rows[0][1]):
                    bad.append("%s:%d: the row under the header is not a --- separator"
                               % (os.path.relpath(path, ROOT), rows[0][0]))
                for line, cells in rows:
                    if len(cells) != len(head):
                        bad.append("%s:%d: %d cells, header has %d" % (os.path.relpath(path, ROOT), line,
                                                                      len(cells), len(head)))
        self.assertTrue(count, "no tables found")
        self.assertEqual(bad, [])

    def test_checker_counts_escaped_pipes_as_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("| a | b |\n|---|---|\n| `x \\| y` | z |\n| one | two | three |\n\n```\n| not | a | table |\n```\n")
            (table,) = table_rows(path)
        self.assertEqual([len(c) for _, c in table], [2, 2, 2, 3])


if __name__ == "__main__":
    unittest.main()
