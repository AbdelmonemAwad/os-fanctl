#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""
os-fanctl #57 - the sensors setting left as shipped follows the matched table entry, without
the groups a host can truly lack (pch, acpi, and board in the daemon's view) when no sensor of
them is read; any other group it names and has not read yet is missing until it has been absent
long enough (sensors.ABSENT_READINGS, ABSENT_SECONDS); a chosen setting is followed as before.

The default path, which tools/test-sensor-groups.py does not take: left_out, MODEL_SENSORS, the
daemon's GroupWatch, the groups the table names, and `fanctl.py decide` without --chosen.

Synthetic readings only, modelled on the sensor names the plugin builds on each appliance
(sensors.py _CLASSIFY, read_cpld(), read_npu(), read_superio()). Nothing here reads a sysctl,
runs kenv, opens a port or writes a file: every reader and writer fanctl.py has is replaced
before anything runs, and one that is reached anyway fails the run.

    python3 tools/test-default-sensors.py [directory holding fanctl.py, sensors.py, fans.json]

Exit 0 when every case holds; each case prints one line.
"""

import contextlib
import io
import json
import os
import sys
import types
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = (os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else
           os.path.join(HERE, '..', 'src', 'opnsense', 'scripts', 'fanctl'))

# fcntl and syslog do not exist on a Windows checkout; fanctl.py imports both at the top and uses syslog's
# constants as default arguments. Stubbed only where they are missing - on FreeBSD the real ones
# load, and nothing below reaches either.
for missing in ('fcntl', 'syslog'):
    try:
        __import__(missing)
    except ImportError:
        stub = types.ModuleType(missing)
        for name in ('LOG_NOTICE', 'LOG_WARNING', 'LOG_ERR', 'LOG_CRIT', 'LOG_INFO', 'LOG_PID',
                     'LOG_DAEMON', 'LOCK_EX', 'LOCK_NB', 'LOCK_UN'):
            setattr(stub, name, 0)
        sys.modules[missing] = stub

sys.path.insert(0, SCRIPTS)
import fanctl                                   # noqa: E402
import sensors                                  # noqa: E402


def refuse(name):
    def call(*args, **kwargs):
        raise AssertionError('%s was reached; this test touches nothing' % name)
    return call


# Every way fanctl.py has of touching the machine, shut before the first case.
for name in ('ensure_dirs', 'write_json', 'log', 'restore', 'apply_duty', 'mark_held',
             'clear_held', 'unmark_held', 'capture_baseline'):
    setattr(fanctl, name, refuse('fanctl.%s' % name))
sensors._run = refuse('sensors._run')
fanctl.read_json = lambda path, default=None: default

TABLE = fanctl.load_table()

FIELDS = {
    'xgs136': {'maker': 'Sophos', 'product': 'XGS', 'version': '136r1',
               'serial': 'AMDA0201-0003', 'board': 'XGS 08'},
    'xgs3300': {'maker': 'Sophos', 'product': 'XGS', 'version': '3300r1',
                'serial': 'AMDA0202-0004', 'board': 'XGS 17'},
    'xg330': {'maker': 'Sophos', 'product': 'XG', 'version': '330r2', 'serial': '',
              'board': 'XG 330r2'},
    'xgs116': {'maker': 'Sophos', 'product': 'XGS', 'version': '116r1',
               'serial': 'AMDA0201-0001', 'board': 'XGS 08'},
    'other': {'maker': 'HP', 'product': '8A51', 'version': 'KBC', 'serial': '', 'board': ''},
}


def sensor(name, group, celsius, source='sysctl'):
    return {'key': '%s:%s' % (source, name), 'name': name, 'group': group, 'kind': group,
            'source': source, 'celsius': celsius, 'named_by_driver': True,
            'confidence': 'named' if celsius is not None else 'unknown'}


def reading(*items, at=0.0):
    groups = {}
    for item in items:
        if item['celsius'] is not None:
            groups[item['group']] = max(groups.get(item['group'], -999.0), item['celsius'])
    return {'at': at, 'sensors': list(items), 'groups': groups,
            'present': [g for g in sensors.GROUPS if g in groups],
            'unreadable': [i['name'] for i in items if i['celsius'] is None],
            'unreadable_why': [], 'constant': []}


# The XGS 136 as the daemon reads it: amdtemp's processor reading, the coprocessor's zones over
# its link (read_npu), its port PHYs; no pchtherm, and the super-I/O inputs only on the page.
R136 = reading(
    sensor('dev.amdtemp.0.core0.sensor0', 'cpu', 58.0),
    sensor('npu.zone:ap-thermal-ic', 'npu', 66.0, 'npu'),
    sensor('npu.zone:ap-thermal-cpu0', 'npu', 65.0, 'npu'),
    sensor('npu.phy:1', 'nic', 51.0, 'npu'),
)
R136_PAGE = reading(*(R136['sensors'] + [sensor('superio SYSTIN', 'board', 41.0, 'superio')]))
# The same machine with amdtemp's node there and not answering this time.
R136_CPU_SILENT = reading(sensor('dev.amdtemp.0.core0.sensor0', 'cpu', None),
                          *R136['sensors'][1:])
# And with the coprocessor gone from the reading altogether: its link down and its copy empty.
R136_NO_NPU = reading(sensor('dev.amdtemp.0.core0.sensor0', 'cpu', 58.0))

# The XGS 3300: the CPLD's three readings, the port chips through octep, no pchtherm.
R3300 = reading(
    sensor('cpld.cpu_c', 'cpu', 61.5, 'cpld'),
    sensor('cpld.npu_c', 'npu', 76.5, 'cpld'),
    sensor('cpld.inlet_c', 'inlet', 33.0, 'cpld'),
    sensor('dev.octep.0.temperature.switch', 'nic', 55.0),
)

# The XG 330 rev 2, the reference appliance: coretemp, pchtherm, two ACPI zones.
R330 = reading(
    sensor('dev.cpu.0.temperature', 'cpu', 54.0), sensor('dev.cpu.1.temperature', 'cpu', 51.0),
    sensor('dev.pchtherm.0.temperature', 'pch', 46.0),
    sensor('hw.acpi.thermal.tz0.temperature', 'acpi', 27.9),
)
R_AMD_ONLY = reading(sensor('dev.amdtemp.0.core0.sensor0', 'cpu', 49.0))
R_NOTHING = reading()

failures = []


def check(label, condition, shown=''):
    print('%s  %s%s' % ('ok  ' if condition else 'FAIL', label, ('  [%s]' % shown) if shown else ''))
    if not condition:
        failures.append(label)


def options_for(text, chosen_flag=None):
    """settings() over a config.xml holding only this sensors value (None: no such element)."""
    root = ET.fromstring('<opnsense><OPNsense><FanCtl><general/></FanCtl></OPNsense></opnsense>')
    if text is not None:
        ET.SubElement(root.find(fanctl.GENERAL), 'sensors').text = text
    options = fanctl.settings(root)
    if chosen_flag is not None:
        options['sensors_chosen'] = chosen_flag
    return options


def entry_for(machine):
    return fanctl.match_model(TABLE, FIELDS[machine])


def line_for(machine, text, read, chosen_flag=None, fresh=True):
    if fresh:
        fanctl._groups_seen.clear()
    key, entry = entry_for(machine)
    return key, fanctl.sensors_line(options_for(text, chosen_flag), entry, read)


def old_line(options, read):
    """The sensors line exactly as contract() built it before this change."""
    wanted = sensors.groups_from(options['sensors'])
    celsius, names, missing = sensors.decide(read, wanted)
    return {
        'name': 'sensors',
        'ok': celsius is not None and not missing,
        'detail': ('%.1f C, read from %s.' % (celsius, ', '.join(names)))
                  if celsius is not None and not missing else
                  'a temperature the decision needs is not being read: %s.'
                  % (', '.join(missing) or 'none of the chosen sensors answered'),
    }


# 1. What counts as a choice.
for text, expect in ((None, False), ('cpu,pch', False), (' CPU , pch ', False), ('', False),
                     ('pch,cpu', True), ('cpu', True), ('cpu,pch,acpi', True),
                     ('cpu,pch,typo', True), ('cpu,board,npu', True)):
    options = options_for(text)
    check('settings: sensors %r is %s' % (text, 'a choice' if expect else 'as shipped'),
          options['sensors_chosen'] is expect, options['sensors'])
check('settings: no FanCtl node at all is as shipped',
      fanctl.settings(ET.fromstring('<opnsense/>'))['sensors_chosen'] is False)

# 2. The model each machine matches, from the real fans.json.
for machine, expect in (('xgs136', 'sophos-xgs-136'), ('xgs3300', 'sophos-xgs-3300'),
                        ('xg330', 'sophos-xg-330r2'), ('xgs116', 'sophos-xg-family'),
                        ('other', 'generic')):
    check('match: %s -> %s' % (machine, expect), entry_for(machine)[0] == expect)

# 3. os-fanctl #57 itself: the XGS 136 with the shipped cpu,pch.
key, line = line_for('xgs136', 'cpu,pch', R136)
check('136 shipped cpu,pch passes (the daemon reading)', line['ok'] and not line['missing'],
      line['detail'])
check('136 follows cpu, npu from the table; board left out (not read in the loop)',
      line['groups'] == ['cpu', 'npu'] and line['left_out'] == ['board']
      and line['named_by'] == 'table' and line['chosen'] is False, line['groups'])
key, line = line_for('xgs136', 'cpu,pch', R136_PAGE)
check('136 shipped, page reading with the super-I/O: cpu, board, npu, nothing missing',
      line['ok'] and line['groups'] == ['cpu', 'board', 'npu'] and line['left_out'] == [],
      line['detail'])
key, line = line_for('xgs136', None, R136)
check('136 with no sensors in config.xml (cpu,pch,acpi from the table defaults) passes too',
      line['ok'] and line['groups'] == ['cpu', 'npu'], line['detail'])
key, line = line_for('xgs136', 'cpu,pch', R136, chosen_flag=True)
check('136 cpu,pch taken as a choice (--chosen) answers as before: pch missing',
      not line['ok'] and line['missing'] == ['pch']
      and line['detail'] == 'a temperature the decision needs is not being read: pch.',
      line['detail'])
key, line = line_for('xgs136', 'cpu,board,npu', R136)
check('136 cpu,board,npu chosen: board missing in the loop, as any choice',
      not line['ok'] and line['missing'] == ['board'] and line['chosen'], line['detail'])
key, line = line_for('xgs136', 'cpu,pch', R136_CPU_SILENT)
check('136 shipped, amdtemp present and silent: cpu stays and is missing',
      not line['ok'] and line['missing'] == ['cpu'] and 'cpu' in line['groups'], line['detail'])
key, line = line_for('xgs136', 'cpu,pch', R136_NO_NPU)
check('136 shipped, one-shot, no coprocessor reading at all: npu, named by the table, missing',
      not line['ok'] and line['groups'] == ['cpu', 'npu'] and line['left_out'] == ['board']
      and line['missing'] == ['npu'], line['detail'])

# 4. The daemon keeps what it has seen: a coprocessor that drops out of the reading is missing.
key, line = line_for('xgs136', 'cpu,pch', R136)
key, line = line_for('xgs136', 'cpu,pch', R136_NO_NPU, fresh=False)
check('136 daemon: npu seen, then gone from the reading -> missing, the line fails',
      not line['ok'] and line['missing'] == ['npu'], line['detail'])
key, line = line_for('xgs136', 'cpu,pch', R136, fresh=False)
check('136 daemon: npu back -> the line holds again', line['ok'], line['detail'])

# 4b. A table-named group that has not appeared yet - the coprocessor's link coming up after the
# daemon started - is missing, not left out, until it has been absent from ABSENT_READINGS
# readings over ABSENT_SECONDS; each reading counted once, by its time.
N, SECS = sensors.ABSENT_READINGS, sensors.ABSENT_SECONDS
check('the absence rule: 30 readings and 300 s', (N, SECS) == (30, 300.0), repr((N, SECS)))
check('only pch, acpi and board may be left out at once',
      sensors.MAY_BE_ABSENT == ('pch', 'acpi', 'board'), repr(sensors.MAY_BE_ABSENT))
fanctl._groups_seen.clear()
lines_late = [line_for('xgs136', 'cpu,pch', reading(*R136_NO_NPU['sensors'], at=1000.0 + 10 * n),
                       fresh=False)[1] for n in range(N - 1)]
check('136 daemon, npu not yet read: missing at every one of the first %d readings' % (N - 1),
      all(not line['ok'] and line['missing'] == ['npu'] and line['left_out'] == ['board']
          for line in lines_late), lines_late[-1]['detail'])
again = [line_for('xgs136', 'cpu,pch', reading(*R136_NO_NPU['sensors'], at=1000.0 + 10 * (N - 2)),
                  fresh=False)[1] for _ in range(5)]
check('the same reading judged again is not counted again: npu still missing',
      all(line['missing'] == ['npu'] for line in again), again[-1]['detail'])
key, line = line_for('xgs136', 'cpu,pch', R136, fresh=False)
check('136 daemon: npu arrives late -> followed, the line holds',
      line['ok'] and line['groups'] == ['cpu', 'npu'], line['detail'])
fanctl._groups_seen.clear()
for n in range(N - 1):
    line_for('xgs136', 'cpu,pch', reading(*R136_NO_NPU['sensors'], at=1000.0 + 10 * n), fresh=False)
key, line = line_for('xgs136', 'cpu,pch', reading(*R136_NO_NPU['sensors'], at=1000.0 + 10 * (N - 1)),
                     fresh=False)
check('136 daemon: npu never read, %d readings but under %d s -> still missing' % (N, SECS),
      not line['ok'] and line['missing'] == ['npu'], line['detail'])
key, line = line_for('xgs136', 'cpu,pch', reading(*R136_NO_NPU['sensors'], at=1000.0 + SECS),
                     fresh=False)
check('136 daemon: npu never read, absent %d readings over %d s -> left out, the line holds'
      % (N + 1, SECS),
      line['ok'] and line['groups'] == ['cpu'] and line['left_out'] == ['board', 'npu'],
      line['detail'])
fanctl._groups_seen.clear()
for n in range(N + 5):
    key, line = line_for('xgs136', 'cpu,pch', reading(*R136['sensors'], at=1000.0 + 10 * n),
                         fresh=False)
key, line = line_for('xgs136', 'cpu,pch', reading(*R136_NO_NPU['sensors'], at=99999.0), fresh=False)
check('136 daemon: npu seen once is never left out - gone is missing, however long',
      not line['ok'] and line['missing'] == ['npu'], line['detail'])
fanctl._groups_seen.clear()
key, line = line_for('xg330', None, reading(sensor('dev.cpu.0.temperature', 'cpu', 50.0), at=5.0),
                     fresh=False)
check('pch and acpi absent: left out at the first reading, nothing missing',
      line['ok'] and line['groups'] == ['cpu'] and line['left_out'] == ['pch'], line['detail'])

# 5. The XGS 3300.
key, line = line_for('xgs3300', 'cpu,pch', R3300)
check('3300 shipped cpu,pch passes from npu, cpu, inlet',
      line['ok'] and line['groups'] == ['npu', 'cpu', 'inlet'] and line['left_out'] == [],
      line['detail'])
check('3300 decides on the hottest: the NPU at 76.5', line['detail'].startswith('76.5 C'),
      line['detail'])
key, line = line_for('xgs3300', 'cpu,pch', R3300, chosen_flag=True)
check('3300 cpu,pch as a choice answers as before: pch missing',
      not line['ok'] and line['missing'] == ['pch'], line['detail'])

# 6. The reference appliance does not change.
key, line = line_for('xg330', 'cpu,pch', R330)
old = old_line(options_for('cpu,pch'), R330)
check('XG 330 shipped cpu,pch: cpu, pch from its table entry, same verdict and number as before',
      line['groups'] == ['cpu', 'pch'] and line['ok'] == old['ok']
      and line['detail'].startswith(old['detail']), line['detail'])

# 7. Machines with no fans named in the table follow the setting's own groups that are read.
key, line = line_for('xgs116', 'cpu,pch', R_AMD_ONLY)
check('XGS 116 (family entry, no fans): cpu, pch left out',
      line['ok'] and line['groups'] == ['cpu'] and line['left_out'] == ['pch']
      and line['named_by'] == 'setting', line['detail'])
key, line = line_for('other', None, R_AMD_ONLY)
check('unknown AMD machine, no config.xml: cpu; pch and acpi left out',
      line['ok'] and line['groups'] == ['cpu'] and line['left_out'] == ['pch', 'acpi'],
      line['detail'])
key, line = line_for('other', 'cpu,pch', R_NOTHING)
check('a machine with no sensor at all still fails the line: cpu missing, pch left out',
      not line['ok'] and line['groups'] == ['cpu'] and line['left_out'] == ['pch']
      and line['missing'] == ['cpu']
      and line['detail'].startswith('a temperature the decision needs is not being read: cpu.'),
      line['detail'])

# 8. A choice is followed exactly as before, on every machine and reading here.
same = True
for machine in FIELDS:
    for read in (R136, R136_PAGE, R136_CPU_SILENT, R136_NO_NPU, R3300, R330, R_AMD_ONLY,
                 R_NOTHING):
        for text in ('cpu', 'pch,cpu', 'cpu,pch,acpi', 'cpu,board,npu', 'npu', 'acpi,nvme'):
            fanctl._groups_seen.clear()
            options = options_for(text)
            new = fanctl.sensors_line(options, entry_for(machine)[1], read)
            old = old_line(options, read)
            if (new['ok'], new['detail']) != (old['ok'], old['detail']):
                same = False
                print('   differs: %s %s %r -> %r' % (machine, text, old, new))
check('every chosen setting: ok and detail identical to the code before (%d cases)'
      % (len(FIELDS) * 8 * 6), same)

# 9. contract() carries the line, with the groups the daemon's channel loop follows.
fanctl._groups_seen.clear()
key, entry = entry_for('xgs136')
found = {'chip': 'NCT6779D', 'writable': False, 'channels': [], 'tach': {}, 'problem': ''}
lines = fanctl.contract(options_for('cpu,pch'), entry, found, {}, R136, [])
check('contract(): six lines, the last the sensors line with its groups',
      len(lines) == 6 and lines[-1]['name'] == 'sensors' and lines[-1]['groups'] == ['cpu', 'npu'],
      [line['name'] for line in lines])

# 9b. A 3300 whose CPLD copy is not filled yet: npu and inlet missing, cpu read.
fanctl._groups_seen.clear()
key, line = line_for('xgs3300', 'cpu,pch', reading(sensor('dev.octep.0.temperature.switch', 'nic', 55.0)))
check('3300 one-shot, the CPLD copy not filled yet: npu, cpu, inlet all missing',
      not line['ok'] and line['missing'] == ['npu', 'cpu', 'inlet'] and line['left_out'] == [],
      line['detail'])

# 10. The verb, end to end, with the machine's readers replaced.
calls = []


def fake_read(include_drives=False, include_superio=False, **kwargs):
    calls.append(include_superio)
    return R136_PAGE if include_superio else R136


fanctl.sensors.read = fake_read
fanctl.smbios = lambda: dict(FIELDS['xgs136'])


def run(*args):
    fanctl._groups_seen.clear()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fanctl.main(['fanctl.py', 'decide'] + list(args))
    text = out.getvalue()
    return rc, (json.loads(text) if text.strip().startswith('{') else text)


rc, doc = run('cpu,pch')
check('decide cpu,pch: rc 0, ok, model sophos-xgs-136, cpu+npu, board left out, no superio read',
      rc == 0 and doc['line']['ok'] and doc['machine']['model'] == 'sophos-xgs-136'
      and doc['line']['groups'] == ['cpu', 'npu'] and calls[-1] is False, doc['line']['detail'])
rc, doc = run('--superio', 'cpu,pch')
check('decide --superio cpu,pch: cpu, board, npu, nothing missing',
      rc == 0 and doc['line']['ok'] and doc['line']['groups'] == ['cpu', 'board', 'npu']
      and calls[-1] is True, doc['line']['detail'])
rc, doc = run('--chosen', 'cpu,pch')
check('decide --chosen cpu,pch: the answer before the change - pch missing',
      rc == 0 and not doc['line']['ok'] and doc['line']['missing'] == ['pch'],
      doc['line']['detail'])
rc, doc = run()
check('decide with no GROUPS: the table default cpu,pch,acpi, as shipped',
      rc == 0 and doc['setting'] == 'cpu,pch,acpi' and doc['line']['chosen'] is False
      and doc['line']['ok'], doc['line']['detail'])
rc, doc = run('--bogus')
check('decide --bogus: usage, rc 2', rc == 2 and doc['status'] == 'failed', doc)
rc, doc = run('cpu', 'pch')
check('decide with two GROUPS words: usage, rc 2', rc == 2, doc)

print('')
print('%d failed' % len(failures) if failures else 'all passed')
sys.exit(1 if failures else 0)
