#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""The XGS 3300's board curve: where the quiet curve gives way to the standard one, and where it
comes back (fanctl.py board_curve(), since 2026-11-17).

The coprocessor's die was the only reading the rule followed. It follows the coprocessor's memory
and every port PHY os-xgs-npu's driver holds as well, each against the table's figures; a reading
this board has given since it started and no longer gives holds the standard curve until it is read
again, after BOARD_UNREAD_SECONDS; and one never given - a memory module without a sensor, no
expansion module - is not waited for. Checked here: the figures, in the table, the settings help and
the README alike; the rule, minute by minute, with the driver's nodes read through a stand-in for
sysctl(8) and the coprocessor's GPIO line through a stand-in for npu-run.sh; and the page's words
for a hold, run in node (tools/pagejs.py).

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
check('the table\'s figures: coprocessor 85/78, memory 75/68, port PHYs 85/78',
      limits == {'npu': (85.0, 78.0), 'memory': (75.0, 68.0), 'phy': (85.0, 78.0)}, limits)
check('every cool figure is below its hot one',
      bool(limits) and all(cool < hot for hot, cool in limits.values()))
check('the code\'s own figures are the table\'s, for a table that gives none',
      fanctl.board_limits({}) == limits, fanctl.board_limits({}))
check('a cool figure at or above its hot one is refused',
      fanctl.board_limits({'memory_hot_c': 70, 'memory_cool_c': 70}) is None)
