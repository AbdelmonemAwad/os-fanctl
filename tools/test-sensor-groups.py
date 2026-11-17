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

dimm and nic - a memory module's own sensor, and a network chip's, a port's PHY among them - were in
the mask and nowhere else: the page did not offer them and sensors.suggested_groups() said they were
never suggested. They are suggested now where a machine reports them, offered by the page there and
only there, followed as written when chosen and missing when chosen and not read, and a default
nowhere.

Synthetic readings only: nothing here reads a sysctl, runs kenv, opens a port or writes a file. The
page's offer is run in node (tools/pagejs.py); without node that one check says it was not run.

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
import pagejs                                   # noqa: E402
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
check('the page names dimm and nic', 'dimm' in titles and 'nic' in titles, sorted(titles))
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


# -- dimm and nic: suggested where found, followed when chosen, a default nowhere -------------

def held(leaf, group, celsius):
    """One of os-xgs-npu's held nodes, as sensors.py classifies it: .memory a memory module, the
    switch and the two PHYs port PHYs in the nic group."""
    item = sensor('dev.octep.0.temperature.%s' % leaf, group, celsius)
    item['kind'] = 'dimm' if group == 'dimm' else 'phy'
    return item


# The second XGS 3300 as p0 read it on 2026-11-17: its coprocessor memory has a sensor, and the
# switch, the SFP+ cages' PHY and the 4x10G module's PHY are all read.
R3300_FULL = reading(
    sensor('cpld.cpu_c', 'cpu', 64.1, 'cpld'),
    sensor('cpld.npu_c', 'npu', 77.6, 'cpld'),
    sensor('cpld.inlet_c', 'inlet', 32.1, 'cpld'),
    held('memory', 'dimm', 52.6),
    held('switch', 'nic', 61.1),
    held('sfp_phy', 'nic', 63.2),
    held('module_phy', 'nic', 49.1),
)
# The first XGS 3300: a memory module without a sensor - its empty node is left out of the reading
# (sensors.py _HELD) - and no expansion module.
R3300_FIRST = reading(
    sensor('cpld.cpu_c', 'cpu', 56.2, 'cpld'),
    sensor('cpld.npu_c', 'npu', 69.3, 'cpld'),
    sensor('cpld.inlet_c', 'inlet', 27.0, 'cpld'),
    held('switch', 'nic', 46.6),
    held('sfp_phy', 'nic', 56.2),
)
# The XGS 136 with the PHYs read_npu() reads over the coprocessor's link.
phy1 = sensor('npu.phy:1', 'nic', 55.0, 'npu')
phy1['kind'] = 'phy'
R136_PHY = reading(*(R136['sensors'] + [phy1]))
# A host with jedec_dimm's hints set and a NIC that publishes its die (ix).
R_HOST = reading(sensor('dev.cpu.0.temperature', 'cpu', 50.0),
                 sensor('dev.pchtherm.0.temperature', 'pch', 45.0),
                 sensor('dev.jedec_dimm.0.temp', 'dimm', 40.0),
                 sensor('dev.ix.0.temp', 'nic', 55.0))

for label, read, groups in (
        ('second 3300: cpu, then its memory, then its port PHYs', R3300_FULL, ['cpu', 'dimm', 'nic']),
        ('first 3300, no memory sensor: cpu and the port PHYs', R3300_FIRST, ['cpu', 'nic']),
        ('XGS 136: cpu and the PHYs read over its link', R136_PHY, ['cpu', 'nic']),
        ('XG 330: cpu, pch and nothing else', R330, ['cpu', 'pch']),
        ('a host with jedec_dimm and ix: after cpu and pch, in that order', R_HOST,
         ['cpu', 'pch', 'dimm', 'nic'])):
    suggested = sensors.suggested_groups(read)
    check('suggested, %s' % label, [group for group, _ in suggested] == groups, suggested)
check('dimm and nic come with their own reasons',
      dict(sensors.suggested_groups(R_HOST)).get('dimm') == 'sensor_on_the_module'
      and dict(sensors.suggested_groups(R_HOST)).get('nic') == 'sensor_on_the_chip',
      sensors.suggested_groups(R_HOST))
silent = reading(sensor('dev.cpu.0.temperature', 'cpu', 50.0), held('memory', 'dimm', None))
check('a memory node that gave no number is not suggested',
      'dimm' not in dict(sensors.suggested_groups(silent)), sensors.suggested_groups(silent))
