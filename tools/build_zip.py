"""Build dist/clean-thumbnail.zip for claude.ai, ChatGPT and API skill upload.

The zip holds the skill folder at its root (clean-thumbnail/SKILL.md), which is
what skill uploaders expect. Run: python tools/build_zip.py
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skill", "clean-thumbnail")
SKIP_DIRS = {"__pycache__", "node_modules", ".pytest_cache"}
SKIP_FILES = {".DS_Store"}
# claude.ai rejects archive paths with other characters ("[wght]" in a font name was enough)
SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]+")


def unsafe_paths():
    """Paths inside the skill folder that skill uploaders would refuse."""
    bad = []
    for base, dirs, files in os.walk(SKILL):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), SKILL).replace(os.sep, "/")
            if f not in SKIP_FILES and not f.endswith(".pyc") and not SAFE_PATH.fullmatch(rel):
                bad.append(rel)
    return sorted(bad)


def main():
    bad = unsafe_paths()
    if bad:
        print("refusing to build: these paths have characters skill uploaders reject (use A-Z a-z 0-9 . _ - /):")
        for b in bad:
            print("  " + b)
        return 1
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "clean-thumbnail.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(SKILL):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                if f in SKIP_FILES or f.endswith(".pyc"):
                    continue
                full = os.path.join(base, f)
                arc = os.path.join("clean-thumbnail", os.path.relpath(full, SKILL)).replace(os.sep, "/")
                z.write(full, arc)
                count += 1
        z.write(os.path.join(ROOT, "LICENSE"), "clean-thumbnail/LICENSE")  # MIT text for the code
        count += 1
    print("wrote %s (%d files)" % (out, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
