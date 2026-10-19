#!/usr/bin/env python3
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
# Put the copyright line in every source file that lacks one, in that file's own comment style.
#
# The same tool as os-xgs-npu's. A file that already names its holder - in this project's form, or in
# the "Copyright (C)" form of OPNsense's own headers that these plugins began with - is left as it
# is. A file that has an SPDX line gets the copyright line right after it, so the two read as one
# header. A file with neither gets both, after its shebang or its opening tag if it has one. Files
# that are not source - pages, images, data, translations - are left alone.
#
#   tools/add-copyright.py            report what would change
#   tools/add-copyright.py --write    change it
#
# The checks workflow runs the report form and fails on anything it would change, so a new source
# file cannot be merged without the line.
import io
import os
import subprocess
import sys

HOLDER = "Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>"
NAMED = ("Copyright (c) 2026 Abdelmonem Awad", "Copyright (C) 2026 Abdelmonem Awad")
SPDX = "SPDX-License-Identifier: BSD-2-Clause"

# Which comment leader a file takes, by its name.
C_LIKE = (".c", ".h")
HASH = (".py", ".sh", ".yml", ".yaml", ".conf", ".mk", "Makefile")
HASH_NAMES = ("Makefile",)
PHP = (".php", ".inc")
VOLT = (".volt",)
XML = (".xml",)

SKIP_DIRS = ("docs/images",)
SKIP_SUFFIX = (".md", ".jpg", ".png", ".svg", ".txt", ".pkg-descr", ".json", ".tsv", ".b64")


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p.decode() for p in out.split(b"\0") if p]


def kind(path):
    base = os.path.basename(path)
    low = path.lower()
    if any(low.startswith(d) for d in SKIP_DIRS) or low.endswith(SKIP_SUFFIX):
        return None
    if base == ".gitignore":
        return None   # a list of names, not a work
    if base in HASH_NAMES or base.endswith(HASH):
        return "hash"
    if base.endswith(C_LIKE):
        return "c"
    if base.endswith(PHP):
        return "php"
    if base.endswith(VOLT):
        return "volt"
    if base.endswith(XML):
        return "xml"
    # Shell scripts and cron entries with no suffix: decide by their first line.
    try:
        with io.open(path, encoding="utf-8", newline="") as f:
            first = f.readline()
    except (OSError, UnicodeDecodeError):
        return None
    if first.startswith("#!") or "/cron.d/" in path or "/newsyslog.conf.d/" in path or "/rc.syshook.d/" in path:
        return "hash"
    return None


def transform(path, text, k):
    """Return the new text, or None if nothing is to change."""
    if any(named in text for named in NAMED):
        return None
    lines = text.split("\n")
    # After an SPDX line, in the same style.
    for i, l in enumerate(lines):
        if SPDX in l:
            lead = l[: l.index(SPDX)]
            lines.insert(i + 1, lead + HOLDER)
            return "\n".join(lines)
    # Neither: a fresh header after the shebang or the opening tag.
    if k == "c":
        head = ["/*-", " * " + SPDX, " * " + HOLDER, " */"]
        return "\n".join(head + lines)
    if k == "hash":
        at = 1 if lines and lines[0].startswith("#!") else 0
        head = ["#-", "# " + SPDX, "# " + HOLDER, "#"]
        return "\n".join(lines[:at] + head + lines[at:])
    if k == "php":
        at = 1 if lines and lines[0].startswith("<?php") else 0
        head = ["", "/*", " * " + SPDX, " * " + HOLDER, " */"]
        return "\n".join(lines[:at] + head + lines[at:])
    if k == "volt":
        head = ["{#", " # " + SPDX, " # " + HOLDER, " #}"]
        return "\n".join(head + lines)
    if k == "xml":
        at = 1 if lines and lines[0].startswith("<?xml") else 0
        head = ["<!--", "    " + SPDX, "    " + HOLDER, "-->"]
        return "\n".join(lines[:at] + head + lines[at:])
    return None


def main(argv):
    write = "--write" in argv
    changed = []
    for path in tracked():
        k = kind(path)
        if k is None:
            continue
        try:
            with io.open(path, encoding="utf-8", newline="") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        new = transform(path, text, k)
        if new is None:
            continue
        changed.append(path)
        if write:
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    for p in changed:
        print(("added: " if write else "missing: ") + p)
    if changed and not write:
        print("%d file(s) lack the copyright line; run with --write" % len(changed))
        return 1
    print("%d file(s) %s" % (len(changed), "given the copyright line" if write else "checked, every source file carries it"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
