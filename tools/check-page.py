#!/usr/bin/env python3
"""Compile every Volt template the way the GUI will, then parse what comes out as JavaScript.

    python3 tools/check-page.py [file ...]

With no argument it checks every *.volt under src/, which is how the workflow runs it.

Two failures this catches, and both of them have taken a page in this project down.

AN APOSTROPHE INSIDE lang._(). Volt compiles that call into a PHP single-quoted string, so the
quote has to arrive already escaped for PHP. One backslash out and the whole page is replaced
by the crash reporter - not a stray character on screen, the entire page - and the error names
a line in generated PHP that does not correspond to anything in the template.

AND AN ORDINARY JAVASCRIPT MISTAKE, which nothing else here sees. The <script> block in a Volt
template is not JavaScript until the template engine has been over it, so no linter reads it
and no test loads it. The first anybody knows is a blank panel in a browser. So the engine's
own substitution is made here and the result is handed to a real parser.

What this is NOT: it does not run the page, it does not know whether a selector matches
anything, and a template that parses is not a template that works. It is the floor, not the
ceiling - it catches the two mistakes that cost the most to find any later way.
"""
import io
import os
import pathlib
import re
import subprocess
import sys

BACKSLASH = chr(92)

CALL = re.compile(r"\{\{\s*lang\._\((['\"])(.*?)\1\)\s*\}\}", re.S)


def as_js_string(match):
    """The literal, and no quotes of its own.

    The engine substitutes the translated text in place, and every one of these calls inside a
    script block is already written between a pair of double quotes by the template - that is
    what makes it a JavaScript string. Adding another pair here would test a page nobody is
    ever served.
    """
    body = match.group(2)
    return body.replace(BACKSLASH, BACKSLASH * 2).replace('"', BACKSLASH + '"')


def check(path):
    """0 if this template is sound, 1 if it is not. Prints what is wrong either way."""
    text = io.open(path, encoding='utf-8').read()

    bad = []
    for match in CALL.finditer(text):
        quote, body = match.group(1), match.group(2)
        if quote == "'" and "'" in body.replace(BACKSLASH + "'", ''):
            bad.append(body[:70])

    if bad:
        print('%s: an apostrophe inside lang._(), which Volt cannot compile:' % path)
        for item in bad:
            print('    %s' % item)
        return 1

    rendered = CALL.sub(as_js_string, text)
    rendered = re.sub(r'\{#.*?#\}', '', rendered, flags=re.S)
    rendered = re.sub(r'\{\{.*?\}\}', '""', rendered, flags=re.S)
    rendered = re.sub(r'\{%.*?%\}', '', rendered, flags=re.S)

    blocks = re.findall(r'<script>(.*?)</script>', rendered, re.S)
    if not blocks:
        print('%s: no script block, nothing to parse' % path)
        return 0

    # Beside this script and never beside the template: run it once from inside a repository
    # with the output next to the input and it leaves a rendered.js in the tree.
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.rendered.js')
    io.open(out, 'w', encoding='utf-8', newline='\n').write('\n'.join(blocks))
    try:
        result = subprocess.run(['node', '--check', out], capture_output=True, text=True)
    except FileNotFoundError:
        print('%s: node is not here, so the script block was not parsed' % path)
        return 0
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass

    if result.returncode != 0:
        print('%s: the script block does not parse:' % path)
        print(result.stdout + result.stderr)
        return 1

    print('%s: %d script block(s), %d lang._() calls, parses clean'
          % (path, len(blocks), len(CALL.findall(text))))
    return 0


def main(argv):
    targets = argv[1:] or sorted(str(item) for item in pathlib.Path('src').rglob('*.volt'))
    if not targets:
        print('no Volt template here')
        return 0

    return 1 if sum(check(path) for path in targets) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
