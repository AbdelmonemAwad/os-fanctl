#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""The page's and the widget's JavaScript, run in node, for the tests.

tools/check-page.py proves the page parses. The tests need more than that from both files: what a
function of the page answers, and what the widget draws, for readings written in the test. So:

THE PAGE is not JavaScript until the template engine has been over it. Its lang._() calls are
substituted as check-page.py substitutes them - the English text in place of the call - and a
named const or function is cut out of the result whole, its brackets counted outside strings and
comments, to be run in node beside the test's own lines with a stand-in for the two jQuery helpers
those parts use ($.each, $.isArray). A part that touches the document cannot be run this way and is
not asked for; the brackets of a regular expression literal are not told apart from code, so a
part that has one with a bracket in it cannot be cut out either, and the cut says so.

THE WIDGET is one of the dashboard's modules. It is copied under an .mjs name into a directory of
its own, beside a module that stands in for the two globals it reaches for - BaseTableWidget, which
the test does not use, and jQuery's text escaping - and imported, and its translations are the
English of its Metadata file, as gettext() hands them over before there is any Arabic.

Nothing here reads anything but the files it is given, and node is the only thing it runs. Where
node is not installed every answer is None, and the caller says the check was not run.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / 'src' / 'opnsense' / 'mvc' / 'app' / 'views' / 'OPNsense' / 'FanCtl' / 'index.volt'
WIDGET = ROOT / 'src' / 'opnsense' / 'www' / 'js' / 'widgets' / 'FanCtl.js'
METADATA = ROOT / 'src' / 'opnsense' / 'www' / 'js' / 'widgets' / 'Metadata' / 'FanCtl.xml'

BACKSLASH = chr(92)
CALL = re.compile(r"\{\{\s*lang\._\((['\"])(.*?)\1\)\s*\}\}", re.S)

# The two jQuery helpers the page's pure parts call. Anything else of jQuery's is the document,
# which is not here: calling it is a mistake in the test, and says so.
JQUERY = '''
const $ = function () { throw new Error('the page asked for the document, which is not here'); };
$.each = function (object, call) { Object.keys(object).forEach(function (key) { call(key, object[key]); }); };
$.isArray = Array.isArray;
'''


def render(path=PAGE):
    """The page's text as the browser gets it from the English GUI: each lang._() its own words."""
    text = pathlib.Path(path).read_text(encoding='utf-8')

    def literal(match):
        return match.group(2).replace(BACKSLASH, BACKSLASH * 2).replace('"', BACKSLASH + '"')

    text = CALL.sub(literal, text)
    text = re.sub(r'\{#.*?#\}', '', text, flags=re.S)
    return re.sub(r'\{\{.*?\}\}', '""', text, flags=re.S)


def _close(text, at):
    """The index just past the bracket that closes the one opening at text[at]."""
    pairs = {'{': '}', '[': ']', '(': ')'}
    stack = [pairs[text[at]]]
    i = at + 1
    while i < len(text):
        c = text[i]
        if c in '\'"`':
            i += 1
            while i < len(text) and text[i] != c:
                i += 2 if text[i] == BACKSLASH else 1
        elif text.startswith('//', i):
            i = text.index('\n', i)
        elif text.startswith('/*', i):
            i = text.index('*/', i) + 1
        elif c in pairs:
            stack.append(pairs[c])
        elif c in ')]}':
            if c != stack.pop():
                raise ValueError('brackets do not pair at %d' % i)
            if not stack:
                return i + 1
        i += 1
    raise ValueError('the bracket at %d is never closed' % at)


def part(text, name):
    """`const NAME = {...};`, `const NAME = [...];` or `function NAME(...) {...}`, cut out whole."""
    found = re.search(r'\bconst %s = ([\[{])' % re.escape(name), text)
    if found:
        end = _close(text, found.start(1))
        if text[end] != ';':
            raise ValueError('const %s does not end where its brackets close' % name)
        return text[found.start():end + 1]
    found = re.search(r'\bfunction %s\(' % re.escape(name), text)
    if not found:
        raise ValueError('no const or function %s in the page' % name)
    body = text.index('{', _close(text, found.end() - 1))
    return text[found.start():_close(text, body)]


def node():
    return shutil.which('node')


def _run(directory, script, name):
    path = os.path.join(directory, name)
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(script)
    done = subprocess.run([node(), path], capture_output=True, text=True, encoding='utf-8',
                          timeout=60)
    if done.returncode != 0:
        raise RuntimeError('node failed:\n%s%s' % (done.stdout, done.stderr))
    return json.loads(done.stdout.strip().splitlines()[-1])


def page(names, expressions, path=PAGE):
    """[the value of each expression], in node, beside the page's parts of these names; or None.
    An expression that throws is {'error': its message} in the list, so the check that asked for it
    fails with that message and the others are still answered."""
    if not node():
        return None
    text = render(path)
    guarded = ['(() => { try { return %s; } catch (error) { return {error: String(error)}; } })()'
               % expression for expression in expressions]
    script = '\n'.join([JQUERY] + [part(text, name) for name in names] +
                       ['console.log(JSON.stringify([%s]));' % ', '.join(guarded)])
    directory = tempfile.mkdtemp(prefix='fanctl-page-')
    try:
        return _run(directory, script, 'page.js')
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def translations(path=METADATA):
    """The widget's strings as gettext() hands them to it before there is a translation."""
    found = {}
    for element in ET.parse(str(path)).getroot().iter('translations'):
        for child in element:
            found[child.tag] = ' '.join((child.text or '').split())
    return found


def widget(body, path=WIDGET, metadata=METADATA):
    """What `body` prints - JSON, on its last line - run as a module beside a FanCtl widget made
    with its English translations, `widget` in scope; or None without node."""
    if not node():
        return None
    directory = tempfile.mkdtemp(prefix='fanctl-widget-')
    try:
        shutil.copyfile(str(path), os.path.join(directory, 'FanCtl.mjs'))
        script = '\n'.join([
            "globalThis.BaseTableWidget = class { constructor(config) { this.config = config; } };",
            "globalThis.$ = function () {",
            "    let value = '';",
            "    const node = {",
            "        text(text) { value = String(text); return node; },",
            "        html() { return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }",
            "    };",
            "    return node;",
            "};",
            "const { default: FanCtl } = await import('./FanCtl.mjs');",
            "const widget = new FanCtl({});",
            "widget.translations = %s;" % json.dumps(translations(metadata)),
            body,
        ])
        return _run(directory, script, 'widget.mjs')
    finally:
        shutil.rmtree(directory, ignore_errors=True)
