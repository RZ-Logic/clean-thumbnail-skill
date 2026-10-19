"""Build dist/clean-thumbnail.zip for claude.ai, ChatGPT and API skill upload.

The zip holds the skill folder at its root (clean-thumbnail/SKILL.md), which is
what skill uploaders expect. Run: python tools/build_zip.py [output.zip]

The build is deterministic: entries are sorted by name and carry a fixed date
(1980-01-01) and fixed permissions, so the same files give the same zip on any
machine and from any checkout. The zip is written to a temporary file and moved
into place, so a reader never sees a half-written one. Tests call build() with a
path in a temporary folder and never touch dist/.
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skill", "clean-thumbnail")
DEFAULT_OUT = os.path.join(ROOT, "dist", "clean-thumbnail.zip")
SKIP_DIRS = {"__pycache__", "node_modules", ".pytest_cache"}
SKIP_FILES = {".DS_Store"}
# claude.ai rejects archive paths with other characters ("[wght]" in a font name was enough)
SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]+")
DATE = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip can hold: no checkout or build time leaks in
FILE_MODE = 0o100644  # a regular file, rw-r--r--, whatever the file system reports


def skill_files():
    """(path inside the zip, file on disk) for every file the skill ships, sorted by zip path. The repository's
    MIT LICENSE goes in as clean-thumbnail/LICENSE unless the skill folder carries its own copy."""
    out = {}
    for base, dirs, files in os.walk(SKILL):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith(".pyc"):
                continue
            full = os.path.join(base, f)
            out["clean-thumbnail/" + os.path.relpath(full, SKILL).replace(os.sep, "/")] = full
    out.setdefault("clean-thumbnail/LICENSE", os.path.join(ROOT, "LICENSE"))  # MIT text for the code
    return sorted(out.items())


def unsafe_paths():
    """Paths inside the skill folder that skill uploaders would refuse."""
    return sorted(arc[len("clean-thumbnail/"):] for arc, _ in skill_files() if not SAFE_PATH.fullmatch(arc))


def build(out_path=None):
    """Write the zip to out_path (default dist/clean-thumbnail.zip); returns (path, number of files).
    Raises ValueError, writing nothing, when a path has characters skill uploaders reject."""
    bad = unsafe_paths()
    if bad:
        raise ValueError("these paths have characters skill uploaders reject (use A-Z a-z 0-9 . _ - /): "
                         + ", ".join(bad))
    out = os.path.abspath(out_path or DEFAULT_OUT)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    entries = skill_files()
    tmp = "%s.%d.tmp" % (out, os.getpid())
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for arc, full in entries:
                info = zipfile.ZipInfo(arc, date_time=DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3  # Unix, so the permissions below mean the same on every OS
                info.external_attr = FILE_MODE << 16
                with open(full, "rb") as f:
                    z.writestr(info, f.read())
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return out, len(entries)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        out, count = build(argv[0] if argv else None)
    except ValueError as e:
        print("refusing to build: %s" % e)
        return 1
    print("wrote %s (%d files)" % (out, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
