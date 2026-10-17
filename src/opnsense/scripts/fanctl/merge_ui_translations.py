#!/usr/local/bin/python3
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""
Add the Fan Control GUI strings (i18n/ui/<locale>.json) to the OPNsense gettext catalogue
of every installed GUI language. The same merge os-linkhealth, os-netreport and os-xgs-npu
ship, each with its own copy, its own temporary name and its own record:

- a string the catalogue already translates is left alone, so core's words - Settings, Port,
  Cancel - keep core's Arabic, and running it again is harmless;
- what this script wrote is remembered in /var/db/fanctl/i18n-owned.json, so --force can
  correct this plugin's own strings and nothing else;
- run by the installer, at every boot (rc.syshook.d/start/64-fanctl-i18n) and by pkg the moment a
  package writes a catalogue (the os-fanctl trigger), because a catalogue written again loses
  what was merged into it.

Prints the number of strings added; exit code 0 either way.
"""

import glob
import json
import os
import struct
import sys

LOCALE_DIR = '/usr/local/share/locale'
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'i18n', 'ui')


def read_mo(path):
    """msgid bytes -> msgstr bytes (plural and context entries kept as raw bytes)"""
    data = open(path, 'rb').read()
    magic = struct.unpack('<I', data[:4])[0]
    endian = '<' if magic == 0x950412de else '>'
    _, count, orig_off, trans_off = struct.unpack(endian + '4I', data[4:20])
    entries = {}
    for i in range(count):
        olen, opos = struct.unpack(endian + '2I', data[orig_off + 8 * i:orig_off + 8 * i + 8])
        tlen, tpos = struct.unpack(endian + '2I', data[trans_off + 8 * i:trans_off + 8 * i + 8])
        entries[data[opos:opos + olen]] = data[tpos:tpos + tlen]
    return entries


def write_mo(path, entries):
    """standard little-endian .mo without a hash table (gettext then uses binary search)"""
    keys = sorted(entries)
    count = len(keys)
    orig_off = 28
    trans_off = orig_off + 8 * count
    data_off = trans_off + 8 * count
    ids = strs = b''
    orig_tab, trans_tab = [], []
    for k in keys:
        orig_tab.append((len(k), len(ids)))
        ids += k + b'\0'
    for k in keys:
        trans_tab.append((len(entries[k]), len(strs)))
        strs += entries[k] + b'\0'
    out = struct.pack('<7I', 0x950412de, 0, count, orig_off, trans_off, 0, data_off)
    for length, pos in orig_tab:
        out += struct.pack('<2I', length, data_off + pos)
    for length, pos in trans_tab:
        out += struct.pack('<2I', length, data_off + len(ids) + pos)
    out += ids + strs
    # A temporary name of our own: os-linkhealth, os-netreport and os-xgs-npu run the same merge from
    # their own boot hooks, and two writers on one temporary path would trade halves of a catalogue.
    tmp = path + '.fanctl.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(out)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


OWNED = '/var/db/fanctl/i18n-owned.json'


def owned():
    """The msgids this plugin has written into the catalogues itself.

    A catalogue holds core's translations and ours in one file, and the rule
    "never touch a string that is already translated" is what keeps this from
    rewording the whole GUI: some of this plugin's strings are core's words -
    Port, Settings, Save, Cancel - and core's Arabic must win every time.

    But that rule also froze OUR OWN strings: correcting a word here would
    never reach the catalogue, because the catalogue already had the old one.
    So the script remembers what it wrote. Anything in this list is ours to
    correct; anything else is left exactly as it was found.
    """
    try:
        with open(OWNED, 'r') as handle:
            return {locale: set(ids) for locale, ids in json.load(handle).items()}
    except (OSError, ValueError):
        return {}


def remember(state):
    try:
        os.makedirs(os.path.dirname(OWNED), mode=0o750, exist_ok=True)
        with open(OWNED, 'w') as handle:
            json.dump({locale: sorted(ids) for locale, ids in state.items()}, handle)
        os.chmod(OWNED, 0o640)
    except OSError:
        pass          # the merge itself succeeded; the bookkeeping is a nicety


def main():
    force = '--force' in sys.argv
    state = owned()
    added_total = 0
    for src in sorted(glob.glob(os.path.join(UI_DIR, '*_*.json'))):
        locale = os.path.basename(src)[:-5]
        mo = os.path.join(LOCALE_DIR, locale, 'LC_MESSAGES', 'OPNsense.mo')
        if not os.path.exists(mo):
            continue
        try:
            strings = json.load(open(src, encoding='utf-8'))
            entries = read_mo(mo)
        except Exception as exc:
            print(f'{locale}: skipped ({exc})', file=sys.stderr)
            continue
        mine = state.setdefault(locale, set())
        added = 0
        for msgid, msgstr in strings.items():
            if not msgstr:
                continue
            key = msgid.encode('utf-8')
            current = entries.get(key)
            if not current:
                entries[key] = msgstr.encode('utf-8')
                mine.add(msgid)
                added += 1
            elif force and msgid in mine and current != msgstr.encode('utf-8'):
                entries[key] = msgstr.encode('utf-8')
                added += 1
        if added:
            write_mo(mo, entries)
            read_mo(mo)          # sanity check: the result must parse
        added_total += added
    remember(state)
    print(added_total)


if __name__ == '__main__':
    main()
