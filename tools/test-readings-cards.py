#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""One card for the memory and one for the hottest port PHY, on the page and on the dashboard
widget, each with the record's lowest, mean and highest (2026-11-17).

The page's Readings tab draws a sensor as a card with the window's three figures under it, and
folds a kind into one card - the hottest of it, with its own record - where there are enough of it:
three of anything, so an XGS 3300 without an expansion module drew its two port PHYs as two cards,
and none said which was the hotter. Port PHYs and memory modules fold at two now, under a name that
says the card is the hottest. The widget folds them the same way, and puts the record's three
figures under the memory's line and the hottest PHY's, from the last day, or the last hour while
the record has no day of that sensor yet.

Both run in node (tools/pagejs.py): the page's fold rule cut out of the page as the GUI serves it,
and the widget's readings and rows, with readings and a record written here. Without node there is
nothing this can check, and it says so and fails.

    python3 tools/test-readings-cards.py"""

import json
import sys

import pagejs

# The rows carry a degree sign and the bidi isolates round their figures, which a console that is not
# UTF-8 cannot print: written as escapes there rather than ending the run.
sys.stdout.reconfigure(errors='backslashreplace')

failures = []


def check(label, condition, shown=''):
    print('%s  %s%s' % ('ok  ' if condition else 'FAIL', label, ('  [%s]' % (shown,)) if shown else ''))
    if not condition:
        failures.append(label)


if not pagejs.node():
    print('node is not here: the page and the widget cannot be run, so nothing was checked')
    sys.exit(1)


def sensor(name, kind, celsius, label=''):
    return {'name': name, 'kind': kind, 'label': label or name, 'group': '', 'celsius': celsius}


OCTEP = 'dev.octep.0.temperature.'
# The second XGS 3300 without its expansion module: the coprocessor, the processor and the inlet
# from the CPLD, the memory module and two port PHYs from os-xgs-npu's driver.
NO_MODULE = [sensor('cpld.npu_c', 'npu_die', 77.6, 'NPU die'),
             sensor('cpld.cpu_c', 'cpu_board', 64.1, 'processor (board)'),
             sensor(OCTEP + 'switch', 'phy', 61.1, 'switch chip'),
             sensor(OCTEP + 'sfp_phy', 'phy', 63.2, 'F1/F2 PHY'),
             sensor(OCTEP + 'memory', 'dimm', 52.6, 'coprocessor memory'),
             sensor('cpld.inlet_c', 'ambient', 32.1, 'inlet air')]
WITH_MODULE = NO_MODULE + [sensor(OCTEP + 'module_phy', 'phy', 49.1, 'module PHY')]
ONE_PHY = [item for item in NO_MODULE if item['name'] != OCTEP + 'switch']
# The XGS 136, as a machine with four processor cores and the twelve PHYs of the coprocessor's link.
XGS136 = [sensor('dev.cpu.%d.temperature' % n, 'cpu_core', 58.0 + n) for n in range(4)] + \
         [sensor('npu.phy:%d' % n, 'phy', 40.0 + n) for n in range(1, 13)]
TWO_DIMMS = [sensor('dev.jedec_dimm.0.temp', 'dimm', 41.0), sensor('dev.jedec_dimm.1.temp', 'dimm', 43.5),
             sensor('dev.cpu.0.temperature', 'cpu_core', 50.0)]


def window(low, mean, high, seconds=3600, coverage=1.0):
    return {'min': low, 'mean': mean, 'max': high, 'seconds': seconds, 'coverage': coverage, 'above': {}}


TREND = {'series': {
    'T|' + OCTEP + 'memory': {'kind': 'T', 'name': OCTEP + 'memory',
                              'windows': {'hour': window(51.9, 52.3, 52.6),
                                          'day': window(45.8, 49.1, 52.6, 86400)}},
    'T|' + OCTEP + 'sfp_phy': {'kind': 'T', 'name': OCTEP + 'sfp_phy',
                               'windows': {'hour': window(62.8, 63.0, 63.2),
                                           'day': window(60.0, 61.9, 63.2, 21600, 0.25)}},
    'T|' + OCTEP + 'switch': {'kind': 'T', 'name': OCTEP + 'switch',
                              'windows': {'hour': window(60.0, 60.6, 61.0)}},
    'T|cpld.npu_c': {'kind': 'T', 'name': 'cpld.npu_c',
                     'windows': {'day': window(70.1, 74.0, 77.6, 86400)}},
    'R|FAN0': {'kind': 'R', 'name': 'FAN0', 'windows': {'day': window(6900, 7000, 7200, 86400)}},
}}

ISOLATE = (u'⁨', u'⁩')


# -- The page -----------------------------------------------------------------------------------------

counts = [
    ('two port PHYs', NO_MODULE, [], {'phy': 2}),
    ('three port PHYs', WITH_MODULE, [], {'phy': 3}),
    ('one port PHY', ONE_PHY, [], {}),
    ('two port PHYs, one of them proved constant', NO_MODULE, [OCTEP + 'switch'], {}),
    ('the XGS 136: four cores, twelve PHYs', XGS136, [], {'cpu_core': 4, 'phy': 12}),
    ('two memory modules, one core', TWO_DIMMS, [], {'dimm': 2}),
    ('two cores', XGS136[:2], [], {}),
]
titles = [('phy', 2), ('dimm', 4), ('cpu_core', 4), ('npu_core', 4)]
answers = pagejs.page(
    ['fc_num', 'fc_fill', 'FOLD', 'fold_counts', 'fold_title'],
    ['fold_counts(%s, %s)' % (json.dumps(read), json.dumps(constant)) for _, read, constant, _ in counts] +
    ["fold_title('%s', %d)" % pair for pair in titles])
for (label, _, _, expect), answer in zip(counts, answers):
    check('page folds %s: %s' % (label, expect or 'nothing'), answer == expect, answer)
for (pair, expect), answer in zip(zip(titles, ['hottest of 2 port PHYs', 'hottest of 4 memory modules',
                                               'processor cores (4)', 'coprocessor cores (4)']),
                                  answers[len(counts):]):
    check('page names the fold of %d %s: %s' % (pair[1], pair[0], expect), answer == expect, answer)


# -- The widget ---------------------------------------------------------------------------------------

body = '''
const curve = {limit_c: 70, panic_c: 85};
const trend = %(trend)s;
const out = {};
const lines = (read) => widget._readings({temperatures: {sensors: read}}).map((s) => [s.label, s.name]);
out.no_module = lines(%(no_module)s);
out.with_module = lines(%(with_module)s);
out.one_phy = lines(%(one_phy)s);
out.xgs136 = lines(%(xgs136)s);
out.two_dimms = lines(%(two_dimms)s);
out.ranges = Object.keys(widget._ranges(trend)).sort();
const rows = (read) => widget._readings({temperatures: {sensors: read}})
    .map((s) => [s.name, widget._sensorRow(s, curve)[1]]);
out.before = rows(%(no_module)s);
widget.ranges = widget._ranges(trend);
out.after = rows(%(no_module)s);
widget.ranges[%(memory)s] = {hour: trend.series['T|' + %(memory)s].windows.hour};
out.hour_only = rows(%(no_module)s);
widget.ranges[%(memory)s] = {hour: {min: null, mean: null, max: null, seconds: 0, coverage: 0}};
out.empty_window = rows(%(no_module)s);
console.log(JSON.stringify(out));
''' % {'trend': json.dumps(TREND), 'no_module': json.dumps(NO_MODULE), 'with_module': json.dumps(WITH_MODULE),
       'one_phy': json.dumps(ONE_PHY), 'xgs136': json.dumps(XGS136), 'two_dimms': json.dumps(TWO_DIMMS),
       'memory': json.dumps(OCTEP + 'memory')}
out = pagejs.widget(body)

check('widget, no module: the two PHYs one line, the hotter, named the hottest of 2',
      out['no_module'] == [['NPU die', 'cpld.npu_c'], ['processor (board)', 'cpld.cpu_c'],
                           ['hottest of 2 port PHYs', OCTEP + 'sfp_phy'],
                           ['coprocessor memory', OCTEP + 'memory'], ['inlet air', 'cpld.inlet_c']],
      out['no_module'])
check('widget, with the module: the hottest of 3',
      ['hottest of 3 port PHYs', OCTEP + 'sfp_phy'] in out['with_module']
      and not any(name in (OCTEP + 'switch', OCTEP + 'module_phy') for _, name in out['with_module']),
      out['with_module'])
check('widget, one PHY: its own line under its own name',
      ['F1/F2 PHY', OCTEP + 'sfp_phy'] in out['one_phy'], out['one_phy'])
check('widget, XGS 136: the cores folded at four, the twelve PHYs at the hottest of 12',
      out['xgs136'] == [['processor cores (4)', 'dev.cpu.3.temperature'], ['hottest of 12 port PHYs', 'npu.phy:12']],
      out['xgs136'])
check('widget, two memory modules: the hotter, named the hottest of 2',
      ['hottest of 2 memory modules', 'dev.jedec_dimm.1.temp'] in out['two_dimms'], out['two_dimms'])
check('widget reads the record\'s temperatures and nothing else of it',
      out['ranges'] == sorted(['cpld.npu_c', OCTEP + 'memory', OCTEP + 'sfp_phy', OCTEP + 'switch']),
      out['ranges'])


def row(rows, name):
    return dict(rows).get(name, '')


def plain(html):
    for mark in ISOLATE:
        html = html.replace(mark, '')
    return html


memory, phy, npu = OCTEP + 'memory', OCTEP + 'sfp_phy', 'cpld.npu_c'
check('widget without the record: no range under any line',
      not any('last day' in html or 'last hour' in html for _, html in out['before']))
check('widget: the memory\'s line carries the last day\'s 45.8 / 49.1 / 52.6',
      'last day 45.8 / 49.1 / 52.6 °C' in plain(row(out['after'], memory)), plain(row(out['after'], memory))[-200:])
check('widget: the figures are fenced off from the sentence around them',
      'last day ' + ISOLATE[0] + '45.8 / 49.1 / 52.6 °C' + ISOLATE[1] in row(out['after'], memory))
check('widget: the hottest PHY\'s line carries its own record, the SFP+ cages\' 60.0 / 61.9 / 63.2',
      'last day 60.0 / 61.9 / 63.2 °C' in plain(row(out['after'], phy)), plain(row(out['after'], phy))[-200:])
check('widget: a window watched for a quarter says so in the tooltip',
      'lowest / mean / highest - 25% of this window was watched' in plain(row(out['after'], phy)))
check('widget: a whole window says only which three figures they are',
      'title="lowest / mean / highest"' in plain(row(out['after'], memory)), plain(row(out['after'], memory))[-260:])
check('widget: the coprocessor\'s line has no range, though the record has its day',
      'last day' not in row(out['after'], npu) and 'last hour' not in row(out['after'], npu))
check('widget: a memory the record holds an hour of: the last hour\'s 51.9 / 52.3 / 52.6',
      'last hour 51.9 / 52.3 / 52.6 °C' in plain(row(out['hour_only'], memory)), plain(row(out['hour_only'], memory))[-200:])
check('widget: a window the record kept and watched nothing of: no range',
      'last day' not in row(out['empty_window'], memory) and 'last hour' not in row(out['empty_window'], memory))

print()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
print('all passed')
