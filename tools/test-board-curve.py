#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""The XGS 3300's board curve: where the quiet curve gives way to the standard one, and where it
comes back (fanctl.py board_curve(), since 2026-11-17).

The coprocessor's die was the only reading the rule followed. It follows the coprocessor's memory
and every port PHY os-xgs-npu's driver holds as well, each against the table's figures, and a
reading that cannot be taken changes nothing, as it changed nothing while the coprocessor's was the
only one: it starts no hold, and one that held the standard curve at the last check holds it until
it is read again. Checked here: the figures, in the table, the settings help and the README alike;
the rule, minute by minute, with the driver's nodes read through a stand-in for sysctl(8) and the
coprocessor's GPIO line through a stand-in for npu-run.sh; and the page's words for a hold, run in
node (tools/pagejs.py).

Synthetic readings only. Nothing here reads a sysctl, runs kenv or npu-run.sh, opens a port, or
writes anywhere but a directory of its own that it removes.

    python3 tools/test-board-curve.py"""

import pathlib
import re
import shutil
import sys
import tempfile
import types
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'src' / 'opnsense' / 'scripts' / 'fanctl'
FORM = ROOT / 'src' / 'opnsense' / 'mvc' / 'app' / 'controllers' / 'OPNsense' / 'FanCtl' / 'forms' / 'general.xml'
README = ROOT / 'README.md'

for missing in ('fcntl', 'syslog'):
    try:
        __import__(missing)
    except ImportError:
        stub = types.ModuleType(missing)
        for name in ('LOG_NOTICE', 'LOG_WARNING', 'LOG_ERR', 'LOG_CRIT', 'LOG_INFO', 'LOG_PID',
                     'LOG_DAEMON', 'LOCK_EX', 'LOCK_NB', 'LOCK_UN'):
            setattr(stub, name, 0)
        sys.modules[missing] = stub

sys.path.insert(0, str(SCRIPTS))
import fanctl                                   # noqa: E402
import pagejs                                   # noqa: E402
import sensors                                  # noqa: E402

failures = []


def check(label, condition, shown=''):
    print('%s  %s%s' % ('ok  ' if condition else 'FAIL', label, ('  [%s]' % (shown,)) if shown else ''))
    if not condition:
        failures.append(label)


TABLE = fanctl.load_table()
FIELDS = {'maker': 'Sophos', 'product': 'XGS', 'version': '3300r1', 'serial': 'AMDA0202-0004',
          'board': 'XGS 17'}
KEY, ENTRY = fanctl.match_model(TABLE, FIELDS)
SPEC = ENTRY.get('board_curve') or {}


# -- The figures: the table's, and the same in the settings help and the README -------------------

limits = fanctl.board_limits(SPEC)
check('the XGS 3300 entry has a board curve', KEY == 'sophos-xgs-3300' and bool(SPEC), KEY)
check('the table\'s figures: coprocessor 85/78, memory 80/75, port PHYs 95/88',
      limits == {'npu': (85.0, 78.0), 'memory': (80.0, 75.0), 'phy': (95.0, 88.0)}, limits)
check('every cool figure is below its hot one',
      bool(limits) and all(cool < hot for hot, cool in limits.values()))
check('the code\'s own figures are the table\'s, for a table that gives none',
      fanctl.board_limits({}) == limits, fanctl.board_limits({}))
check('a cool figure at or above its hot one is refused',
      fanctl.board_limits({'memory_hot_c': 70, 'memory_cool_c': 70}) is None)
check('a figure that is not a number is refused', fanctl.board_limits({'phy_hot_c': 'hot'}) is None)
check('the table says where each figure comes from, with the figures it has', all(
    any(text.startswith('%s, %d and %d C:' % (kind, limits[kind][0], limits[kind][1]))
        for text in SPEC.get('figures') or [])
    for kind in ('npu', 'memory', 'phy')), [text[:20] for text in SPEC.get('figures') or []])

form = ET.parse(str(FORM)).getroot()
helps = [' '.join((field.findtext('help') or '').split()) for field in form.iter('field')
         if field.findtext('id') == 'fanctl.general.board_curve']
stated = re.search(r'while the coprocessor reads (\d+) C or more, its memory (\d+) C or more or a port PHY '
                   r'(\d+) C or more, until each is below (\d+), (\d+) and (\d+) C', helps[0] if helps else '')
check('the settings help states the table\'s figures',
      stated is not None and [float(x) for x in stated.groups()] == [
          limits['npu'][0], limits['memory'][0], limits['phy'][0],
          limits['npu'][1], limits['memory'][1], limits['phy'][1]],
      stated.groups() if stated else helps)
check('the settings help says a reading that cannot be taken changes nothing',
      bool(helps) and 'A reading that cannot be taken changes nothing' in helps[0], helps)
readme = README.read_text(encoding='utf-8')
rows = dict((kind, (float(hot), float(cool))) for kind, hot, cool in re.findall(
    r'^\| (coprocessor|memory|port PHYs) \| (\d+) C \| (\d+) C \|', readme, re.M))
check('the README states the table\'s figures',
      rows == {'coprocessor': limits['npu'], 'memory': limits['memory'], 'port PHYs': limits['phy']}, rows)
check('the README says a reading that cannot be taken changes nothing',
      '**A reading that cannot be taken changes nothing**' in readme)


# -- The machine, stood in for -----------------------------------------------------------------------

WORK = tempfile.mkdtemp(prefix='fanctl-board-')
fanctl.STATE_DIR = WORK + '/db'
fanctl.RUN_DIR = WORK + '/run'
fanctl.LOG_DIR = WORK + '/log'
fanctl.BOARD_CURVE = fanctl.STATE_DIR + '/board-curve.json'

said = []
fanctl.log = lambda message, level=None: said.append(message)
fanctl.match_model = lambda table=None, fields=None: (KEY, ENTRY)
OPTIONS = {'enabled': True, 'board_curve': 'quiet'}
fanctl.settings = lambda root=None: dict(OPTIONS)

# Every write of the state file, so that a check can say it was not rewritten.
written = []
write_json = fanctl.write_json


def counting_write(path, payload, *args, **kwargs):
    written.append(path)
    return write_json(path, payload, *args, **kwargs)


fanctl.write_json = counting_write

MACHINE = {'npu': 70.0, 'nodes': {}, 'sysctl': True, 'line': 'input', 'answers': True, 'asked': []}
NAMES = ['dev.octep.0.%desc', 'dev.octep.0.mgmt_start', 'dev.octep.0.temperature.npu',
         'dev.octep.0.temperature.inlet', 'dev.octep.0.temperature.memory',
         'dev.octep.0.temperature.switch', 'dev.octep.0.temperature.sfp_phy',
         'dev.octep.0.temperature.module_phy']


def fake_sysctl(args, timeout=10):
    """sysctl(8) as it answers on the XGS 3300: -N lists names and reads nothing; -e prints
    name=value for each node that answered, and nothing for one the driver refused (ENOENT)."""
    if not MACHINE['sysctl']:
        return ''
    if args[:2] == ['/sbin/sysctl', '-N']:
        return '\n'.join(NAMES) + '\n'
    if args[:2] == ['/sbin/sysctl', '-e']:
        lines = []
        for name in args[2:]:
            value = MACHINE['nodes'].get(name.rsplit('.', 1)[-1])
            if value is not None:
                lines.append('%s=%.1fC' % (name, value))
        return '\n'.join(lines) + '\n'
    raise AssertionError('sysctl asked %r' % (args,))


def fake_npu_run(command, seconds=20):
    MACHINE['asked'].append(command)
    if not MACHINE['answers']:
        return 1, 'no answer'
    if command.startswith('gpioinfo'):
        return 0, 'line  48:      unnamed       unused   %s  active-high' % MACHINE['line']
    if command.startswith('gpioset'):
        MACHINE['line'] = 'output'
        return 0, ''
    if command.startswith('gpioget'):
        MACHINE['line'] = 'input'
        return 0, ''
    raise AssertionError('the coprocessor was asked %r' % command)


sensors._run = fake_sysctl
fanctl._npu_run = fake_npu_run
fanctl._npu_celsius = lambda: MACHINE['npu']

REST = {'memory': 52.6, 'switch': 61.1, 'sfp_phy': 63.2, 'module_phy': 49.1}


def fresh(line='input'):
    """A machine this plugin has not set yet: no state of its own, the line as the board left it."""
    shutil.rmtree(WORK + '/db', ignore_errors=True)
    MACHINE['line'] = line


def minute(npu=None, **nodes):
    MACHINE['npu'] = npu
    MACHINE['nodes'] = nodes
    MACHINE['asked'] = []
    del said[:]
    del written[:]
    return fanctl.board_curve()


def state_of(document):
    return {item['name'].rsplit('.', 1)[-1]: item['state'] for item in document.get('inputs') or []}


def reasons(document):
    return [(item['kind'], item['why'], item['name'].rsplit('.', 1)[-1]) for item in document['held_for']]


def holds(text):
    return any('standard curve until' in line for line in text)


try:
    # read_held(): the driver's nodes only, each classified, an empty one empty.
    MACHINE['nodes'] = {'npu': 77.6, 'memory': 52.6, 'switch': 61.1}
    held = sensors.read_held()
    check('read_held(): the six temperature nodes and nothing else of the driver\'s',
          [entry['name'] for entry in held] == NAMES[2:], [entry['name'] for entry in held])
    kinds = {entry['name'].rsplit('.', 1)[-1]: (entry['group'], entry['kind'], entry['celsius'])
             for entry in held}
    check('read_held(): memory a memory module, the three port chips PHYs, an empty node empty',
          kinds['memory'] == ('dimm', 'dimm', 52.6) and kinds['switch'] == ('nic', 'phy', 61.1)
          and kinds['sfp_phy'] == ('nic', 'phy', None) and kinds['npu'][1] == 'npu_die', kinds)

    # 1. The second XGS 3300 at rest, quiet asked: every reading below its cool figure.
    fresh()
    doc = minute(npu=77.6, **REST)
    check('at rest: the quiet curve, the line driven low, nothing holds',
          doc['running'] == 'quiet' and doc['gpio'] == 'output' and not doc['held']
          and doc['held_for'] == [] and any(c.startswith('gpioset') for c in MACHINE['asked']), doc['running'])
    check('at rest: the coprocessor, its memory and three port PHYs followed, all cool',
          state_of(doc) == {'npu_c': 'cool', 'memory': 'cool', 'switch': 'cool', 'sfp_phy': 'cool',
                            'module_phy': 'cool'}, state_of(doc))

    # 2. The memory at its hot figure, 80 C: the standard curve, the line given back. 79.9 is not it.
    doc = minute(npu=77.6, **dict(REST, memory=79.9))
    check('memory at 79.9, under 80: the quiet curve stays', doc['running'] == 'quiet' and not said, said)
    doc = minute(npu=77.6, **dict(REST, memory=80.0))
    check('memory at 80.0: the standard curve, the line given back',
          doc['running'] == 'standard' and doc['held'] and doc['gpio'] == 'input', doc['running'])
    check('memory at 80.0: held for the memory, hot, and said so once',
          reasons(doc) == [('memory', 'hot', 'memory')]
          and len(said) == 2 and 'coprocessor memory reads 80.0 C, at or above 80 C' in said[0], said)
    status = fanctl.board_curve_status(ENTRY, OPTIONS)
    check('the page\'s document: held, for dev.octep.0.temperature.memory, hot',
          status['held'] and [(item['name'], item['why']) for item in status['held_for']]
          == [('dev.octep.0.temperature.memory', 'hot')], status)
    doc = minute(npu=77.6, **dict(REST, memory=81.0))
    check('memory still hot a minute later: the state not written again, nothing said',
          doc['held'] and fanctl.BOARD_CURVE not in written and not said, (written, said))

    # 3. Below its hot figure and not its cool one: held, and said to be not yet below its cool
    #    figure, not hot. Below the cool one, 75 C: quiet again.
    doc = minute(npu=77.6, **dict(REST, memory=77.0))
    check('memory at 77.0, between 75 and 80: still the standard curve, nothing said',
          doc['running'] == 'standard' and doc['held'] and not said, said)
    check('memory at 77.0: held for it as not yet below its cool figure ("warm"), not as hot',
          reasons(doc) == [('memory', 'warm', 'memory')], doc['held_for'])
    doc = minute(npu=77.6, **dict(REST, memory=75.0))
    check('memory at 75.0, its cool figure: still the standard curve', doc['running'] == 'standard')
    doc = minute(npu=77.6, **dict(REST, memory=74.9))
    check('memory at 74.9: the quiet curve again, said once',
          doc['running'] == 'quiet' and not doc['held'] and doc['gpio'] == 'output'
          and sum('the quiet curve again' in line for line in said) == 1, said)

    # 4. The hottest port PHY counts, whichever it is, at 95 C, until it is below 88 C.
    for leaf in ('switch', 'sfp_phy', 'module_phy'):
        doc = minute(npu=77.6, **dict(REST, **{leaf: 95.0}))
        check('%s at 95.0: the standard curve, held for a port PHY' % leaf,
              doc['running'] == 'standard' and reasons(doc) == [('phy', 'hot', leaf)], doc['held_for'])
        doc = minute(npu=77.6, **dict(REST, **{leaf: 88.0}))
        check('%s at 88.0: still held, not yet below its cool figure' % leaf,
              doc['running'] == 'standard' and reasons(doc) == [('phy', 'warm', leaf)], doc['held_for'])
        doc = minute(npu=77.6, **dict(REST, **{leaf: 87.9}))
        check('%s at 87.9: the quiet curve again' % leaf, doc['running'] == 'quiet', doc['running'])
    doc = minute(npu=77.6, **dict(REST, switch=94.9, sfp_phy=94.9, module_phy=94.9))
    check('every port PHY at 94.9, under 95: the quiet curve stays', doc['running'] == 'quiet',
          state_of(doc))
    doc = minute(npu=77.6, **REST)

    # 5. The coprocessor itself, as before.
    doc = minute(npu=85.0, **REST)
    check('the coprocessor at 85.0: the standard curve, held for it',
          doc['running'] == 'standard' and reasons(doc) == [('npu', 'hot', 'npu_c')], doc['held_for'])
    doc = minute(npu=77.9, **REST)
    check('the coprocessor at 77.9: the quiet curve again', doc['running'] == 'quiet', doc['running'])

    # 6. A reading that cannot be taken changes nothing.
    #    a. The memory not read again this boot - one failed read, and os-xgs-npu's feeder marks the
    #       module as having no sensor - while nothing holds: quiet throughout, nothing said.
    for _ in range(5):
        doc = minute(npu=77.6, **dict(REST, memory=None))
        check('memory unread, nothing holding: the quiet curve, nothing said',
              doc['running'] == 'quiet' and state_of(doc)['memory'] == 'unread' and not said, said)
    #    b. ...and a hold the coprocessor starts later is not kept by it: quiet again when the
    #       coprocessor is below its cool figure, the memory still unread.
    doc = minute(npu=86.0, **dict(REST, memory=None))
    check('the coprocessor hot, the memory unread: held for the coprocessor alone',
          doc['running'] == 'standard' and reasons(doc) == [('npu', 'hot', 'npu_c')], doc['held_for'])
    doc = minute(npu=77.0, **dict(REST, memory=None))
    check('the coprocessor at 77.0, the memory still unread: the quiet curve again',
          doc['running'] == 'quiet' and not doc['held'], doc['held_for'])
    #    c. The port chips' nodes empty for three checks - a failed read of os-xgs-npu's every four
    #       minutes, and its driver dropping the value at five: quiet throughout.
    for _ in range(3):
        doc = minute(npu=77.6, memory=52.6)
        check('the three PHYs unread, nothing holding: the quiet curve, nothing said',
              doc['running'] == 'quiet' and not said, said)
    doc = minute(npu=77.6, **REST)
    #    d. A hold the memory started ends when the memory is below its cool figure, with a PHY that
    #       was below its own unread at that check: what it last read stands.
    doc = minute(npu=77.6, **dict(REST, memory=82.0))
    doc = minute(npu=77.6, **dict(REST, memory=82.5, sfp_phy=None))
    check('held for the memory, an unread PHY that was cool: held for the memory alone, the state '
          'not written again', reasons(doc) == [('memory', 'hot', 'memory')]
          and fanctl.BOARD_CURVE not in written, (doc['held_for'], written))
    doc = minute(npu=77.6, **dict(REST, memory=70.0, sfp_phy=None))
    check('the memory at 70.0 with that PHY unread: the quiet curve again',
          doc['running'] == 'quiet' and not doc['held'], doc['held_for'])
    doc = minute(npu=77.6, **REST)
    #    e. A reading that held the standard curve and stops holds it still, until it is read again
    #       below its cool figure; nothing said in between.
    doc = minute(npu=77.6, **dict(REST, sfp_phy=96.0))
    doc = minute(npu=77.6, **dict(REST, sfp_phy=None))
    check('the hot SFP+ PHY unread: still the standard curve, held for it as not read, nothing said',
          doc['running'] == 'standard' and reasons(doc) == [('phy', 'unread', 'sfp_phy')] and not said,
          (doc['held_for'], said))
    doc = minute(npu=77.6, **dict(REST, sfp_phy=None))
    check('...and a minute later still', doc['running'] == 'standard' and doc['held'])
    doc = minute(npu=77.6, **dict(REST, sfp_phy=90.0))
    check('read again at 90.0: held, not yet below its cool figure',
          reasons(doc) == [('phy', 'warm', 'sfp_phy')], doc['held_for'])
    doc = minute(npu=77.6, **REST)
    check('read again at 63.2: the quiet curve again', doc['running'] == 'quiet', doc['running'])
    #    f. The coprocessor's own reading, from the CPLD, the same: not read while it holds, held;
    #       not read while nothing holds, nothing.
    doc = minute(npu=None, **REST)
    check('the coprocessor unread, nothing holding: the quiet curve, nothing said',
          doc['running'] == 'quiet' and state_of(doc)['npu_c'] == 'unread' and not said, said)
    doc = minute(npu=86.0, **REST)
    doc = minute(npu=None, **REST)
    check('the coprocessor unread after it was hot: still held, for it, as not read',
          doc['running'] == 'standard' and reasons(doc) == [('npu', 'unread', 'npu_c')], doc['held_for'])
    doc = minute(npu=77.0, **REST)
    check('the coprocessor read again at 77.0: the quiet curve again', doc['running'] == 'quiet')
    #    g. Every node gone - the driver's sysctls not answering at all: one that held the standard
    #       curve holds it still, though it is not even listed; the others change nothing.
    doc = minute(npu=77.6, **dict(REST, memory=85.0))
    MACHINE['sysctl'] = False
    doc = minute(npu=77.6)
    check('sysctl answering nothing while the memory holds: still held, for the memory, not read',
          doc['running'] == 'standard' and reasons(doc) == [('memory', 'unread', 'memory')], doc['held_for'])
    MACHINE['sysctl'] = True
    doc = minute(npu=77.6, **REST)
    check('sysctl back, everything cool: the quiet curve again', doc['running'] == 'quiet', doc['running'])
    MACHINE['sysctl'] = False
    for _ in range(3):
        doc = minute(npu=77.6)
    check('sysctl answering nothing, nothing holding: the quiet curve, nothing said',
          doc['running'] == 'quiet' and not said and state_of(doc) == {'npu_c': 'cool'}, state_of(doc))
    MACHINE['sysctl'] = True
    doc = minute(npu=77.6, **REST)

    # 7. A reading that stops while it holds: Standard ends the hold, and quiet asked again does not
    #    start one for it.
    doc = minute(npu=77.6, **dict(REST, memory=85.0))
    doc = minute(npu=77.6, **dict(REST, memory=None))
    OPTIONS['board_curve'] = 'standard'
    doc = minute(npu=77.6, **dict(REST, memory=None))
    check('Standard chosen while an unread memory holds: the hold ended', not doc['held'], doc['held_for'])
    OPTIONS['board_curve'] = 'quiet'
    doc = minute(npu=77.6, **dict(REST, memory=None))
    check('quiet asked again, the memory still unread: the quiet curve', doc['running'] == 'quiet'
          and not doc['held'], doc['held_for'])
    doc = minute(npu=77.6, **REST)

    # 8. A coprocessor that does not answer while a reading is hot: the hold said once.
    fresh()
    minute(npu=70.0, **REST)
    MACHINE['answers'] = False
    first_said = minute(npu=70.0, **dict(REST, memory=85.0))
    once = list(said)
    again = minute(npu=70.0, **dict(REST, memory=85.0))
    check('the coprocessor not answering: failed, and the hold said at the first minute only',
          first_said['status'] == 'failed' and again['status'] == 'failed'
          and sum(holds([line]) for line in once) == 1 and not holds(said), (once, said))
    check('...and the page still shows the hold', fanctl.board_curve_status(ENTRY, OPTIONS)['held'])
    MACHINE['answers'] = True
    minute(npu=70.0, **REST)

    # 9. Standard asked: nothing holds, and the coprocessor is asked only to give back what was set.
    OPTIONS['board_curve'] = 'standard'
    doc = minute(npu=90.0, **dict(REST, memory=90.0))
    check('standard asked, everything hot: nothing held, the line given back',
          doc['running'] == 'standard' and not doc['held'] and MACHINE['line'] == 'input', doc)
    doc = minute(npu=90.0, **REST)
    check('standard asked, nothing of the plugin\'s to undo: the coprocessor is not asked',
          MACHINE['asked'] == [] and doc['status'] == 'ok', MACHINE['asked'])
    OPTIONS['board_curve'] = 'quiet'

    # 10. A state written by 1.1.8, held by the coprocessor's heat, is read as a hold of the
    #     coprocessor's: kept while the CPLD is not read, as 1.1.8 kept it, and ended below 78 C.
    fresh()
    fanctl.write_json(fanctl.BOARD_CURVE, {'set': 'standard', 'before': 'input', 'held_by_heat': True})
    check('a 1.1.8 state: the page reads it as held', fanctl.board_curve_status(ENTRY, OPTIONS)['held'])
    doc = minute(npu=None, **REST)
    check('a 1.1.8 hold with the coprocessor unread: still held, for the coprocessor',
          doc['running'] == 'standard' and reasons(doc) == [('npu', 'unread', 'npu_c')], doc['held_for'])
    fresh()
    fanctl.write_json(fanctl.BOARD_CURVE, {'set': 'standard', 'before': 'input', 'held_by_heat': True})
    doc = minute(npu=70.0, **REST)
    stored = fanctl.read_json(fanctl.BOARD_CURVE, {})
    check('a 1.1.8 state with nothing hot: quiet again, and the old key gone',
          doc['running'] == 'quiet' and 'held_by_heat' not in stored and stored.get('held') is False, stored)

    # 11. A table whose figures do not read is refused, and nothing is asked.
    ENTRY_BAD = dict(ENTRY, board_curve=dict(SPEC, phy_cool_c=96))
    fanctl.match_model = lambda table=None, fields=None: (KEY, ENTRY_BAD)
    doc = minute(npu=70.0, **REST)
    check('a cool figure above its hot one: refused, the coprocessor not asked',
          doc['status'] == 'failed' and MACHINE['asked'] == [], doc)
    fanctl.match_model = lambda table=None, fields=None: (KEY, ENTRY)
finally:
    shutil.rmtree(WORK, ignore_errors=True)


# -- The page: which curve runs, and why ------------------------------------------------------------

names = pagejs.page(['board_held_title', 'board_curve_names'], [
    "board_curve_names({enabled: true, setting: 'quiet', set: 'standard', held: true, held_for: ["
    "{name: 'dev.octep.0.temperature.memory', kind: 'memory', why: 'hot'},"
    "{name: 'dev.octep.0.temperature.sfp_phy', kind: 'phy', why: 'unread'},"
    "{name: 'dev.octep.0.temperature.switch', kind: 'phy', why: 'hot'},"
    "{name: 'dev.octep.0.temperature.module_phy', kind: 'phy', why: 'hot'},"
    "{name: 'cpld.npu_c', kind: 'npu', why: 'warm'}]})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'standard', held: true, held_for: []})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'quiet', held: false, held_for: []})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'untouched', held: false})",
    "board_curve_names({enabled: true, setting: 'standard', set: 'standard', held: true})",
    "board_curve_names({enabled: false, setting: 'quiet', set: 'quiet', held: false})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'standard', held: true, held_for: ["
    "{name: 'dev.octep.0.temperature.memory', kind: 'memory', why: 'warm'}]})",
])
if names is None:
    print('--    the page\'s words for the board curve: not run, node is not here')
else:
    check('page: a hold for the memory, a PHY not read, two PHYs hot and the coprocessor not yet below '
          'its cool figure is four reasons, each naming its sensors', names[0] == [
              ['standard curve, held: coprocessor memory hot',
               'dev.octep.0.temperature.memory'],
              ['standard curve, held: a temperature not read',
               'dev.octep.0.temperature.sfp_phy'],
              ['standard curve, held: port PHY hot',
               'dev.octep.0.temperature.switch, dev.octep.0.temperature.module_phy'],
              ['standard curve, held: not yet below its cool figure', 'cpld.npu_c']], names[0])
    check('page: a hold with no reason recorded (1.1.8) is the coprocessor\'s heat',
          names[1] == [['standard curve, held: coprocessor hot', '']], names[1])
    check('page: quiet set and nothing holding is the quiet curve', names[2] == [['quiet curve', '']],
          names[2])
    check('page: quiet asked and not yet set is the standard curve',
          names[3] == [['standard curve', '']], names[3])
    check('page: standard asked is the standard curve, whatever a hold says',
          names[4] == [['standard curve', '']], names[4])
    check('page: the plugin off is the standard curve', names[5] == [['standard curve', '']], names[5])
    check('page: a memory held between its figures is not called hot',
          names[6] == [['standard curve, held: not yet below its cool figure',
                        'dev.octep.0.temperature.memory']], names[6])


print()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
print('all passed')
