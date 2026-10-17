#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""Every string the page and the widget show is in the catalogue, and has its Arabic.

    tools/check-strings.py [repository]

The strings are the ones OPNsense passes through gettext: lang._() in the page, the labels and
help of the settings form, the menu's and the privilege's names, gettext() in PHP, and the
widget's <translations>, which the dashboard controller passes through gettext() one by one. A
string that reaches gettext without being in src/opnsense/scripts/fanctl/i18n/ui/source.json is
shown in English on an otherwise Arabic page and nothing in the build would say so; this does.
A catalogued string no page uses any more is listed and does not fail.
"""

import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
UI = ROOT / 'src' / 'opnsense' / 'scripts' / 'fanctl' / 'i18n' / 'ui'


def used():
    found = {}
    for f in sorted((ROOT / 'src').rglob('*')):
        if not f.is_file():
            continue
        rel = str(f.relative_to(ROOT))
        if f.suffix == '.volt':
            t = f.read_text(encoding='utf-8')
            for s in (re.findall(r"lang\._\('((?:[^'\\]|\\.)*)'\)", t) +
                      re.findall(r'lang\._\("((?:[^"\\]|\\.)*)"\)', t)):
                found.setdefault(s.replace("\\'", "'"), rel)
        elif f.suffix == '.xml' and 'Metadata' in f.parts:
            for tr in ET.parse(f).getroot().iter('translations'):
                for child in tr:
                    if child.text and child.text.strip():
                        found.setdefault(' '.join(child.text.split()), rel)
        elif f.suffix == '.xml' and ('forms' in f.parts or f.name in ('Menu.xml', 'ACL.xml')):
            t = f.read_text(encoding='utf-8')
            for s in re.findall(r'<(?:label|help|name|description)>(.*?)</(?:label|help|name|description)>', t, re.S):
                found.setdefault(' '.join(s.split()), rel)
            for s in re.findall(r'VisibleName="([^"]+)"', t):
                found.setdefault(s, rel)
        elif f.suffix in ('.inc', '.php'):
            t = f.read_text(encoding='utf-8', errors='ignore')
            for s in re.findall(r"gettext\('((?:[^'\\]|\\.)*)'\)", t):
                found.setdefault(s, rel)
    return found


def main():
    try:
        source = json.loads((UI / 'source.json').read_text(encoding='utf-8'))
        arabic = json.loads((UI / 'ar_SA.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print('the catalogue cannot be read: %s' % exc)
        return 1
    strings = used()
    absent = [(s, f) for s, f in strings.items() if s not in source]
    untranslated = [s for s in source if not arabic.get(s)]
    stale = [s for s in source if s not in strings]
    print('on the page and the widget: %d | catalogued: %d | translated: %d'
          % (len(strings), len(source), sum(1 for s in source if arabic.get(s))))
    for s in stale:
        print('   catalogued but unused: %s' % s[:90])
    for s, f in absent:
        print('   NOT CATALOGUED (%s): %s' % (f, s[:90]))
    for s in untranslated:
        print('   NO ARABIC: %s' % s[:90])
    return 1 if absent or untranslated else 0


if __name__ == '__main__':
    sys.exit(main())
