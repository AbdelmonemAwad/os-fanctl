#!/usr/bin/env python3
"""No comment inside a widget's <translations>, because one there empties the dashboard.

The dashboard controller builds its widget list like this (V: OPNsense 26.7.4_1,
Core/Api/DashboardController.php:124,136):

    $translations = (array)($metadataAttributes->translations ?? []);
    foreach ($translations as $key => $value) { $translations[$key] = gettext($value); }

An XML comment inside that element arrives in the cast as a "comment" key alongside the
strings. With exactly one comment the value is a SimpleXMLElement, gettext() coerces it
through __toString(), and nothing anywhere says a thing. With two or more it is an array,
gettext() raises a TypeError, and get_dashboard answers

    {"errorMessage":"Unexpected error, check log for details"}

That API is what the whole Lobby is built from, so the failure is not "this widget is
missing": every widget on every dashboard disappears and the page shows one red box, with
nothing in any log naming the file that did it.

Measured on the reference appliance, 2026-09-22: three comments in that element emptied
the dashboard; moving them out brought it back. This plugin had one, which is to say it
was one comment away and there was no sign of it anywhere.

So the rule is checked rather than remembered. Comments elsewhere in the file are welcome
and are how these files are meant to be documented - it is this one element that must hold
strings and nothing else.

    python3 tools/check-widget-metadata.py [directory]
"""
import sys
import xml.dom.minidom
from pathlib import Path


def offences(path):
    """Every comment node inside a <translations> element in this file, with its line."""
    try:
        document = xml.dom.minidom.parse(str(path))
    except Exception as failure:                      # noqa: BLE001 - reported, not raised
        return [(0, 'could not be parsed: %s' % failure)]

    found = []
    for element in document.getElementsByTagName('translations'):
        for child in element.childNodes:
            if child.nodeType == child.COMMENT_NODE:
                # minidom does not carry line numbers, and the text of the comment is a
                # better pointer than a number would be anyway: it is what the person has
                # to find and move.
                first = (child.data or '').strip().splitlines()
                found.append((0, (first[0] if first else '(empty comment)')[:70]))
    return found


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else '.')
    files = sorted(root.glob('src/**/widgets/Metadata/*.xml'))

    if not files:
        print('no widget metadata here')
        return 0

    bad = 0
    for path in files:
        for _, what in offences(path):
            print('%s: comment inside <translations>: %s' % (path, what))
            bad += 1

    if bad:
        print()
        print('%d comment(s) inside a <translations> element.' % bad)
        print('Move them outside it. Two of them in one element take down every widget')
        print('on every dashboard, and nothing in any log says which file did it.')
        return 1

    print('%d widget metadata file(s) checked, no comments inside <translations>.' % len(files))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