check('a figure that is not a number is refused', fanctl.board_limits({'phy_hot_c': 'hot'}) is None)
check('the table says where each figure comes from', all(
    any(text.startswith(kind + ',') for text in SPEC.get('figures') or [])
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
readme = README.read_text(encoding='utf-8')
rows = dict((kind, (float(hot), float(cool))) for kind, hot, cool in re.findall(
    r'^\| (coprocessor|memory|port PHYs) \| (\d+) C \| (\d+) C \|', readme, re.M))
check('the README states the table\'s figures',
      rows == {'coprocessor': limits['npu'], 'memory': limits['memory'], 'port PHYs': limits['phy']}, rows)


# -- The machine, stood in for -----------------------------------------------------------------------

WORK = tempfile.mkdtemp(prefix='fanctl-board-')
fanctl.STATE_DIR = WORK + '/db'
fanctl.RUN_DIR = WORK + '/run'
fanctl.LOG_DIR = WORK + '/log'
fanctl.BOARD_CURVE = fanctl.STATE_DIR + '/board-curve.json'
fanctl.BOARD_SEEN = fanctl.RUN_DIR + '/board-inputs.json'

said = []
fanctl.log = lambda message, level=None: said.append(message)
fanctl.match_model = lambda table=None, fields=None: (KEY, ENTRY)
OPTIONS = {'enabled': True, 'board_curve': 'quiet'}
fanctl.settings = lambda root=None: dict(OPTIONS)

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


def boot():
    """A new boot: RUN_DIR emptied, as cleanvar does, and the line back with the board."""
    shutil.rmtree(fanctl.RUN_DIR, ignore_errors=True)
    MACHINE['line'] = 'input'


def minute(tick, npu=None, **nodes):
    MACHINE['npu'] = npu
    MACHINE['nodes'] = nodes
    MACHINE['asked'] = []
    del said[:]
    return fanctl.board_curve(tick=tick)


def state_of(document):
    return {item['name'].rsplit('.', 1)[-1]: item['state'] for item in document.get('inputs') or []}


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
    boot()
    doc = minute(1000, npu=77.6, **REST)
    check('at rest: the quiet curve, the line driven low, nothing holds',
          doc['running'] == 'quiet' and doc['gpio'] == 'output' and not doc['held']
          and doc['held_for'] == [] and any(c.startswith('gpioset') for c in MACHINE['asked']), doc['running'])
    check('at rest: the coprocessor, its memory and three port PHYs followed, all cool',
          state_of(doc) == {'npu_c': 'cool', 'memory': 'cool', 'switch': 'cool', 'sfp_phy': 'cool',
                            'module_phy': 'cool'}, state_of(doc))

    # 2. The memory reaches its hot figure: the standard curve, the line given back.
    doc = minute(1060, npu=77.6, **dict(REST, memory=75.0))
    check('memory at 75.0: the standard curve, the line given back',
          doc['running'] == 'standard' and doc['held'] and doc['gpio'] == 'input', doc['running'])
    check('memory at 75.0: held for the memory, and said so once',
          [(item['kind'], item['why']) for item in doc['held_for']] == [('memory', 'hot')]
          and len(said) == 2 and 'coprocessor memory reads 75.0 C, at or above 75 C' in said[0],
          said)
    status = fanctl.board_curve_status(ENTRY, OPTIONS)
    check('the page\'s document: held, for dev.octep.0.temperature.memory',
          status['held'] and [item['name'] for item in status['held_for']]
          == ['dev.octep.0.temperature.memory'], status)

    # 3. Below its hot figure and not its cool one: still held. Below the cool one: quiet again.
    doc = minute(1120, npu=77.6, **dict(REST, memory=70.0))
    check('memory at 70.0, between 68 and 75: still the standard curve',
          doc['running'] == 'standard' and doc['held'] and not said, said)
    doc = minute(1180, npu=77.6, **dict(REST, memory=67.9))
    check('memory at 67.9: the quiet curve again, said once',
          doc['running'] == 'quiet' and not doc['held'] and doc['gpio'] == 'output'
          and any('the quiet curve again' in line for line in said), said)

    # 4. The hottest port PHY counts, whichever it is.
    for leaf in ('switch', 'sfp_phy', 'module_phy'):
        doc = minute(1240, npu=77.6, **dict(REST, **{leaf: 85.0}))
        check('%s at 85.0: the standard curve, held for a port PHY' % leaf,
              doc['running'] == 'standard' and [(item['kind'], item['why'], item['name'].rsplit('.', 1)[-1])
                                                for item in doc['held_for']] == [('phy', 'hot', leaf)],
              doc['held_for'])
        doc = minute(1300, npu=77.6, **REST)
        check('%s back at rest: the quiet curve again' % leaf, doc['running'] == 'quiet', doc['running'])
    doc = minute(1360, npu=77.6, **dict(REST, switch=84.9, sfp_phy=84.9, module_phy=84.9))
    check('every port PHY at 84.9, under 85: the quiet curve stays', doc['running'] == 'quiet',
          state_of(doc))
    doc = minute(1420, npu=77.6, **REST)

    # 5. The coprocessor itself, as before.
    doc = minute(1480, npu=85.0, **REST)
    check('the coprocessor at 85.0: the standard curve, held for it',
          doc['running'] == 'standard' and [item['kind'] for item in doc['held_for']] == ['npu'],
          doc['held_for'])
    doc = minute(1540, npu=77.9, **REST)
    check('the coprocessor at 77.9: the quiet curve again', doc['running'] == 'quiet', doc['running'])

    # 6. A reading given this boot that goes empty: waited for BOARD_UNREAD_SECONDS, then held.
    check('a reading empty for 90 s is one the rule cannot see', fanctl.BOARD_UNREAD_SECONDS == 90)
    empty = dict(REST, memory=None)
    doc = minute(1600, npu=77.6, **empty)
    check('memory empty at one check: still quiet, the memory "empty"',
          doc['running'] == 'quiet' and state_of(doc)['memory'] == 'empty', state_of(doc))
    doc = minute(1660, npu=77.6, **empty)
    check('memory empty a minute later: still quiet', doc['running'] == 'quiet', state_of(doc))
    doc = minute(1720, npu=77.6, **empty)
    check('memory empty two minutes: the standard curve, held while it is not read',
          doc['running'] == 'standard' and [(item['kind'], item['why']) for item in doc['held_for']]
          == [('memory', 'unread')] and 'coprocessor memory has not been read for 120 s' in said[0], said)
    doc = minute(1780, npu=77.6, **REST)
    check('memory read again, and cool: the quiet curve again', doc['running'] == 'quiet', doc['running'])

    # 7. A reading empty for seconds between two of the feeder's values is never held for.
    doc = minute(1840, npu=77.6, **dict(REST, sfp_phy=None))
    doc = minute(1900, npu=77.6, **REST)
    check('a PHY empty at one check, back at the next: quiet throughout, nothing said',
          doc['running'] == 'quiet' and not any('standard curve until' in line for line in said), said)

    # 8. Every node gone - the driver's sysctls not answering at all - is every node empty.
    MACHINE['sysctl'] = False
    for tick in (1960, 2020):
        doc = minute(tick, npu=77.6)
    check('sysctl answering nothing: the readings it gave are empty, not forgotten',
          doc['running'] == 'quiet' and set(state_of(doc).values()) == {'cool', 'empty'}, state_of(doc))
    doc = minute(2080, npu=77.6)
    check('sysctl answering nothing for two minutes: the standard curve',
          doc['running'] == 'standard' and len(doc['held_for']) == 4, doc['held_for'])
    MACHINE['sysctl'] = True
    doc = minute(2140, npu=77.6, **REST)
    check('sysctl back, everything cool: the quiet curve again', doc['running'] == 'quiet', doc['running'])

    # 9. A reading never given since the board started is not waited for.
    boot()
    first = {'switch': 46.6, 'sfp_phy': 56.2}
    for tick in (100, 160, 220, 280):
        doc = minute(tick, npu=69.3, **first)
    check('a module without a sensor, no expansion module: quiet, those two never seen',
          doc['running'] == 'quiet' and state_of(doc)['memory'] == 'not seen'
          and state_of(doc)['module_phy'] == 'not seen', state_of(doc))

    # 10. A record from another boot is no record of this one.
    boot()
    minute(5000, npu=70.0, **REST)
    doc = minute(30, npu=70.0, **dict(REST, memory=None))
    check('a record whose uptime runs past this boot\'s is dropped: the empty memory not seen',
          state_of(doc)['memory'] == 'not seen' and doc['running'] == 'quiet', state_of(doc))

    # 11. A coprocessor that does not answer while a reading is hot: the hold said once.
    boot()
    minute(100, npu=70.0, **REST)
    MACHINE['answers'] = False
    first_said = minute(160, npu=70.0, **dict(REST, memory=80.0))
    once = list(said)
    again = minute(220, npu=70.0, **dict(REST, memory=80.0))
    check('the coprocessor not answering: failed, and the hold said at the first minute only',
          first_said['status'] == 'failed' and again['status'] == 'failed'
          and sum('standard curve until' in line for line in once) == 1
          and not any('standard curve until' in line for line in said), (once, said))
    check('...and the page still shows the hold', fanctl.board_curve_status(ENTRY, OPTIONS)['held'])
    MACHINE['answers'] = True
    minute(280, npu=70.0, **REST)

    # 12. Standard asked: nothing holds, and the coprocessor is asked only to give back what was set.
    OPTIONS['board_curve'] = 'standard'
    doc = minute(340, npu=90.0, **dict(REST, memory=90.0))
    check('standard asked, everything hot: nothing held, the line given back',
          doc['running'] == 'standard' and not doc['held'] and MACHINE['line'] == 'input', doc)
    doc = minute(400, npu=90.0, **REST)
    check('standard asked, nothing of the plugin\'s to undo: the coprocessor is not asked',
          MACHINE['asked'] == [] and doc['status'] == 'ok', MACHINE['asked'])
    OPTIONS['board_curve'] = 'quiet'

    # 13. A state written by 1.1.8, held by the coprocessor's heat, is read as a hold.
    boot()
    fanctl.write_json(fanctl.BOARD_CURVE, {'set': 'standard', 'before': 'input', 'held_by_heat': True})
    check('a 1.1.8 state: the page reads it as held', fanctl.board_curve_status(ENTRY, OPTIONS)['held'])
    doc = minute(100, npu=70.0, **REST)
    stored = fanctl.read_json(fanctl.BOARD_CURVE, {})
    check('a 1.1.8 state with nothing hot: quiet again, and the old key gone',
          doc['running'] == 'quiet' and 'held_by_heat' not in stored and stored.get('held') is False, stored)

    # 14. A table whose figures do not read is refused, and nothing is asked.
    ENTRY_BAD = dict(ENTRY, board_curve=dict(SPEC, phy_cool_c=90))
    fanctl.match_model = lambda table=None, fields=None: (KEY, ENTRY_BAD)
    doc = minute(160, npu=70.0, **REST)
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
    "{name: 'dev.octep.0.temperature.module_phy', kind: 'phy', why: 'hot'}]})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'standard', held: true, held_for: []})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'quiet', held: false, held_for: []})",
    "board_curve_names({enabled: true, setting: 'quiet', set: 'untouched', held: false})",
    "board_curve_names({enabled: true, setting: 'standard', set: 'standard', held: true})",
    "board_curve_names({enabled: false, setting: 'quiet', set: 'quiet', held: false})",
])
if names is None:
    print('--    the page\'s words for the board curve: not run, node is not here')
else:
    check('page: a hold for the memory, a PHY not read and two PHYs hot is three reasons, each '
          'naming its sensors', names[0] == [
              ['the standard curve, held while the coprocessor memory is hot',
               'dev.octep.0.temperature.memory'],
              ['the standard curve, held while a temperature it follows is not read',
               'dev.octep.0.temperature.sfp_phy'],
              ['the standard curve, held while a port PHY is hot',
               'dev.octep.0.temperature.switch, dev.octep.0.temperature.module_phy']], names[0])
    check('page: a hold with no reason recorded (1.1.8) is the coprocessor\'s heat',
          names[1] == [['the standard curve, held while the coprocessor is hot', '']], names[1])
    check('page: quiet set and nothing holding is the quiet curve', names[2] == [['the quiet curve', '']],
          names[2])
    check('page: quiet asked and not yet set is the standard curve',
          names[3] == [['the standard curve', '']], names[3])
    check('page: standard asked is the standard curve, whatever a hold says',
          names[4] == [['the standard curve', '']], names[4])
    check('page: the plugin off is the standard curve', names[5] == [['the standard curve', '']], names[5])


print()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
print('all passed')