flat = held('memory', 'dimm', 40.0)
flat['confidence'] = 'constant'
check('a memory sensor proved constant is not suggested',
      'dimm' not in dict(sensors.suggested_groups(reading(flat))))

for label, text, entry, read, ok, groups, detail in (
        ('second 3300 chose dimm: its memory, 52.6', 'dimm', entry3300, R3300_FULL, True, ['dimm'],
         '52.6 C, read from dev.octep.0.temperature.memory.'),
        ('second 3300 chose nic: the hottest port PHY, the SFP+ cages\' at 63.2', 'nic', entry3300,
         R3300_FULL, True, ['nic'], '63.2 C, read from dev.octep.0.temperature.sfp_phy.'),
        ('second 3300 chose cpu,dimm,nic: the hottest of all, the processor', 'cpu,dimm,nic',
         entry3300, R3300_FULL, True, ['cpu', 'dimm', 'nic'], '64.1 C, read from cpld.cpu_c.'),
        ('first 3300 chose dimm, which it has no sensor of: missing, the fans go back', 'dimm',
         entry3300, R3300_FIRST, False, ['dimm'],
         'a temperature the decision needs is not being read: dimm.'),
        ('136 chose nic: its PHY over the link', 'nic', entry136, R136_PHY, True, ['nic'],
         '55.0 C, read from npu.phy:1.')):
    fanctl._groups_seen.clear()
    line = fanctl.sensors_line(options_for(text), entry, read)
    check(label, line['ok'] is ok and line['chosen'] and line['groups'] == groups
          and line['detail'] == detail, line['detail'])

defaults = [fanctl.MODEL_SENSORS, TABLE.get('defaults', {}).get('sensors', '')]
defaults += [','.join(fan.get('sensors') or []) for model in TABLE['models'].values()
             for fan in model.get('fans') or []]
check('dimm and nic are a default nowhere: the model, the table\'s defaults, no fan in the table',
      not any(group in sensors.groups_from(text) for text in defaults for group in ('dimm', 'nic')),
      [text for text in defaults if {'dimm', 'nic'} & set(sensors.groups_from(text))])
fanctl._groups_seen.clear()
line = fanctl.sensors_line(options_for(fanctl.MODEL_SENSORS), entry3300, R3300_FULL)
check('second 3300 as shipped follows npu, cpu, inlet - not its memory or port PHYs',
      line['ok'] and line['groups'] == ['npu', 'cpu', 'inlet'] and line['left_out'] == [],
      line['detail'])

# The page offers the two only where the machine reports them (sensor_offer() in index.volt).
offered = pagejs.page(['sensor_title', 'FOUND_ONLY', 'sensor_offer'], [
    "sensor_offer('dimm', ['cpu', 'npu', 'inlet', 'nic', 'dimm'], false)",
    "sensor_offer('nic', ['cpu', 'npu', 'inlet', 'nic', 'dimm'], false)",
    "sensor_offer('dimm', ['cpu', 'npu', 'inlet', 'nic'], false)",
    "sensor_offer('nic', ['cpu', 'pch'], false)",
    "sensor_offer('dimm', ['cpu', 'pch'], true)",
    "sensor_offer('npu', ['cpu', 'pch'], false)",
    "sensor_offer('dimm', null, false)",
    "sensor_offer('drive', ['cpu', 'drive'], false)",
])
if offered is None:
    print('--    the page\'s offer of dimm and nic: not run, node is not here')
else:
    check('page: memory modules offered where a memory sensor is read', offered[0] == 'memory modules',
          offered[0])
    check('page: port PHYs offered where one is read',
          offered[1] == 'port PHYs and network controllers', offered[1])
    check('page: memory modules not offered on a 3300 whose module has no sensor', offered[2] is None,
          offered[2])
    check('page: network chips not offered on a machine without one', offered[3] is None, offered[3])
    check('page: a saved dimm the machine lacks is shown, with the note',
          offered[4] == 'memory modules (not on this machine)', offered[4])
    check('page: npu is offered everywhere, with the note where it is not read',
          offered[5] == 'network coprocessor (not on this machine)', offered[5])
    check('page: before any document has said, nothing is called absent', offered[6] == 'memory modules',
          offered[6])
    check('page: a group the page has no title for is not offered', offered[7] is None, offered[7])


print()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
print('all passed')
