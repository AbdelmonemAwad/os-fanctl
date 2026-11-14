#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""The sensor groups an owner can choose: the same list in sensors.py, the model and the page, and
a choice of any of them followed as written by the contract, the daemon's loop and
`fanctl.py decide`.

os-fanctl #57 made npu and inlet groups a machine follows when the setting is left as shipped (the
XGS 136 and XGS 3300 entries in fans.json name them for their fans), while the model's mask still
refused them, so an owner could not choose by name what the plugin chose for them. This holds the
three lists together and checks that an explicit npu or inlet is taken as a choice: followed when
the machine reads it, and reported missing - the fans going back to the hardware - when it does
not, as for any other chosen group.

Synthetic readings only: nothing here reads a sysctl, runs kenv, opens a port or writes a file.

    python3 tools/test-sensor-groups.py"""

import contextlib
import io
import json
import pathlib
import re
import sys
import types
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'src' / 'opnsense' / 'scripts' / 'fanctl'
MODEL = ROOT / 'src' / 'opnsense' / 'mvc' / 'app' / 'models' / 'OPNsense' / 'FanCtl' / 'FanCtl.xml'
PAGE = ROOT / 'src' / 'opnsense' / 'mvc' / 'app' / 'views' / 'OPNsense' / 'FanCtl' / 'index.volt'
CATALOGUE = SCRIPTS / 'i18n' / 'ui' / 'source.json'

# fcntl and syslog are on FreeBSD and on the runner; on a Windows checkout they are not, and
# fanctl.py imports both and uses syslog's constants as default arguments. Nothing below reaches
# either.
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
import sensors                                  # noqa: E402


def refuse(name):
    def call(*args, **kwargs):
        raise AssertionError('%s was reached; this test touches nothing' % name)
    return call


for name in ('ensure_dirs', 'write_json', 'log', 'restore', 'apply_duty', 'mark_held',
             'clear_held', 'unmark_held', 'capture_baseline'):
    if hasattr(fanctl, name):
        setattr(fanctl, name, refuse('fanctl.%s' % name))
sensors._run = refuse('sensors._run')
fanctl.read_json = lambda path, default=None: default

failures = []


def check(label, condition, shown=''):
    print('%s  %s%s' % ('ok  ' if condition else 'FAIL', label, ('  [%s]' % (shown,)) if shown else ''))
    if not condition:
        failures.append(label)


def sensor(name, group, celsius, source='sysctl'):
    return {'key': '%s:%s' % (source, name), 'name': name, 'group': group, 'kind': group,
            'source': source, 'celsius': celsius, 'named_by_driver': True,
            'confidence': 'named' if celsius is not None else 'unknown'}


def reading(*items):
    groups = {}
    for item in items:
        if item['celsius'] is not None:
            groups[item['group']] = max(groups.get(item['group'], -999.0), item['celsius'])
    return {'at': 0.0, 'sensors': list(items), 'groups': groups,
            'present': [g for g in sensors.GROUPS if g in groups],
            'unreadable': [i['name'] for i in items if i['celsius'] is None],
            'unreadable_why': [], 'constant': []}


def options_for(text):
    """settings() over a config.xml holding only this sensors value, as the daemon reads one."""
    root = ET.fromstring('<opnsense><OPNsense><FanCtl><general/></FanCtl></OPNsense></opnsense>')
    ET.SubElement(root.find(fanctl.GENERAL), 'sensors').text = text
    return fanctl.settings(root)


# The XGS 3300 as the daemon reads it: the CPLD's three readings and the port chips.
R3300 = reading(
    sensor('cpld.cpu_c', 'cpu', 61.5, 'cpld'),
    sensor('cpld.npu_c', 'npu', 76.5, 'cpld'),
    sensor('cpld.inlet_c', 'inlet', 33.0, 'cpld'),
    sensor('dev.octep.0.temperature.switch', 'nic', 55.0),
)
# The XGS 136: the processor, the coprocessor's zones over its link; no inlet sensor at all.
R136 = reading(
    sensor('dev.amdtemp.0.core0.sensor0', 'cpu', 58.0),
    sensor('npu.zone:ap-thermal-ic', 'npu', 66.0, 'npu'),
)
# The XG 330 rev 2: no coprocessor and no inlet sensor.
R330 = reading(sensor('dev.cpu.0.temperature', 'cpu', 54.0),
               sensor('dev.pchtherm.0.temperature', 'pch', 46.0))

FIELDS = {
    'xgs3300': {'maker': 'Sophos', 'product': 'XGS', 'version': '3300r1',
                'serial': 'AMDA0202-0004', 'board': 'XGS 17'},
    'xgs136': {'maker': 'Sophos', 'product': 'XGS', 'version': '136r1',
               'serial': 'AMDA0201-0003', 'board': 'XGS 08'},
}
TABLE = fanctl.load_table()


# -- One list in three places ---------------------------------------------------------------

model = ET.parse(str(MODEL)).getroot()
mask = model.find('.//sensors/Mask').text
masked = re.fullmatch(r'/\^\(([a-z|]+)\)\$/', mask)
check('the model\'s sensors mask is one alternation of names', masked is not None, mask)
masked = masked.group(1).split('|') if masked else []
check('the model\'s mask is the whole of sensors.GROUPS, in its order',
      masked == list(sensors.GROUPS), masked)
check('npu and inlet pass the model\'s mask',
      all(re.fullmatch(mask.strip('/'), group) for group in ('npu', 'inlet')))

default = model.find('.//sensors/Default').text
check('the model\'s default is fanctl.MODEL_SENSORS', default == fanctl.MODEL_SENSORS, default)

page = PAGE.read_text(encoding='utf-8')
block = re.search(r'const sensor_title = \{(.*?)\};', page, re.S)
titles = dict(re.findall(r"'([a-z]+)':\s*\"\{\{ lang\._\('([^']+)'\) \}\}\"",
                         block.group(1) if block else ''))
check('the page names npu and inlet', 'npu' in titles and 'inlet' in titles, titles)
check('every group the page names is in sensors.GROUPS and the mask',
      titles and all(key in sensors.GROUPS and key in masked for key in titles), sorted(titles))
catalogue = set(json.loads(CATALOGUE.read_text(encoding='utf-8')))
check('every group title on the page is in the string catalogue',
      all(title in catalogue for title in titles.values()),
      [title for title in titles.values() if title not in catalogue])
check('no group title begins with "The"',
      not any(title.lower().startswith('the ') for title in titles.values()))


# -- A choice of npu or inlet is a choice -----------------------------------------------------

for text, groups in (('npu', ['npu']), ('inlet', ['inlet']), ('npu,inlet', ['npu', 'inlet']),
                     ('cpu,npu,inlet', ['cpu', 'npu', 'inlet']), (' NPU , Inlet ', ['npu', 'inlet'])):
    options = options_for(text)
    check('settings: %r is the owner\'s choice of %s' % (text, ','.join(groups)),
          options['sensors_chosen'] and sensors.groups_from(options['sensors']) == groups,
          options['sensors'])

entry3300 = fanctl.match_model(TABLE, FIELDS['xgs3300'])[1]
entry136 = fanctl.match_model(TABLE, FIELDS['xgs136'])[1]
entry330 = {}

cases = (
    ('3300 chose npu: followed, 76.5 from the CPLD', 'npu', entry3300, R3300,
     True, ['npu'], '76.5 C, read from cpld.npu_c.'),
    ('3300 chose inlet: followed, 33.0 from the CPLD', 'inlet', entry3300, R3300,
     True, ['inlet'], '33.0 C, read from cpld.inlet_c.'),
    ('3300 chose npu,inlet: the hottest of the two', 'npu,inlet', entry3300, R3300,
     True, ['npu', 'inlet'], '76.5 C, read from cpld.npu_c.'),
    ('136 chose npu: followed from the coprocessor\'s zone', 'npu', entry136, R136,
     True, ['npu'], '66.0 C, read from npu.zone:ap-thermal-ic.'),
    ('136 chose inlet, which it has no sensor of: missing, not left out', 'inlet', entry136, R136,
     False, ['inlet'], 'a temperature the decision needs is not being read: inlet.'),
    ('XG 330 chose cpu,npu: npu missing, the fans go back', 'cpu,npu', entry330, R330,
     False, ['cpu', 'npu'], 'a temperature the decision needs is not being read: npu.'),
)
for label, text, entry, read, ok, groups, detail in cases:
    fanctl._groups_seen.clear()
    line = fanctl.sensors_line(options_for(text), entry, read)
    check(label, line['ok'] is ok and line['chosen'] and line['named_by'] == 'owner'
          and line['groups'] == groups and line['left_out'] == [] and line['detail'] == detail,
          line['detail'])

# The daemon's loop: a channel the table names no sensors for follows the groups the sensors line
# judged (daemon(), `followed`), and a chosen npu is those groups, decided on as the line did.
fanctl._groups_seen.clear()
line = fanctl.sensors_line(options_for('npu'), entry3300, R3300)
celsius, names, missing = sensors.decide(R3300, line['groups'])
check('the loop decides a chosen npu from cpld.npu_c, nothing missing',
      celsius == 76.5 and names == ['cpld.npu_c'] and missing == [], (celsius, names, missing))


# -- fanctl.py decide takes them too ----------------------------------------------------------

current = {'read': R3300, 'fields': FIELDS['xgs3300']}
fanctl.sensors.read = lambda **kwargs: current['read']
fanctl.smbios = lambda: dict(current['fields'])
fanctl.load_table = lambda *args, **kwargs: TABLE
real_match = fanctl.match_model
fanctl.match_model = lambda table=None, fields=None: real_match(table, current['fields'])


def run_decide(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fanctl.main(['fanctl.py', 'decide'] + list(args))
    return rc, json.loads(out.getvalue())


for args, read, fields, ok, groups in (
        (('npu,inlet',), R3300, FIELDS['xgs3300'], True, ['npu', 'inlet']),
        (('inlet',), R3300, FIELDS['xgs3300'], True, ['inlet']),
        (('--chosen', 'npu'), R136, FIELDS['xgs136'], True, ['npu']),
        (('inlet',), R136, FIELDS['xgs136'], False, ['inlet'])):
    current.update(read=read, fields=fields)
    fanctl._groups_seen.clear()
    rc, doc = run_decide(*args)
    line = doc.get('line') or {}
    check('decide %s on %s: rc 0, chosen, %s, ok=%s' % (' '.join(args), fields['version'],
                                                        ','.join(groups), ok),
          rc == 0 and doc['status'] == 'ok' and line.get('chosen') is True
          and line.get('groups') == groups and line.get('ok') is ok, line.get('detail'))


print()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
print('all passed')
