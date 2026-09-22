#!/usr/bin/env python3
"""Keep Arabic out of the code. It belongs in the translation files and nowhere else.

The rule is the owner's, and it is a good one for a project meant to be read by people
who do not read Arabic: source, comments, commit messages, identifiers and log lines stay
in English, and every Arabic word lives in a catalogue where a translator can find it and
a reviewer can diff it. A sentence written straight into a template cannot be reviewed by
somebody who cannot read it, cannot be changed without touching code, and will not be
found by anything that counts what still needs translating.

What is exempt, and only this:
  * the translation catalogues themselves - i18n/**, *.po, *.mo;
  * anything a repository declares in .code-language-ignore, one glob per line with the
    reason beside it, the same shape as .private-data-ignore.

    tools/check-code-language.py [paths...]

Exit status is 0 when clean, 1 when something must move into a catalogue.
"""
import pathlib
import sys

# Arabic, Arabic Supplement, Arabic Extended-A, and the presentation forms - as numbers,
# not as a character range.
#
# The obvious way to write this is a regex class with the two ends of each range in it,
# and that way puts literal Arabic in this file: whatever escape is typed, something on
# the way in resolves it, and then the checker fails on its own source. It did, on the
# first run - a fair demonstration that the rule bites. Comparing code points needs no
# character at all, so this file stays ASCII, as it asks every other file to be.
ARABIC_RANGES = (
    (0x0600, 0x06ff),   # Arabic
    (0x0750, 0x077f),   # Arabic Supplement
    (0x08a0, 0x08ff),   # Arabic Extended-A
    (0xfb50, 0xfdff),   # Arabic Presentation Forms-A
    (0xfe70, 0xfeff),   # Arabic Presentation Forms-B
)


def has_arabic(line):
    return any(low <= ord(character) <= high
               for character in line
               for low, high in ARABIC_RANGES)


SKIP_DIRS = {'.git', '__pycache__', 'node_modules'}
BINARY = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.gz', '.mo'}
CATALOGUES = ('.po',)
IGNORE_FILE = '.code-language-ignore'


def exempt(relative):
    """Whether this path is a translation file rather than code."""
    parts = relative.as_posix().split('/')
    if 'i18n' in parts or 'locale' in parts:
        return True
    return relative.suffix.lower() in CATALOGUES


def exceptions(root):
    path = pathlib.Path(root) / IGNORE_FILE
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            out.append(line)
    return out


def main(argv):
    roots = argv[1:] or ['.']
    failures = 0
    checked = 0
    exempted = 0

    for root in roots:
        allowed = exceptions(root)
        base = pathlib.Path(root)
        for path in sorted(base.rglob('*')):
            if not path.is_file() or any(p in SKIP_DIRS for p in path.parts):
                continue
            if path.suffix.lower() in BINARY:
                continue
            relative = path.relative_to(base)
            if exempt(relative) or any(relative.match(p) for p in allowed):
                exempted += 1
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            checked += 1
            for number, line in enumerate(text.splitlines(), 1):
                if has_arabic(line):
                    print('%s:%d: Arabic in code - move it to the catalogue' % (path, number))
                    failures += 1

    if failures:
        print()
        print('%d line(s) carry Arabic outside a translation file.' % failures)
        print('Put the English in the source and the Arabic in i18n/ui/ar_SA.json, keyed')
        print('by that English, so lang._() finds it like every other string.')
        return 1
    print('%d file(s) checked, %d translation files skipped, no Arabic in code.'
          % (checked, exempted))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
