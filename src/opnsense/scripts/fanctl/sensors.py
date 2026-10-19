#!/usr/local/bin/python3
"""
    Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.

    --------------------------------------------------------------------------

    Finds the temperatures this machine has - without being told what to
    expect - reads them, names them, and says how sure it is about each one.

    Claims below are marked V (run on the reference appliance and quoted), D
    (primary source, with a file and line) or R (reported). Nothing unmarked is
    a fact.

    WHY THIS FILE STOPPED BEING A LIST OF FOUR PREFIXES
    ---------------------------------------------------
    The version this replaces knew four sysctl prefixes by heart and read the
    leaves under them. That works on the reference appliance and nowhere else,
    and worse, it was wrong ON the reference appliance in a way nobody could
    see: it offered `hw.acpi.thermal.tz0` and `tz1` to a fan curve as
    temperatures.

    They are not temperatures. They are constants baked into this board's DSDT
    (V, 2026-09-22, ex-Sophos XG 330 rev 2):

        TZ00._TMP  falls through to  Return (0x0BC2)  = 3010 dK = 27.85 C
        TZ01._TMP  falls through to  Return (0x0BD6)  = 3030 dK = 29.85 C

    and what the kernel prints is exactly that, for ever:

        dev.cpu.0.temperature   54 51 50 49 45 49 55 49 47 47 50 55 44   (8 distinct)
        dev.pchtherm.0.temp...  37.5 37.5 37.5 37.5 38.5 38.0 ... 39.0   (4 distinct)
        hw.acpi.thermal.tz0     27.9 x13                                 (1 distinct)
        hw.acpi.thermal.tz1     29.9 x13                                 (1 distinct)

    thirteen samples over sixty seconds, the cores swinging eleven degrees in
    the same window. A fan curve following tz0 would hold one duty for ever
    and call it thermostatic control.

    The DSDT says why, and the chain closes (V for each link):

      1. both _TMP methods try the firmware's digital-thermal-sensor path,
         then the ACPI embedded controller, _SB.PCI0.LPCB.H_EC.ECAV;
      2. this machine has no ACPI EC at all: `dev.acpi_ec` is an unknown oid;
      3. so both fall through to the literal at the end of the method;
      4. 0x0BC2 = 3010 decikelvin = 27.85 C, 0x0BD6 = 3030 = 29.85 C;
      5. which is exactly and only what the kernel has ever printed.

    And the same dead EC takes the board's other cooling path with it. The
    DSDT declares five ACPI fans - FAN0..FAN4, PNP0C0B, on/off power resources
    with no _FIF/_FPS/_FSL/_FST, so no speed control and no readback - and
    hangs them off TZ00's _AL0.._AL4 with active trip points at 71.1, 55.1,
    50.1, 45.1 and 40.1 C (V, `hw.acpi.thermal.tz0._ACx`). TZ00 reports 27.9 C
    for ever, so not one of those trips can ever fire. There was never going
    to be anything cooling this machine until somebody wrote this plugin.

    So the rule this file is built on: A NAME IS NOT A MEASUREMENT. Discovery
    finds candidates; a candidate becomes a measurement only when something
    that can tell the difference has looked. Every sensor therefore carries a
    `confidence`, and `decide()` will not feed a curve from one this file has
    proved to be a constant.

    WHAT IT LOOKS AT
    ----------------
      1. the sysctl tree, two ways and neither of them a list of drivers.
         First by DECLARATION - `sysctl -aF` prints each node's format string
         and a node registered `IK` or `IK3` is a temperature because its
         author told the kernel so. Second by the shape of the name, for the
         drivers that registered a plain integer instead. So a machine with
         amdtemp, aibs, asmc, jedec_dimm, ow_temp, ugold, gpioths, htu21,
         ds3231 or a NIC that publishes one is covered without this file
         knowing any of those names. Those are the in-tree modules on FreeBSD
         15.1 whose binaries contain the string "temperature" (V:
         `grep -l -a temperature /boot/kernel/*.ko` over 844 modules returned
         33, of which those are the sensor drivers).
      2. drive temperature through smartctl(8), when it is installed and the
         caller asks. Off by default in the curve path - see read().
      3. the super-I/O hardware monitor's own temperature inputs, through
         fanctl-io's `temp` and chip.py's temps(). On this appliance that is
         where the only sensors the BOARD has of its own live, and FreeBSD
         exposes not one of them: ncthwm(4) registers five sysctls and every
         one is a fan tachometer. It is also where the most dangerous readings
         in this file come from, which is what the SUPER-I/O section below is
         for. This file still degrades to "not available" and never to an
         exception when the helper is older than the command, busy, or absent.

    WHAT IT WILL NOT DO
    -------------------
      * load a kernel module to make a sensor appear. A fan plugin that
        kldloads a driver on a firewall to get a nicer number is a fan plugin
        that changed the machine. What is not in the tree does not exist here.
      * write anything, anywhere, ever - including the one write that would be
        tempting: Linux's nct6775 driver CLEARS bit 0 of a temperature input's
        configuration register to enable an input the firmware switched off
        (D, nct6775-core.c:3431-3445, `if (tmp & 0x01) write(reg, tmp & 0xfe)`).
        This file reports such an input as disabled and leaves it disabled.
      * guess what an unidentified input is physically touching. See
        `attached` and `attached_hint`.

    THE OUTPUT IS MACHINE-READABLE AND ENGLISH-ONLY
    -----------------------------------------------
    Every human-facing word here is an English key. The Arabic lives in the
    translation catalogue and the page maps `kind` and `confidence` through it,
    exactly as index.volt already does for `sensor_title`. Nothing in this file
    is ever translated in place.
"""

import errno
import json
import os
import re
import subprocess
import time

# --------------------------------------------------------------------------
# Groups: the coarse buckets a settings page offers and a curve selects on.
#
# The first four are the keys the settings model, index.volt's `sensor_title`
# map and stored configurations already use; they keep their exact spelling so
# that an upgrade does not silently drop a saved selection. The rest were added
# when discovery stopped being a fixed list, and each one exists because a real
# driver puts something there.
#
#   cpu    on-die core and package sensors. On an x86 firewall this is the
#          fastest-moving temperature in the machine and the one a curve
#          usually wants: it moves within a second of the load arriving.
#   pch    the chipset. Slower, and on a passively cooled southbridge it is
#          the reading that notices a chassis fan that has already stopped.
#   acpi   the board's own thermal zones - whatever the firmware chose to put
#          there, which on this appliance is a constant (see the header) and
#          on another machine may be the only sensor present.
#   nvme   drive sensors the kernel publishes in the sysctl tree.
#   drive  drive temperature obtained from smartctl, which is a different
#          path with a different cost and is kept a different group for that
#          reason alone.
#   board  hardware-monitor inputs: super-I/O, aibs, asmc, a BMC's SDRs.
#          These are the ones whose physical attachment is unknown.
#   nic    network controllers that publish a die temperature.
#   dimm   memory modules (jedec_dimm and friends).
#   other  a temperature this file found and could not classify. It is shown,
#          because a reading nobody has named is still a reading, and it is
#          never a default curve input.
GROUPS = ('cpu', 'pch', 'acpi', 'nvme', 'drive', 'board', 'nic', 'dimm', 'npu', 'inlet', 'other')

# How sure this file is about each sensor. These are keys, not sentences; the
# page owns the words. Declared here, like GROUPS, so that the page and the
# tests have one place to read the vocabulary from rather than four string
# literals that can drift.
#
#   measured   a driver publishes it as a temperature AND it has been seen to
#              change. Nothing else in software proves a sensor is live.
#   named      a driver publishes it as a temperature and names what it is.
#              Trustworthy, but not yet proven to move - which is the ordinary
#              state on a machine that has just booted.
#   constant   it is readable and has not moved across the whole history while
#              something else did. Not a sensor. tz0 and tz1 above.
#   unknown    found by the shape of its name in a tree this file does not
#              recognise. Shown, never followed by default.
CONFIDENCE = ('measured', 'named', 'constant', 'unknown')

# Enumeration is expensive and reading is not, so they are separated and only
# one of them is cached. Measured on the reference appliance (V):
#
#     sysctl -aF, the whole name space       67-79 ms idle, 195 ms busy
#     read(), seven named sensors             2-9 ms
#     read(include_drives=True)              18-44 ms
#     smartctl -A -j, one SATA disk          13-15 ms
#
# A fan controller asks every ten seconds. Re-enumerating each time would cost
# more than everything else in the loop put together, for an answer that
# changes when somebody plugs a disk in. Five minutes is short enough that the
# disk appears while they are still standing there, and long enough that the
# enumeration is well under a thousandth of the loop.
REDISCOVER_SECONDS = 300

# Anything outside this is not a temperature: it is a sensor that has come
# loose, or a node that means something else entirely. A curve fed -273 C
# would idle a fan in a hot box and one fed 313 C would run every fan at full
# speed for ever; both are worse than an honestly absent reading, which the
# caller already fails safe on.
PLAUSIBLE_LOW = -40.0
PLAUSIBLE_HIGH = 150.0

# A sensor is called `constant` only with this much evidence behind it, and
# these three numbers are the result of getting it wrong first.
#
# The first draft asked for eight samples over forty-five seconds and for
# something else to have moved at all. Run on the reference appliance it
# called `dev.pchtherm.0.temperature` a constant (V, 2026-09-22: twelve
# samples over 55.5 s, every one 46.0 C, while the cores fell from 69 to 50),
# and `decide()` then dropped `pch` from a `cpu,pch` selection. That is a real
# sensor, and it was only holding still: the PCH reports in half-degree steps
# and sits inside a lump of metal, so a minute of no change is its ordinary
# behaviour. It had in fact moved 37.5 -> 39.0 in an earlier minute (V).
#
# An accusation this file gets wrong removes a real sensor from a fan
# decision, so the bar is set where no sensor that moves at all can reach it:
# half an hour, sixty samples, AND another sensor swinging eight degrees in
# the same window - which on this machine the cores do inside two minutes, and
# which tz0 and tz1 have never done in their lives.
CONSTANT_MIN_SAMPLES = 60
CONSTANT_MIN_SECONDS = 1800.0
CONSTANT_PEER_SWING_C = 8.0

# How many samples of each sensor to keep. At a ten-second interval this is
# two hours, which is what the statistics on the page are drawn from and is
# comfortably longer than the window `constant` needs. It is also the whole of
# this module's memory growth: a daemon that runs for a year holds this and
# nothing more.
HISTORY = 720


# --------------------------------------------------------------------------
# Parsing
#
# sysctl(8) applies the node's format string before printing, so a CTLTYPE_INT
# registered "IK" arrives as `82.0C` and not as the 3551 decikelvin it holds
# underneath. That is the usual case and the cheap one. `sysctl -t` does NOT
# help here - it reports the CTLTYPE and says `integer` for every one of these
# (V: dev.cpu.0.temperature, dev.pchtherm.0.temperature, dev.pchtherm.0.pmtemp,
# hw.acpi.thermal.tz0.temperature, dev.cpu.0.coretemp.tjmax all report
# `integer`) - so the units have to come out of the printed value.

_CELSIUS = re.compile(r'^(-?\d+(?:\.\d+)?)\s*C$')
_NUMBER = re.compile(r'^(-?\d+(?:\.\d+)?)$')


def parse_units(raw):
    """(celsius, units_key) for a printed sysctl value, or (None, reason).

    The units problem is real and this file refuses to bluff its way through
    it. Three shapes are accepted and a fourth deliberately is not:

      `82.0C`          Celsius. sysctl formatted an IK node. Almost always
                       this, and never ambiguous.
      -40 .. 150       a bare number in a plausible Celsius range.
      2000 .. 4500     decikelvin, the raw units under the IK format, for a
                       node whose format string got lost somewhere. 3551 is
                       not a temperature in any other reading.
      1e6 .. 1.6e8     microCelsius. ugold(4) says so in as many words
                       (D, "Inner temperature in microCelsius" in
                       /boot/kernel/ugold.ko).

    NOT accepted: the 1000..150000 band, where Linux-style millidegrees and
    FreeBSD decikelvin overlap. 3131 is 40.0 C read as decikelvin and 3.1 C
    read as millidegrees, and there is nothing in the name or the type to
    separate them. A wrong guess there feeds a fan curve a number an order of
    magnitude out, so the answer is `None` and the sensor is reported
    unreadable with its raw text intact for somebody to look at.
    """
    if raw is None:
        return None, 'absent'
    text = str(raw).strip()
    if not text:
        return None, 'absent'

    match = _CELSIUS.match(text)
    if match is not None:
        celsius = float(match.group(1))
        units = 'C'
    else:
        match = _NUMBER.match(text)
        if match is None:
            return None, 'not-a-number'
        number = float(match.group(1))
        magnitude = abs(number)
        if magnitude <= 150.0:
            celsius, units = number, 'C'
        elif 2000.0 <= magnitude <= 4500.0:
            celsius, units = (number / 10.0) - 273.15, 'dK'
        elif 1000000.0 <= magnitude <= 160000000.0:
            celsius, units = number / 1000000.0, 'uC'
        else:
            return None, 'units-unknown'

    if celsius < PLAUSIBLE_LOW or celsius > PLAUSIBLE_HIGH:
        return None, 'implausible'
    return round(celsius, 1), units


def parse(raw):
    """A temperature in degrees Celsius, or None when it is not one.

    Kept with exactly the signature the rest of the plugin already calls.
    None is a real answer and the callers treat it as one: a sensor that has
    stopped answering must never be turned into a plausible-looking number.
    """
    return parse_units(raw)[0]


# --------------------------------------------------------------------------
# Discovery
#
# The shape rules below were not remembered. They come from reading the leaf
# names out of the modules on this machine that could publish one
# (V, `strings -a /boot/kernel/<m>.ko | grep -i temp`):
#
#   coretemp        dev.cpu.<n>.temperature                  "82.0C"
#   pchtherm        dev.pchtherm.<n>.temperature             "55.5C"
#   acpi_tz         hw.acpi.thermal.tz<n>.temperature        "27.9C"
#   amdtemp         dev.amdtemp.<n>.core<c>.sensor<s>, .ccd<n>
#                   ("Core 0 / Sensor 0 temperature", "CCD %u temperature")
#   aibs            leaf `temp`
#   jedec_dimm      leaf `temp`   ("DIMM temperature (deg C)")
#   asmc            dev.asmc.<n>.temp.<name>  (dozens of them)
#   ow_temp         leaf `temperature`
#   htu21, ds3231,
#   gpioths         leaf `temperature`
#   ugold           leaf `temperature`, microCelsius
#   if_ix, if_ice   leaf `temp`
#   if_cxgbe        leaf `temperature`
#
# So: a leaf of `temperature` or `temp`, or a leaf matching sensor<n> under a
# parent that says temperature, or an `asmc` temp subtree. Everything else is
# rejected, and the rejection list matters more than the acceptance one.

# A leaf that IS a reading.
_LEAF_IS_TEMPERATURE = re.compile(r'(?:^|\.)(?:temperature|temp)$')

# Sub-trees whose leaves are readings even though the leaf is not called
# `temp`: amdtemp's per-core sensors, and the drivers that hang a numbered or
# named probe UNDER a `temp` node rather than calling the leaf `temp`.
#
# The second half of this is here because the first draft did not have it and
# dropped every ASUS board on the market: aibs(4) makes a `temp` subtree and
# hangs a child named for the sensor's INDEX under it, so the leaf is a digit
# and no rule about the word "temp" at the end of a name can see it
# (D, sys/dev/acpi_support/atk0110.c:222-235 - `snprintf(sysctl_name, ..., "%i",
# idx)` under a node named "temp"). asmc(4) does the same with names instead
# of numbers: `dev.asmc.0.temp.TC0D` and about forty siblings.
#
# aibs is in fact caught by the declaration path as well, because it registers
# those nodes with the "IK" format (D, atk0110.c:235). That is the ordinary
# case: the two paths overlap, and the one below is the net for a driver whose
# author used a plain "I".
#
# The third alternative is `hw.temperature.<name>`, a whole subtree whose
# children are readings and whose leaf is the sensor's name - so `CPU`, and no
# rule about the word "temp" at the end can see that either. It is not on this
# appliance, and it is not invented here: OPNsense's own RRD collector looks
# for `hw.temperature.CPU` alongside dev.cpu.0.temperature and
# hw.acpi.thermal.tz0.temperature (D,
# /usr/local/opnsense/scripts/health/library/OPNsense/RRD/Stats/Temperature.php
# :36-39). Somebody's machine has it; this one does not.
_SUBTREE_IS_TEMPERATURE = re.compile(
    r'^(?:dev\.(?:amdtemp\.\d+\.(?:core\d+\.sensor\d+|ccd\d+)'
    r'|[a-z0-9_]+\.\d+\.temp\.[A-Za-z0-9_]+)'
    r'|hw\.temperature\.[A-Za-z0-9_.]+)$')

# A leaf that LOOKS like a temperature and is a LIMIT. This list is the whole
# reason discovery is safe to run on a machine nobody has seen, and every
# entry on it was observed rather than assumed (V, reference appliance):
#
#   dev.pchtherm.0.ctt      115.0C   catastrophic trip, a threshold
#   dev.pchtherm.0.t0temp   108.0C   \
#   dev.pchtherm.0.t1temp   111.0C    > the PCH's own alert thresholds
#   dev.pchtherm.0.t2temp   114.0C   /
#   dev.pchtherm.0.pmtemp    50.0C   a setpoint; thirteen samples, one value
#   dev.cpu.0.coretemp.tjmax 100.0C  the junction limit
#   dev.cpu.0.coretemp.delta     26  distance below tjmax, not a temperature
#   hw.acpi.thermal.tz0._CRT 119.1C  \
#   hw.acpi.thermal.tz0._ACx ...      > ACPI trip points
#   hw.acpi.thermal.tz0._PSV  -1     /
#
# pmtemp is the instructive one: it is spelled like a reading, it is inside a
# real sensor's subtree, it parses to a plausible 50.0 C, and it is a number
# the firmware wrote once. Name-shape alone would have taken it.
_LEAF_IS_LIMIT = re.compile(
    r'(?:^|\.)(?:'
    r'ctt|tjmax|delta|resolution|throttle_log'
    r'|pmtemp|pmtime|t\d+temp'
    r'|_[A-Z]{2,3}\d?|_ACx'
    r'|[a-z_]*(?:crit|max|min|hyst|over|under|thresh|threshold|limit|target'
    r'|warn|alarm|offset|trip|shutdown|fail)[a-z_]*'
    r')$')

# Two whole subtrees where only `.temperature` is a reading and everything
# else is a limit, stated positively because these are the two this machine
# proves and a positive rule cannot be defeated by a leaf name nobody
# predicted.
_STRICT_SUBTREES = ('dev.pchtherm.', 'hw.acpi.thermal.')


# Where a driver publishes the limit for a reading, so the page can show
# headroom rather than a bare number. tjmax is the one that matters: 82 C
# means something quite different against a tjmax of 100 than against 70.
def _limit_name(name):
    parts = name.split('.')
    if name.startswith('dev.cpu.') and name.endswith('.temperature'):
        return 'dev.cpu.%s.coretemp.tjmax' % parts[2]
    if name.startswith('dev.pchtherm.') and name.endswith('.temperature'):
        return 'dev.pchtherm.%s.ctt' % parts[2]
    if name.startswith('hw.acpi.thermal.') and name.endswith('.temperature'):
        return 'hw.acpi.thermal.%s._CRT' % parts[3]
    return None


# group and kind, from the shape of the name. `kind` is the key the page
# translates; `label` below is the English fallback for anywhere that has no
# catalogue (the CLI, the log, a status file read over ssh).
_CLASSIFY = (
    # os-xgs-npu's copies of the coprocessor's readings, held by its drivers for OPNsense's own
    # temperature widget - see _MIRRORS. Unclassified they were 'other', named by their sysctl.
    (re.compile(r'^dev\.octep\.\d+\.temperature\.npu$'), 'npu', 'npu_die'),
    (re.compile(r'^dev\.octep\.\d+\.temperature\.inlet$'), 'inlet', 'ambient'),
    (re.compile(r'^dev\.npuep\.\d+\.temperature$'), 'npu', 'npu_die'),
    (re.compile(r'^dev\.cpu\.\d+\.temperature$'), 'cpu', 'cpu_core'),
    (re.compile(r'^dev\.coretemp\.'), 'cpu', 'cpu_core'),
    (re.compile(r'^dev\.amdtemp\.\d+\.ccd\d+$'), 'cpu', 'cpu_die'),
    (re.compile(r'^dev\.amdtemp\.'), 'cpu', 'cpu_core'),
    (re.compile(r'^dev\.pchtherm\.'), 'pch', 'chipset'),
    (re.compile(r'^hw\.acpi\.thermal\.'), 'acpi', 'acpi_zone'),
    (re.compile(r'^dev\.nvme\.'), 'nvme', 'nvme'),
    (re.compile(r'^dev\.(?:nda|nvd)\.'), 'nvme', 'nvme'),
    (re.compile(r'^dev\.jedec_dimm\.'), 'dimm', 'dimm'),
    (re.compile(r'^dev\.(?:aibs|asmc|ipmi|superio|ncthwm|nct)'), 'board', 'board_input'),
    (re.compile(r'^dev\.(?:ix|ice|cxgbe|t\d+nex|bnxt|mlx5en|igb|em|ixl|cc|axgbe)\.'),
     'nic', 'nic'),
    (re.compile(r'^dev\.(?:ow_temp|htu21|ds3231|gpioths|ugold|lm75|tmp\d+)\.'),
     'board', 'probe'),
    # hw.temperature.CPU and its siblings. The leaf is the sensor's own name
    # and the only one anybody can rely on is CPU, so that one is classified
    # and the rest go to `board` as named probes - which is what they are.
    (re.compile(r'^hw\.temperature\.(?:CPU|cpu)'), 'cpu', 'cpu_die'),
    (re.compile(r'^hw\.temperature\.'), 'board', 'probe'),
)


# os-xgs-npu's drivers hold copies of the coprocessor's readings, written every minute by its
# scripts/xgs/temperatures.sh so that OPNsense's own temperature widget has them. Each copy, and
# the reading here that it copies.
_MIRRORS = (
    (re.compile(r'^dev\.octep\.\d+\.temperature\.npu$'), 'cpld.npu_c'),
    (re.compile(r'^dev\.octep\.\d+\.temperature\.inlet$'), 'cpld.inlet_c'),
    (re.compile(r'^dev\.npuep\.\d+\.temperature$'), 'npu.zone:ap-thermal-ic'),
)


def _mirror_of(name):
    """The name of the reading this sensor is a copy of, or None."""
    for pattern, original in _MIRRORS:
        if pattern.match(str(name)):
            return original
    return None


def _classify(name):
    for pattern, group, kind in _CLASSIFY:
        if pattern.match(name):
            return group, kind
    return 'other', 'unknown'


def _label(name, kind):
    """A short English name, for a table that has to fit it in a column.

    This is a fallback and a log line, not the page's label. The page has
    `kind` and the catalogue; anything here would be untranslatable.
    """
    parts = name.split('.')
    if name.startswith('dev.octep.') and name.endswith('.temperature.npu'):
        return 'NPU (the coprocessor die), as its driver holds it'
    if name.startswith('dev.octep.') and name.endswith('.temperature.inlet'):
        return 'inlet air, as the driver holds it'
    if name.startswith('dev.npuep.') and name.endswith('.temperature'):
        return 'NPU, the SoC sensor, as its driver holds it'
    if name.startswith('dev.amdtemp.') and len(parts) >= 3:
        # core0.sensor0 is the processor's own reading on these Ryzen boards, not one core's;
        # the name was the whole sysctl, on the page's hottest card too.
        return 'processor, as amdtemp reads it'
    if kind == 'cpu_core' and name.startswith('dev.cpu.'):
        return 'core %s' % parts[2]
    if kind == 'chipset':
        return 'chipset'
    if kind == 'acpi_zone' and len(parts) >= 4:
        return 'ACPI zone %s' % parts[3].replace('tz', '')
    if kind == 'nvme' and len(parts) >= 3:
        return 'drive %s' % parts[2]
    if kind == 'drive':
        return 'drive %s' % parts[-1]
    if kind == 'dimm' and len(parts) >= 3:
        return 'memory %s' % parts[2]
    if kind == 'nic' and len(parts) >= 3:
        return '%s%s' % (parts[1], parts[2])
    if name.startswith('hw.temperature.'):
        # The leaf IS the name here - 'CPU', 'Ambient'. Prefixing it with
        # the subtree would read 'temperature Ambient'.
        return '.'.join(parts[2:])
    if kind in ('board_input', 'probe') and len(parts) >= 3:
        return '%s %s' % (parts[1], '.'.join(parts[3:]) or parts[2])
    return name


def _is_candidate(name):
    """Is this sysctl a temperature READING, as opposed to a limit or a knob.

    The limit test runs FIRST and beats every acceptance rule below it. That
    ordering is the whole safety property of this function: an acceptance rule
    is a guess about a driver nobody here has, and a limit that slips through
    it becomes a 115 C reading that pins a fan at full speed for ever. A
    reading wrongly rejected is a sensor missing from a page, which somebody
    notices and nobody is hurt by.
    """
    if _LEAF_IS_LIMIT.search(name):
        return False
    for prefix in _STRICT_SUBTREES:
        if name.startswith(prefix):
            return name.endswith('.temperature')
    if _SUBTREE_IS_TEMPERATURE.match(name):
        return True
    return bool(_LEAF_IS_TEMPERATURE.search(name))


# --------------------------------------------------------------------------
# Running things

def _run(args, timeout=10):
    """Run a command and return whatever it managed to print on stdout.

    Same habit as the sibling plugins - a tool that is missing or unhappy
    shows up as absent data the caller can talk about, never as an exception
    that takes a fan controller down - with one difference that matters here.

    The output is kept whatever the exit status. sysctl(8) is asked for
    several names at once and exits non-zero when ANY of them is unknown,
    while still printing every one that exists: a sensor that vanished when a
    device detached is the ordinary case, not an error. Discarding stdout on a
    non-zero exit - which is what the sibling plugins' version of this helper
    does, correctly, for commands that ask one thing - would let one absent
    sensor take every present one with it, and a fan controller with no
    temperatures fails safe and does nothing at all.
    """
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return done.stdout or ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _sysctl_formats():
    """{name: format string} for the whole tree, values untouched.

    `-aF` prints each node's FORMAT and not its value. That matters beyond
    speed: `sysctl -a` READS every node in the tree, costs 135-270 ms (V,
    three runs) and asks a great many drivers for a value nobody wanted.
    Enumeration should not be a side effect.

    The format is the best thing in this file, and it was not this file's
    idea. OPNsense core already finds its dashboard sensors this way
    (D, /usr/local/opnsense/service/conf/actions.d/actions_system.conf,
    [sensors]):

        sysctl -aF | awk -F ": " '$2 ~ "^IK" { print $1 }' |
          grep -v -e "\\._" -e "\\.ctt" -e "\\.[pt][m012]" -e "\\.tjmax" | sort

    A node registered with the `IK` format IS a temperature, because the
    driver author said so to the kernel - that is a declaration, not an
    inference from a name. `IK` holds tenths of a kelvin and `IK3`
    thousandths, and sysctl(8) formats both with the trailing C. Nothing else
    in the tree carries that format.

    It is not the whole answer, which is why the name-shape rules above stay:
    a driver that registers a temperature as a plain `I` - ugold(4)'s
    microCelsius nodes, for one - is invisible to this test and visible to
    those. So both run, and a node found by declaration is marked as such.
    """
    formats = {}
    for line in _run(['/sbin/sysctl', '-aF']).splitlines():
        name, _, fmt = line.partition(':')
        name = name.strip()
        if name:
            formats[name] = fmt.strip()

    # None, not {}, when nothing came back at all, and the difference is the
    # whole of fix 1 below. _run() returns the empty string for a timeout, for
    # an OSError, and for a sysctl that could not be forked - and it returns
    # the empty string for a machine with an empty sysctl tree, which does not
    # exist. There is no FreeBSD on which `sysctl -aF` prints nothing, so not
    # one line IS a failure and is reported as one instead of being mistaken
    # for a finding (V, 2026-09-22: 19280 names on the reference appliance).
    return formats or None


# The format strings that declare a temperature, and what they hold.
_DECLARED_UNITS = {'IK': 'dK', 'IK3': 'mK'}


def _sysctl_values(names):
    """{name: raw string} for the names given, in one call.

    One process launch for the lot. One per sensor would be eight launches
    every ten seconds for an answer the kernel hands over in one.
    """
    values = {}
    if not names:
        return values
    for line in _run(['/sbin/sysctl', '-e'] + list(names)).splitlines():
        name, _, value = line.partition('=')
        name = name.strip()
        if name:
            values[name] = value.strip()
    return values


# --------------------------------------------------------------------------
# The catalogue: what this machine has

_catalogue = None
_catalogue_at = 0.0


def discover(force=False):
    """Everything this machine could report a temperature from.

    Returns a list of descriptors, each a dict that says where a reading comes
    from and what is known about it BEFORE anything has been read:

        {'key': 'sysctl:dev.cpu.0.temperature',
         'name': 'dev.cpu.0.temperature',
         'source': 'sysctl',
         'group': 'cpu', 'kind': 'cpu_core', 'label': 'core 0',
         'limit_name': 'dev.cpu.0.coretemp.tjmax',
         'attached': 'cpu_die',
         'format': 'IK', 'declared': True, 'declared_units': 'dK',
         'named_by_driver': True}

    `declared` says the KERNEL calls this node a temperature, not that this
    file guessed from its name. On the reference appliance all seven are
    declared, and the seven are exactly the seven OPNsense core's own
    dashboard finds (V, both lists compared 2026-09-22) - which is the
    cross-check that says the shape rules are not quietly inventing sensors.

    `attached` is what the reading is physically measuring, and it is only
    filled in where a driver's own name settles it: a core's digital thermal
    sensor is on the die by construction, a PCH sensor is in the PCH. Where
    nothing settles it the value is 'unknown' and stays 'unknown' - see the
    SUPER-I/O section for why that is the only honest answer for a
    hardware-monitor input.

    Cached for REDISCOVER_SECONDS. A caller that has just plugged something in
    passes force=True.
    """
    global _catalogue, _catalogue_at

    now = time.time()
    if _catalogue is not None and not force and (now - _catalogue_at) < REDISCOVER_SECONDS:
        return _catalogue

    formats = _sysctl_formats()
    if formats is None:
        # ENUMERATION FAILED - it did not find nothing. Keep whatever was known,
        # do NOT restart the clock, and let the next call try again immediately.
        #
        # This is the one defect in this file that could be walked from a
        # swallowed error to a hot firewall, and it was measured rather than
        # imagined (V, 2026-09-22, /root/fanwork2/adv_test.py on the reference
        # appliance). With `sysctl -aF` failing once and healthy on the very
        # next call, the old code stored the empty list AND its timestamp, so:
        #
        #   discover() during the failure          0 sensors
        #   discover() immediately after, healthy  0 sensors   <- cached
        #   read()          counts {'found': 0, 'read': 0, ...}
        #   decide(cpu,pch,acpi)  (None, [], ['cpu', 'pch', 'acpi'])
        #
        # for the whole REDISCOVER_SECONDS window. A reading with no
        # temperature in it fails the caller's sensors contract
        # (fanctl.py:1076-1082, `ok = celsius is not None and not missing`),
        # a failed contract calls restore(), and restore() puts this
        # appliance's fan back on the law it came with: saved_mode 4,
        # saved_duty_raw 50, which is SmartFan IV asking for 20 percent
        # (V, `fanctl-io status`, 2026-09-22). Five minutes of that with the
        # cores where they are is the thing this plugin exists to prevent.
        #
        # The cost of the fix on a machine that genuinely has no temperature
        # at all is one `sysctl -aF` per call instead of one per five minutes
        # - 50-80 ms (V) against a ten-second loop. That is the right way
        # round: retrying is cheap and a cached failure is not.
        return _catalogue if _catalogue is not None else []

    found = []
    for name, fmt in formats.items():
        declared = _DECLARED_UNITS.get(fmt)

        # Two ways in, and the limit denylist governs both. A declaration says
        # "this node is a temperature"; it does not say "this node is a
        # reading". dev.cpu.0.coretemp.tjmax and hw.acpi.thermal.tz0._CRT are
        # both declared IK on this machine (V) and both are limits.
        if declared is not None:
            if _LEAF_IS_LIMIT.search(name):
                continue
            if any(name.startswith(p) for p in _STRICT_SUBTREES) \
                    and not name.endswith('.temperature'):
                continue
        elif not _is_candidate(name):
            continue

        group, kind = _classify(name)
        found.append({
            'key': 'sysctl:%s' % name,
            'name': name,
            'source': 'sysctl',
            'group': group,
            'kind': kind,
            'label': _label(name, kind),
            'limit_name': _limit_name(name),
            'attached': _ATTACHED.get(kind, 'unknown'),
            'format': fmt,
            'declared': declared is not None,
            'declared_units': declared,
            # A kernel declaration counts as the driver naming it. Without
            # one, the name has to carry the claim on its own.
            'named_by_driver': declared is not None or kind != 'unknown',
        })

    found.sort(key=lambda entry: (GROUPS.index(entry['group']), entry['name']))
    _catalogue = found
    _catalogue_at = now
    return found


# What a `kind` is attached to, where the driver's own name settles it, and
# nothing else goes in this table. Every entry here is one where the sensor is
# INSIDE the thing it measures and could not be anywhere else: a core's
# thermal sensor is on the die, a drive's is in the drive, a DIMM's is on the
# module. That is a fact of construction, not a board designer's intention.
#
# Anything not here is 'unknown', and that is not laziness: a pin on a
# hardware-monitor chip measures whatever somebody put a thermistor next to,
# and no register records what that was. See SUPER-I/O.
_ATTACHED = {
    'cpu_core': 'cpu_die',
    'cpu_die': 'cpu_die',
    'chipset': 'chipset_die',
    'nvme': 'drive',
    'drive': 'drive',
    'dimm': 'memory_module',
    'nic': 'nic_die',
}


# --------------------------------------------------------------------------
# Drives, through smartctl
#
# Kept apart from the sysctl path because it is a different kind of access
# with a different cost and a different failure mode, and because a drive
# temperature is not a fan-curve input by default - a warm SSD is warm because
# it is working, and spinning a chassis fan up at it does very little. It is
# read because somebody looking at the page wants to see it.
#
# V on the reference appliance: smartctl 7.5, one SATA SSD, `smartctl -A -j`
# costs 13-15 ms over three runs - cheaper than the sysctl read - and returns
#
#     "temperature": {"current": 46}
#
# next to raw attributes that are a trap. Attribute 190 "Temperature_Case"
# has raw value 588447779, which is a packed min/max triple that smartctl
# renders as "35 (Min/Max 19/35)"; attribute 194 "Temperature_Internal" is a
# plain 46. Which of 190 and 194 is "the" temperature is a per-vendor
# convention with no rule behind it. So this reads the normalised
# `temperature.current` that smartctl computed and never the raw attribute -
# and where smartctl declines to normalise, this reports nothing rather than
# picking one.
#
# AND IT DOES NOT GO THROUGH os-smart. That plugin is installed on the
# reference appliance and has a script at
# /usr/local/opnsense/scripts/OPNsense/Smart/detailed_list.sh, and calling it
# would be wrong twice over: it is another plugin's private script path, not
# an interface anybody promised to keep, and it does not ask for attributes at
# all (V: no `-A` anywhere in it), so it has no temperature in it to take.
# What os-fanctl actually needs is the package underneath - smartctl belongs
# to smartmontools-7.5_2, which os-smart merely depends on too (V, `pkg
# which` and `pkg info -r smartmontools`). So the dependency to declare is
# smartmontools, the caller is smartctl, and os-smart is not involved.

SMARTCTL = '/usr/local/sbin/smartctl'
SYSCTL_DISKS = 'kern.disks'


def _disks():
    """The disks this machine has, newest-first as the kernel lists them."""
    return [d for d in _run(['/sbin/sysctl', '-n', SYSCTL_DISKS]).split() if d]


def read_drives():
    """{key: entry} for every drive smartctl will give a temperature for.

    `-n standby` is not optional. A spinning disk that has parked its heads
    for the night must not be woken every ten seconds by a fan controller
    asking how warm it is; smartctl exits without touching it and this
    reports the drive as asleep rather than as broken.
    """
    entries = []
    if not os.access(SMARTCTL, os.X_OK):
        return entries

    for disk in _disks():
        device = '/dev/%s' % disk
        raw = _run([SMARTCTL, '-A', '-j', '-n', 'standby', device], timeout=20)
        try:
            answer = json.loads(raw or '{}')
        except ValueError:
            continue

        key = 'smartctl:%s' % disk
        entry = {
            'key': key,
            'name': device,
            'source': 'smartctl',
            'group': 'drive',
            'kind': 'drive',
            'label': 'drive %s' % disk,
            'attached': 'drive',
            'named_by_driver': True,
            'celsius': None,
            'raw': None,
            'units': None,
        }

        # smartctl says so itself when it refused to wake the disk, in two
        # places. power_mode is the structured one and is checked first (V on
        # the reference appliance, which has an SSD and therefore only ever
        # produces the other branch: `"power_mode": {"ata_value": 255, "name":
        # "ACTIVE or IDLE"}`); the message text is the second net, for a
        # smartctl that reports the refusal and no power mode (R - there is no
        # spinning disk here to make it happen).
        mode = (answer.get('power_mode') or {}).get('name', '')
        messages = ' '.join(m.get('string', '')
                            for m in answer.get('smartctl', {}).get('messages', []))
        if any(word in (mode + ' ' + messages).upper()
               for word in ('STANDBY', 'SLEEP')):
            entry['unreadable'] = 'asleep'
            entries.append(entry)
            continue

        current = (answer.get('temperature') or {}).get('current')
        if isinstance(current, (int, float)):
            celsius, units = parse_units(current)
            entry['celsius'] = celsius
            entry['units'] = units
            entry['raw'] = str(current)
            if celsius is None:
                entry['unreadable'] = units
        else:
            entry['unreadable'] = 'not-reported'
        entries.append(entry)

    return entries


# --------------------------------------------------------------------------
# SUPER-I/O: the inputs nobody on this machine has read
#
# ncthwm(4) exposes five fan tachometers and NO temperatures at all (V:
# `sysctl -aN | grep -i temp` on the reference appliance returns nothing under
# dev.ncthwm), although the NCT6779D has six temperature inputs - SYSTIN,
# CPUTIN, AUXTIN0..3 - plus PECI, SMBus and PCH-forwarded sources. Reading
# them needs the port window, which means fanctl-io, which means a command
# that does not exist in the shipped helper yet.
#
# THE REGISTERS, from lm-sensors' driver (D throughout). The source is
# drivers/hwmon/nct6775-core.c, nct6775-platform.c, nct6775.h and lm75.h in
# torvalds/linux at commit 54743b5ab981 (2026-08-11), which is what every
# line number below counts against - a citation to "master" is a citation to
# nothing once somebody adds a chip.
#
#   value registers, with limits      NCT6779_REG_TEMP[]     = {0x27, 0x150}
#                                     (core.c:377)
#   value registers, fan-control      NCT6779_REG_TEMP_MON[] =
#                                     {0x73,0x75,0x77,0x79,0x7b} (core.c:378)
#   what each of the first two is     NCT6775_REG_TEMP_SOURCE[] =
#                                     {0x621,0x622,...}  (core.c:169-170,
#                                     selected for nct6779 at core.c:3890)
#   what each of the last five is     NCT6775_REG_TEMP_SEL[] =
#                                     {0x100,0x200,0x300,0x800,0x900,...}
#                                     (core.c:171-172, selected at core.c:3891)
#   enable bit (0 = on, 1 = off)      NCT6779_REG_TEMP_CONFIG[] = {0x18,0x152}
#                                     (core.c:379-380)
#   thermistor or diode               REG_VBAT 0x5D, REG_DIODE 0x5E,
#                                     DIODE_MASK 0x02   (core.c:99-101),
#                                     decoded at core.c:3457-3468
#   source number to name             nct6779_temp_label[] (core.c:389-422):
#                                     1 SYSTIN, 2 CPUTIN, 3 AUXTIN0,
#                                     4 AUXTIN1, 5 AUXTIN2, 6 AUXTIN3,
#                                     8-15 SMBUSMASTER 0-7, 16-17 PECI Agent
#                                     0-1, 18-25 PCH_*, 26 BYTE_TEMP,
#                                     31 Virtual_TEMP
#   which source numbers are real     NCT6779_TEMP_MASK 0x07ffff7e
#                                     (core.c:424)
#   per-input calibration             NCT6779_REG_TEMP_OFFSET[] =
#                                     {0x454,0x455,0x456,0x44a,0x44b,0x44c,
#                                      0x44d,0x449}  (core.c:386-387), a
#                                     signed byte of whole degrees. The CHIP
#                                     applies it: Linux reads it and shows it
#                                     separately and does not adjust the
#                                     reading with it (D, core.c:1661-1664
#                                     reads it, core.c:2240-2248 prints it,
#                                     and nothing adds it to a temperature).
#                                     Worth reporting, because a firmware that
#                                     calibrated an input is a thing somebody
#                                     should see - and worth NOT adding, which
#                                     would apply it twice.
#   AMD TSI, if any                   NCT6776_REG_TSI_TEMP[] =
#                                     {0x409,0x40b,...,0x417} (core.c:331-332),
#                                     8.3 format, (reg >> 5) * 125 millidegrees
#                                     (core.c:1012-1015)
#
# HOW A VALUE IS DECODED. 0x150 and 0x73/0x75/0x77/0x79/0x7b are word-sized on
# this family (D, core.c:1187-1204); 0x27 is a byte and the driver left-aligns
# it by shifting it up eight (D, core.c:1211-1223). The result goes through
# LM75_TEMP_FROM_REG, which is `((s16)reg / 128) * 500` millidegrees
# (D, drivers/hwmon/lm75.h) - a signed 9-bit value in the top of the word,
# half a degree per step. So: take the word, interpret it as signed 16-bit,
# arithmetic-shift right 7, halve. Resolution 0.5 C.
#
# HOW A SOURCE IS SELECTED AND REPORTED. The source register's low five bits
# (`src &= 0x1f`, D, core.c:4188) index nct6779_temp_label. Zero means the
# register is not assigned to anything. A source whose bit is clear in
# NCT6779_TEMP_MASK is one the chip does not have and Linux logs and skips it
# (D, core.c:4192-4197). That is the whole of the identification the hardware
# offers.
#
# THE COMMAND THIS FILE CALLS. It was designed here, built in fanctl-io.c and
# run on this appliance on 2026-09-22; what follows is what it does and what
# it returned, and the rows further down that were once a sketch are now a
# transcript.
#
#     fanctl-io temp [<n>]
#
#   Argument shape: none, or one input number. The answer is a table of at
#   most seven rows and the table is the point, because a row's meaning is its
#   source byte and a caller asking for "input 3" without the source byte has
#   learned nothing. `temp <n>` narrows the PRINTING only: the whole table is
#   read either way, because `selected_by` is a cross-reference over every
#   source register and a single row cannot be answered without them all.
#
#   Enumeration order, and it is Linux's rather than an invention: the two
#   limit-capable registers first, NCT6779_REG_TEMP = {0x27, 0x150} with their
#   sources at REG_TEMP_SOURCE {0x621, 0x622}, then the five fan-control
#   registers, NCT6779_REG_TEMP_MON = {0x73, 0x75, 0x77, 0x79, 0x7b} with
#   their sources at REG_TEMP_SEL {0x100, 0x200, 0x300, 0x800, 0x900}. Seven
#   rows, indexes 1 to 7 (D, core.c:4181-4241 for the first loop and
#   4242-4293 for the second).
#
#   Output shape, one JSON object as every other command prints. This is a
#   real row off this machine (V, 2026-09-22), not an example:
#
#     {"ok":true,"code":0,"command":"temp",
#      "chip":{...as identify prints...},
#      "map":"NCT6779D","vbat_raw":"0x41","diode_raw":"0x00",
#      "diode_mask":"0x02",
#      "inputs":[
#        {"index":4,"reg":"0x075","word":true,"role":"fan-control",
#         "fan_channel":2,"src_reg":"0x200","src_raw":"0x81","src":1,
#         "src_name":"SYSTIN","src_known":true,"present":true,
#         "config_reg":null,"config_raw":null,
#         "raw":"0x2f00","stable":true,"resolution_c":0.5,"celsius":47.0,
#         "type":"thermistor","pin":true,"attached":"unknown",
#         "offset_reg":"0x454","offset_raw":"0x00","offset_c":0,
#         "selected_by":[2,3,4,5]},
#        {"index":1,"reg":"0x027","src":16,"src_name":"PECI Agent 0",
#         "present":true,"enabled":true,"raw":"0x00","celsius":null,
#         "unreadable":"source-silent","decoded_c":0.0,
#         "pin":false,"attached":"cpu_die","selected_by":[1]}
#      ],
#      "count":7,"inputs_on_chip":7,"note":"..."}
#
#   `present:false` is printed rather than the row being dropped, because
#   "this register exists and is assigned to nothing" and "this chip has no
#   such register" are different facts and a page that cannot tell them apart
#   will show a hole where it should show an answer.
#
#   `celsius:null` with `unreadable` is how EVERY row that is not a
#   temperature arrives, and the reason is always named: unassigned,
#   disabled-in-chip, unstable, implausible, source-silent. `decoded_c` beside
#   one of those is what the decode makes of bytes that were refused - it is
#   evidence for checking the refusal and it is never a reading. This file
#   never promotes it to one.
#
#   `source-silent` is the reason that was not in the design and is the whole
#   reason this section is worth re-reading. A PECI agent, an SMBus master and
#   a PCH forward are not pins: the chip is repeating a number another device
#   gave it, and until that device answers the register holds its reset value.
#   All zeroes decodes to a perfectly plausible 0.0 C. On this board BOTH
#   limit-capable registers are assigned to PECI agents and both read zero
#   while the cores swing (V - see below), so without that rule this file
#   would have published "PECI Agent 0, attached cpu_die, 0.0 C" as a
#   measurement of the CPU die. A curve following it would hold a fan at its
#   minimum for ever. The helper refuses it at the register; this file would
#   have had no way to tell.
#
#   `enabled:false` comes from bit 0 of the configuration register and means
#   the firmware switched the input off. The value register then holds
#   whatever was last there, so `celsius` is null and no number is printed at
#   all. Linux CLEARS that bit to turn the input on (D, core.c:3431-3445).
#   THIS PLUGIN MUST NOT. It is a write, to a chip that is at this moment the
#   only thing controlling the fan that is cooling the machine, for the sake
#   of a nicer number.
#
#   `selected_by` is the one genuinely new thing the command buys, and it is
#   free. Linux's second loop is the one headed "Repeat with temperatures used
#   for fan control" and it reads REG_TEMP_SEL[i] for each of the five
#   monitoring registers (D, core.c:4242-4253), so REG_TEMP_SEL[0] = 0x100 is
#   the temperature that fan channel 1's own control law is watching.
#
#   That answered a question DESIGN.md section 8 had open - which temperature
#   the board's own fan law follows - with a READ, and the answer is worse
#   than the question (V, 2026-09-22, this appliance):
#
#       0x100  fan channel 1  src 0x90 -> source 16, PECI Agent 0, reads 0x00
#       0x200  fan channel 2  src 0x81 -> source  1, SYSTIN,       reads 0x2f00
#       0x300  fan channel 3  src 0x81 -> source  1, SYSTIN
#       0x800  fan channel 4  src 0x81 -> source  1, SYSTIN
#       0x900  fan channel 5  src 0x01 -> source  1, SYSTIN
#
#   Channel 1 is the only channel with a fan on it, its saved original is
#   SmartFan IV at raw 50 - 20% (V, /var/db/fanctl/ch1.state: "4 50 c562") -
#   and the temperature that law watches is a PECI agent that has never
#   reported anything. That is why the vendor's curve sat at 20% with the
#   cores at 80 C: it was not a badly-shaped curve, it was a curve with a dead
#   sensor on its input. It also means `release` hands this fan back to a law
#   that cannot respond to heat, which is a sentence the page should be able
#   to say.
#
#   It is still worth saying what this does not answer: that channel 2 follows
#   SYSTIN does not say what SYSTIN is touching. It moves the question one
#   step, honestly, and stops there.
#
#   Failure shape: the existing one, unchanged, and all four paths exercised
#   (V, 2026-09-22, test builds with a private lock file and a private device
#   path so that nothing near the running daemon was disturbed): code 9 when
#   another fanctl-io holds the window, code 2 when the superio device is not
#   there, code 4 when the chip has no temperature map, code 1 for an input
#   number that does not exist. No new exit code was invented: chip.py's CODES
#   table is the plugin's vocabulary for hardware refusals and a new number
#   there is a new sentence on a page in two languages.
#
#   AND IT NEEDED NO NEW PORT CODE AT ALL, as designed. ec_select(),
#   ec_read_raw(), ec_read() and ec_read16() were already there and unchanged;
#   what `temp` added is a register table, a decode, and one reader
#   (ec_read_value) that applies ec_read's read-it-twice discipline to a value
#   register without ec_read's death on disagreement - because a temperature
#   can legitimately change between two reads, and losing six good rows to one
#   flickering one is a worse answer than a row marked unstable. The helper
#   now has seven commands and two of them only read.
#
# WHAT SELECTING A REGISTER RISKS, SAID PLAINLY. Reading 0x073 is four port
# accesses, two of which are WRITES:
#
#     outb(0xa35, 0x4e)      write: select the bank register
#     outb(0xa36, 0x00)      write: bank 0
#     outb(0xa35, 0x73)      write: latch the index
#     inb (0xa36)            read:  the byte
#
# (D, nct6775-platform.c:316-341 for the sequence; V for the port numbers,
# `fanctl-io identify` reports index_port 0xa35 and data_port 0xa36 on this
# machine.) None of those writes is a fan setting - 0x4E is the bank selector
# and 0xa35 is an address latch, not a register that holds anything. The risk
# is the other one: ncthwm(4) walks the same two ports with no lock at all,
# so a sysctl read landing between the index write and the data read returns a
# byte from whichever register the kernel selected. fanctl-io already answers
# that by reading every configuration register twice and refusing when the two
# disagree.
#
# Which leads to the honest comparison, and it is the reason this command is
# worth adding at all: `fanctl-io status` ALREADY does this. It reports
# `"mode":0,"duty_raw":255` (V, run on the reference appliance), and those can
# only come from 0x102 and 0x109, which are bank 1, which means status already
# writes 0x4E and an index. A temperature read is the same four accesses
# against a different register number. It adds no new CLASS of access, and
# anybody who is willing to run `status` on a live firewall is already
# accepting exactly this.
#
# WHAT THE PLUGIN CAN AND CANNOT SAY ABOUT WHAT AN INPUT TOUCHES:
#
#   it CAN say    which source the chip has selected (SYSTIN, CPUTIN,
#                 AUXTIN0-3, a PECI agent, an SMBus master, a PCH forward) -
#                 from the source register, D above;
#   it CAN say    whether the input is wired for a thermistor or a diode -
#                 from REG_VBAT and REG_DIODE, D above;
#   it CANNOT say what the far end of that wire is touching. SYSTIN is a pin.
#                 What sits near it was decided by whoever laid the board out,
#                 and there is no register that records it. On this appliance
#                 one of AUXTIN0-3 may be the input near the power supply -
#                 whose fan the owner also replaced and for which no sensor
#                 exists anywhere else - and there is no way to find out from
#                 software.
#
#   so it MUST SAY  "attached": "unknown", and say it in those words on the
#                 page next to the reading, together with the only two things
#                 that would settle it: warm one part of the chassis at a time
#                 and watch which input moves, or read the board vendor's
#                 documentation. It must NOT infer from the value. A 45 C
#                 reading is consistent with a dozen places inside a firewall.
#
#   and it MAY OFFER a hint, labelled as one. If an input tracks dev.cpu.* to
#                 within a few degrees across a load swing, it is probably
#                 near the CPU. That is evidence, and it belongs in
#                 `attached_hint` with the correlation that produced it - never
#                 in `attached`, which is reserved for things that are known.
#
# AND ONE MORE THING THE CHIP CAN SAY, WHICH IS NOT THE SAME AS BEING SURE.
# A source that is NOT a pin carries its own claim about what it is: a PECI
# agent is the CPU reporting its own die, a PCH forward is the chipset passing
# on something it already knows. Those are the only attachments this plugin
# names, and on this appliance naming them would have been a mistake, because
# the claim and the reading are different questions (V, 2026-09-22): both PECI
# rows say cpu_die and both read zero while the cores swung 54 to 80 C over
# 766 s. So a claimed attachment is what the source WOULD be measuring if it
# were reporting, and it is never on its own evidence that it is. That is why
# suggested_groups() below now asks a board input to have MOVED before it
# offers it to a curve, and not merely to be named.

# The method chip.py would grow, and the reason it is a METHOD and not a
# command string passed down from here. DESIGN.md section 3 puts one rule
# across this boundary: nothing above chip.py names a register - and by the
# same argument nothing above it names a helper command either, because a
# string like 'temp' in this file is a register name wearing a hat. So this
# asks chip.py for `temps()`; chip.py runs `fanctl-io temp` and turns exit
# codes into its own exceptions exactly as it already does for `tach` and
# `status`, and this file never learns that the helper is a program.
#
# chip.py has that method now (V, 2026-09-22: Chip.temps(), which runs the
# helper's `temp` and classifies its exit codes through the same _run() every
# other command goes through). The name is still read through this constant
# and never written as a literal, for the reason above - and because the
# 'no-passthrough' path stays live for ever: an OPNsense package can be
# upgraded with the daemon still running (DESIGN.md section 8), so a chip.py
# from one version WILL meet a helper from another, and "this build cannot
# ask" has to remain a sentence this file can say without raising.
HELPER_TEMP_METHOD = 'temps'

# Source number to name, from nct6779_temp_label (D, nct6775-core.c:389-422).
# Carried here so that a helper that returns only the number still produces a
# named row, and so that the names on the page and the names in the driver
# cannot drift. Index is the value of the source register's low five bits.
#
# THIS TABLE IS THE FAMILY'S NAMES AND NOT ONE CHIP'S. Entry 31 is the one
# that makes the difference visible: Virtual_TEMP is a source on the later
# chips and is NOT in nct6779_temp_label, whose array stops at 26, and
# NCT6779_TEMP_MASK has bit 31 clear. So a number in here is a name somebody
# in this family uses, not a promise that the chip in front of us has it.
#
# Which is why read_superio() asks the helper before it uses this table at
# all: the helper carries the per-chip mask and says `src_known` false for a
# source number the chip does not claim, and a name is only taken from here
# when it has not said that. V, 2026-09-22: without that check, a source
# register forced to 31 produced a row called "superio Virtual_TEMP" on a
# NCT6779D - a sensor with a driver's name, on a chip that has no such
# source, which is exactly the row DESIGN.md refuses to let a curve follow.
NCT_TEMP_LABEL = {
    1: 'SYSTIN', 2: 'CPUTIN', 3: 'AUXTIN0', 4: 'AUXTIN1', 5: 'AUXTIN2',
    6: 'AUXTIN3',
    8: 'SMBUSMASTER 0', 9: 'SMBUSMASTER 1', 10: 'SMBUSMASTER 2',
    11: 'SMBUSMASTER 3', 12: 'SMBUSMASTER 4', 13: 'SMBUSMASTER 5',
    14: 'SMBUSMASTER 6', 15: 'SMBUSMASTER 7',
    16: 'PECI Agent 0', 17: 'PECI Agent 1',
    18: 'PCH_CHIP_CPU_MAX_TEMP', 19: 'PCH_CHIP_TEMP', 20: 'PCH_CPU_TEMP',
    21: 'PCH_MCH_TEMP', 22: 'PCH_DIM0_TEMP', 23: 'PCH_DIM1_TEMP',
    24: 'PCH_DIM2_TEMP', 25: 'PCH_DIM3_TEMP', 26: 'BYTE_TEMP',
    31: 'Virtual_TEMP',
}

# The sources that ARE identified by the chip, because they are not board
# wiring at all: a PECI agent is the CPU reporting its own die over its own
# bus, and a PCH_* forward is the chipset passing on something it already
# knows. Nothing on the board decides what those mean.
#
# Everything else - SYSTIN, CPUTIN, AUXTIN0-3 - is a pin, and a pin is
# unknown. CPUTIN is deliberately on the unknown side: the name is the
# board designer's intention, not a measurement, and on a repurposed appliance
# the intention is the vendor's and the vendor is gone.
#
# READ THIS TABLE AS A CLAIM, NOT AS A FINDING. It says what a source would be
# measuring if it were reporting. It does not say that it is. On this
# appliance both PECI agents are in here as cpu_die and both are silent (V -
# see the SUPER-I/O section), and the helper is what catches that: a forwarded
# source holding nothing but its reset value arrives with celsius None and
# `unreadable: source-silent`, so it never reaches a curve and never reaches
# this table's promise. The same word in `attached` means two different things
# on a pin and on a forward, and the difference is carried in `pin`.
NCT_ATTACHED = {
    'PECI Agent 0': 'cpu_die', 'PECI Agent 1': 'cpu_die',
    'PCH_CHIP_TEMP': 'chipset_die', 'PCH_CPU_TEMP': 'cpu_die',
    'PCH_DIM0_TEMP': 'memory_module', 'PCH_DIM1_TEMP': 'memory_module',
    'PCH_DIM2_TEMP': 'memory_module', 'PCH_DIM3_TEMP': 'memory_module',
}


def _superio_label(name):
    """What a hardware-monitor input is, around the chip's own name for it.

    The bare names - SYSTIN, CPUTIN, SMBUSMASTER 0 - read on the page as keys. This says only
    what the chip itself says an input is - a pin, a reading it fetches over SMBus or PECI, or a
    forward from the chipset - and never what a pin is wired to, which is the board's and not
    known here (see NCT_ATTACHED).
    """
    if name.startswith('SMBUSMASTER'):
        return 'hardware monitor, over SMBus (%s)' % name
    if name.startswith('PECI'):
        return 'processor, over PECI (%s)' % name
    if name.startswith('PCH_'):
        return 'from the chipset (%s)' % name
    return 'hardware monitor pin %s' % name


def read_superio(chip=None):
    """The hardware monitor's own temperature inputs, through chip.py.

    Returns (entries, reason). The entries have the same shape as every other
    sensor in this file, so that read() can put them in one list and a page
    can render one table; the reason is what to say when there are none.

    Four ways it can be unavailable and all four are ordinary:

      * chip.py is not importable - 'no-chip-module'. This is not an x86
        appliance, or the plugin is running from a checkout.
      * the helper is not installed - 'no-helper'.
      * this chip.py has no temps() - 'no-passthrough'. Kept live for ever on
        purpose: an OPNsense upgrade leaves the old daemon running, so a
        chip.py from one version will meet a helper from another.
      * the helper answered and refused - the refusal's own slug: 'busy',
        'chip', 'no-device', 'no-permission', 'readback', and
        'no-helper-command' for a helper that predates `temp`.

    None of them raises. A sensor reader is not a place to discover an
    exception at three in the morning, and a fan controller that loses its
    temperatures fails safe by handing the fans back - which on this appliance
    means 20 percent.

    ONE SENSOR PER SOURCE, NOT ONE PER REGISTER, and that is the only
    reshaping this function does. The chip mirrors one pin into several
    registers: on this appliance SYSTIN is the source of all four fan-control
    registers and PECI Agent 0 is the source of two more (V, 2026-09-22), so a
    row-per-register list would show a page seven board sensors where the
    board has two, and count four copies of one thermistor as four
    measurements. The registers a source was read from are kept in
    `registers`, with each one's raw bytes and decode, so nothing is lost and
    a disagreement between two registers holding the same source is visible
    rather than averaged away.

    The KEY is the source, not the register, for the same reason but with
    teeth: the Tracker keys history on it, and a register that was re-pointed
    at another source would otherwise blend two sensors into one history and
    call the result a constant, or worse, not.
    """
    try:
        import chip as chip_module
    except ImportError:
        return [], 'no-chip-module'

    try:
        handle = chip or chip_module.Chip()
        if not handle.installed():
            return [], 'no-helper'
        answer = getattr(handle, HELPER_TEMP_METHOD)()
    except AttributeError:
        return [], 'no-passthrough'
    except Exception as exc:                      # noqa: BLE001 - see below
        # Deliberately broad. chip.ChipError and its four subclasses are the
        # expected refusals, but this function's contract to a fan controller
        # is that it returns a list; a sensor reader is not a place to
        # discover a new exception type at three in the morning.
        #
        # Code 1 is given its own word. `temp` is called with no arguments, so
        # the only way this plugin can be told it called the helper wrongly is
        # that the helper on this machine is older than the command - which is
        # a thing to say plainly rather than as "a bug in this plugin".
        if getattr(exc, 'code', None) == 1:
            return [], 'no-helper-command'
        return [], getattr(exc, 'slug', 'error')

    by_source = {}
    order = []
    for row in (answer or {}).get('inputs', []):
        if not row.get('present', True):
            continue

        source = NCT_TEMP_LABEL.get(row.get('src'), None)
        if row.get('src_known') is False:
            # The CHIP says it has no such source - that is its own mask and
            # not this file's table. NCT_TEMP_LABEL holds the whole family's
            # names, so it will happily name a number the chip in front of us
            # does not have, and a row carrying that name is a row naming a
            # sensor that is not there. Dropping the name leaves the row
            # visible, keyed and counted on its number - "superio source 31",
            # named_by_driver False, attached unknown - which is the honest
            # shape for a register holding something nobody can identify.
            #
            # Explicit False only, never a missing key: a helper older than
            # `src_known` omits it, and this must keep naming rows for one of
            # those. On this appliance all seven rows are src_known true (V,
            # 2026-09-22) and this branch does not run.
            source = None
        name = row.get('src_name') or source or ('source %s' % row.get('src'))
        key = 'superio:%s' % (source or 'src%s' % row.get('src'))
        celsius = row.get('celsius')
        celsius = celsius if isinstance(celsius, (int, float)) else None

        where = {
            'register': row.get('reg'),
            'raw': row.get('raw'),
            'celsius': celsius,
            'role': row.get('role'),
            'fan_channel': row.get('fan_channel'),
            'stable': row.get('stable'),
        }
        if row.get('unreadable'):
            where['unreadable'] = row['unreadable']

        entry = by_source.get(key)
        if entry is None:
            entry = {
                'key': key,
                'name': 'superio %s' % name,
                'source': 'superio',
                'group': 'board',
                'kind': 'board_input',
                'label': _superio_label(name),
                # A claim where the chip makes one, 'unknown' everywhere else.
                # See the table above: on a pin this is the honest answer and
                # on a forward it is what the source WOULD be measuring.
                'attached': NCT_ATTACHED.get(source, 'unknown'),
                'named_by_driver': source is not None,
                'celsius': celsius,
                'raw': row.get('raw'),
                'units': 'C' if celsius is not None else None,
                'register': row.get('reg'),
                'registers': [],
                'src': row.get('src'),
                'sensor_type': row.get('type'),
                'pin': row.get('pin'),
                'offset_c': row.get('offset_c'),
                'selected_by': [],
            }
            # The helper's own word for why this is not a temperature -
            # unassigned, disabled-in-chip, unstable, implausible,
            # source-silent - carried up unchanged. Rewording a refusal at
            # this distance from the register loses the only detail that lets
            # somebody check it.
            if celsius is None:
                entry['unreadable'] = row.get('unreadable') or 'not-reported'
            if row.get('enabled') is False:
                entry['celsius'] = None
                entry['units'] = None
                entry['unreadable'] = 'disabled-in-chip'
            by_source[key] = entry
            order.append(key)
        elif entry['celsius'] is None and celsius is not None:
            # A later register held a reading where the first did not. Take
            # it, and leave both in `registers` so the difference is visible.
            entry['celsius'] = celsius
            entry['raw'] = row.get('raw')
            entry['register'] = row.get('reg')
            entry['units'] = 'C'
            entry.pop('unreadable', None)

        entry['registers'].append(where)
        for channel in row.get('selected_by') or []:
            if channel not in entry['selected_by']:
                entry['selected_by'].append(channel)

    for entry in by_source.values():
        entry['selected_by'].sort()

    return [by_source[key] for key in order], 'ok'


# --------------------------------------------------------------------------
# IPMI
#
# There is no BMC on the reference appliance and this file does not pretend
# there might be: /dev/ipmi* does not exist (V) and dmidecode's type 38 table,
# which is where an IPMI device would announce itself, is empty (V,
# `dmidecode -t 38` printed only its banner).
#
# WHAT A MACHINE THAT HAS ONE SHOULD DO, written down here rather than
# implemented against nothing:
#
#   * read it with `ipmitool sdr type temperature`, ONLY if /dev/ipmi0 exists
#     AND ipmitool is installed. Never kldload ipmi(4) to make the device
#     appear - DESIGN.md section 11 already says this plugin does not load
#     modules, and loading a driver that takes the KCS interface on a
#     firewall to read
#     a temperature is not a fan plugin's business.
#   * treat every SDR row as group 'board', confidence 'named': the BMC's
#     names are the board vendor's own and are better than anything this file
#     could infer. `attached` stays 'unknown' for the same reason it does for
#     a super-I/O pin - "Inlet Temp" is a label, not a measurement of where
#     the probe is.
#   * keep it OUT of the curve path. An ipmitool call goes over KCS and takes
#     hundreds of milliseconds (R - not measured here, there is nothing to
#     measure it on), which is a large fraction of a ten-second loop for a
#     reading that moves slowly. It belongs on the page and in the history, at
#     its own slower cadence.
#
# AND THE PARSING IS NOT WRITTEN. That is deliberate and it is the house rule
# rather than laziness: there is no BMC here, so any ipmitool output parsing in
# this file would be code that has never once run against the thing it claims
# to read. read_ipmi() therefore detects the BMC honestly and then says
# 'not-implemented' in as many words, which is a true sentence a page can show
# and an accurate thing to hand the next person. What it needs is somebody
# with a BMC and twenty minutes:
#
#     ipmitool -c sdr type temperature
#     Inlet Temp,24,degrees C,ok
#     Exhaust Temp,38,degrees C,ok
#
# four comma-separated fields (R, from the documented -c output; not observed
# here) - name, value, unit, state - one row per sensor, skipping rows whose
# state is not ok and rows whose unit is not degrees.

IPMI_DEVICE = '/dev/ipmi0'
IPMITOOL = '/usr/local/bin/ipmitool'


def ipmi_available():
    """Whether this machine has both a BMC device and a tool to ask it.

    V on the reference appliance: False, and for the stronger of the two
    reasons - /dev/ipmi* does not exist at all, and dmidecode's type 38 table,
    which is where an IPMI device announces itself, is empty.
    """
    try:
        return os.path.exists(IPMI_DEVICE) and os.access(IPMITOOL, os.X_OK)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return False
        raise


def read_ipmi():
    """([], reason) - the same shape read_superio() has, and always empty.

    Two different reasons, and the difference is the whole point of the
    function existing at all: 'no-bmc' means this machine has nothing to ask,
    and 'not-implemented' means it has, and this plugin cannot yet. A page
    that shows the second one is a page that tells somebody there is a
    temperature here worth having.
    """
    return [], ('not-implemented' if ipmi_available() else 'no-bmc')


# --------------------------------------------------------------------------
# Reading

CPLD_TOOL = '/usr/local/sbin/xgs3300-sensors'


def read_cpld():
    """The appliance's own sensor bank, where the board has one the host can reach.

    On the Sophos XGS 3300 the temperatures and fans are registers on a CPLD behind
    an SMBus face at slave 0x60 on the host's main SMBus controller, and the tool
    os-xgs-npu installs reads them with the vendor's own transaction and arithmetic
    (hi + lo / 8 for a temperature, a fan byte in units of 300 rpm). This asks that
    tool for its one-line JSON and returns entries in the shape every other source
    here uses, so the page renders one table and decide() can use the groups.

    Three readings: the CPU as the board sees it (beside amdtemp's die reading, and
    usually a few degrees apart), the NPU - the OCTEON TX coprocessor, the hottest
    thing in the box and the one no kernel driver reports - and the inlet air.
    The fans come back as tachometers through fanctl.cpld_tach(), not as sensors.

    Absent tool, absent board, or a tool that failed: an empty list, and nothing
    else in this file notices. Nothing here is written to the hardware.
    """
    entries = []
    if not os.access(CPLD_TOOL, os.X_OK):
        return entries
    raw = _run([CPLD_TOOL, 'json'], timeout=10)
    if not raw:
        return entries
    try:
        values = json.loads(raw.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return entries
    for key, label, group, kind, attached, limit in (
            ('cpu_c', 'CPU, as the board reads it', 'cpu', 'cpu_board', 'cpu_die', 95.0),
            ('npu_c', 'NPU (the coprocessor die)', 'npu', 'npu_die', 'coprocessor_die', 100.0),
            ('inlet_c', 'inlet air', 'inlet', 'ambient', 'chassis_inlet', 45.0)):
        celsius = values.get(key)
        if not isinstance(celsius, (int, float)):
            continue
        entries.append({
            'key': 'cpld:%s' % key,
            'name': 'cpld.%s' % key,
            'label': label,
            'group': group,
            'kind': kind,
            'source': 'cpld',
            'celsius': float(celsius),
            'raw': '%.3fC' % celsius,
            'units': 'C',
            'limit_c': limit,
            'headroom_c': limit - float(celsius),
            'attached': attached,
            'named_by_driver': True,
        })
    return entries


NPU_LINK = '/usr/local/opnsense/scripts/npuctl/mgmt-link.sh'
# One line run on the coprocessor over the management link: every thermal zone of its own
# Linux as "zone <type> <millidegrees>", then the temperature of the PHY behind every copper
# label as "phy <label> <millidegrees>" (xgs-phy-temperature prints "phyN temperatures T 0").
NPU_PROBE = ('for z in /sys/class/thermal/thermal_zone*; do echo zone $(cat $z/type) $(cat $z/temp); done; '
             'for l in 1 2 3 4 5 6 7 8 9 10 11 12; do '
             'echo phy $l $(xgs-phy-temperature -l $l 2>/dev/null | awk \'{print $3}\'); done')
NPU_CACHE_SECONDS = 60
_npu_cache = {'at': 0.0, 'entries': []}


def read_npu():
    """The coprocessor's own temperatures, on the appliance that has a management link to it.

    On the Sophos XGS 136 the coprocessor (an ARMADA CN9131) runs its own Linux, and that
    Linux reports its thermal zones - the SoC sensor, the four application cores, the two
    communication-processor dies - and, through the vendor's xgs-phy-temperature, the PHY
    behind every copper port. None of it is reachable from a FreeBSD driver; all of it is one
    ssh away once os-xgs-npu has given the management link its place (that project's
    mgmt-link.sh). This asks over that link, caches for a minute because the twelve PHY
    readings are twelve MDIO transactions on the far side, and returns entries in the shape
    every other source here uses.

    No link script, no link, a link that does not answer: an empty list, and nothing else in
    this file notices. Nothing here is written to the hardware.
    """
    now = time.time()
    if now - _npu_cache['at'] < NPU_CACHE_SECONDS:
        return list(_npu_cache['entries'])
    _npu_cache['at'] = now
    _npu_cache['entries'] = []
    if not os.access(NPU_LINK, os.X_OK) or not _run(['/sbin/ifconfig', 'mvmgmt0'], timeout=5):
        return []
    raw = _run(['/bin/sh', NPU_LINK, 'ssh', NPU_PROBE], timeout=25)
    entries = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 3 or not re.match(r'^-?[0-9]+$', parts[2]):
            continue
        celsius = int(parts[2]) / 1000.0
        if parts[0] == 'zone':
            zone = parts[1]
            if zone == 'ap-thermal-ic':
                label, kind, limit = 'NPU, the SoC sensor', 'npu_die', 100.0
            elif zone.startswith('ap-thermal-cpu'):
                label, kind, limit = 'NPU core %s' % zone[len('ap-thermal-cpu'):], 'npu_core', 100.0
            elif zone.endswith('.thermal'):
                # The communication processors sit at 0xf2000000 + n * 0x2000000 on the ARMADA
                # 8K family, so the zone's address names which one: f2400078 is CP0, f4400078
                # CP1. Printed as the address, the label read as a register.
                address = zone[:-len('.thermal')]
                try:
                    label = 'NPU CP%d die' % ((int(address, 16) - 0xf2000000) // 0x2000000)
                except ValueError:
                    label = 'NPU CP die at %s' % address
                kind, limit = 'npu_die', 100.0
            else:
                label, kind, limit = 'NPU %s' % zone, 'npu_other', 100.0
            key, group, attached = 'zone:%s' % zone, 'npu', 'coprocessor_die'
        elif parts[0] == 'phy':
            label, kind, limit = 'PHY of Port%s' % parts[1], 'phy', 105.0
            key, group, attached = 'phy:%s' % parts[1], 'nic', 'port_phy'
        else:
            continue
        entries.append({
            'key': 'npu:%s' % key,
            'name': 'npu.%s' % key,
            'label': label,
            'group': group,
            'kind': kind,
            'source': 'npu',
            'celsius': celsius,
            'raw': '%s' % parts[2],
            'units': 'C',
            'limit_c': limit,
            'headroom_c': limit - celsius,
            'attached': attached,
            'named_by_driver': True,
        })
    _npu_cache['entries'] = entries
    return list(entries)


def read(catalogue=None, include_drives=False, include_superio=False,
         tracker=None):
    """Every temperature this machine reports, grouped, named and rated.

    Returns the document the status file carries verbatim:

        {'at': 1774000000.0,
         'sensors': [{'key': 'sysctl:dev.cpu.0.temperature',
                      'name': 'dev.cpu.0.temperature',
                      'label': 'core 0', 'group': 'cpu', 'kind': 'cpu_core',
                      'source': 'sysctl', 'celsius': 82.0, 'raw': '82.0C',
                      'units': 'C', 'limit_c': 100.0, 'headroom_c': 18.0,
                      'attached': 'cpu_die', 'confidence': 'measured'}, ...],
         'groups': {'cpu': 82.0, 'pch': 55.5},      # the hottest in each
         'present': ['cpu', 'pch', 'acpi'],
         'unreadable': ['dev.nvme.0.temperature'],
         'unreadable_why': [{'name': ..., 'why': 'implausible'}],
         'constant': ['hw.acpi.thermal.tz0.temperature'],
         'superio': 'ok',
         'ipmi': 'no-bmc',
         'counts': {'found': 10, 'read': 9, 'sysctl': 7,
                    'smartctl': 1, 'superio': 2}}

    Every sensor carries `key`, `name`, `label`, `group`, `kind`, `source`,
    `celsius`, `raw`, `units`, `attached` and `confidence`, whichever of the
    three sources it came from. `format`, `declared` and `declared_units` are
    sysctl-only and absent on a drive or a super-I/O input - they describe a
    kernel node and there is no kernel node behind those. Callers read them
    with .get().

    `unreadable` is the list of nodes that exist and did not give a number
    this time, each with the reason. It is kept apart from a group that is
    simply absent, because a sensor that was there a minute ago and is not now
    is the one case where something has gone wrong rather than being merely
    unsupported.

    `constant` is the list of sensors this file has proved are not
    measurements. They are still in `sensors`, with their numbers, because the
    number is a true statement about what the kernel returns; they are named
    here so the page can say what they are and `decide()` can refuse them.

    include_drives is off by default and that is a cost decision, not a
    correctness one: smartctl is cheap (13-15 ms, V) but it is an ATA command
    to a device, and a fan controller has no business sending one every ten
    seconds for a reading it will not use. The page asks for it; the loop does
    not.

    include_superio is off by default, and it stays off by default here even
    now that the command exists. The DEFAULT is a decision about the fan loop
    and not about the command: the loop runs every ten seconds and every
    superio read is another pass over a window that is shared with a kernel
    driver holding no lock, for readings that on this appliance are one
    thermistor and one dead PECI agent. The page asks for it, the status file
    carries it, and a machine whose only honest sensor is on the super-I/O -
    which is a real machine, just not this one - turns it on in the loop
    deliberately, where somebody can see that the decision was made.

    What it costs when it is on (V, 2026-09-22, this appliance): one helper
    process, about 70 port accesses, and a non-blocking lock that is either
    taken at once or refused. Fifty-two reads at fifteen-second intervals
    alongside the running daemon, whose own `status` call comes round every
    ten, were refused as busy exactly zero times.
    """
    entries = list(catalogue if catalogue is not None else discover())

    names = [entry['name'] for entry in entries]
    limits = [entry['limit_name'] for entry in entries if entry.get('limit_name')]
    tree = _sysctl_values(names + limits)

    sensors = []
    unreadable = []
    unreadable_why = []

    for entry in entries:
        raw = tree.get(entry['name'])
        celsius, units = parse_units(raw)

        sensor = dict(entry)
        sensor['celsius'] = celsius
        sensor['raw'] = raw
        sensor['units'] = units if celsius is not None else None

        if celsius is None:
            sensor['unreadable'] = units
            unreadable.append(entry['name'])
            unreadable_why.append({'name': entry['name'], 'why': units})
        else:
            limit = parse(tree.get(entry.get('limit_name'))) if entry.get('limit_name') else None
            if limit is not None:
                # Headroom is reported even when it is negative, and that is
                # not a detail. A first draft only filled it in when the limit
                # was above the reading, which withheld the number in the one
                # case it exists for: a core past tjmax must read -1.0 on the
                # page, not blank.
                sensor['limit_c'] = limit
                sensor['headroom_c'] = round(limit - celsius, 1)

        sensors.append(sensor)

    if include_drives:
        for entry in read_drives():
            sensors.append(entry)
            if entry.get('celsius') is None:
                unreadable.append(entry['name'])
                unreadable_why.append({'name': entry['name'],
                                       'why': entry.get('unreadable', 'absent')})

    superio_state = 'not-asked'
    if include_superio:
        found, superio_state = read_superio()
        for entry in found:
            sensors.append(entry)
            if entry.get('celsius') is None:
                unreadable.append(entry['name'])
                unreadable_why.append({'name': entry['name'],
                                       'why': entry.get('unreadable', 'absent')})

    for entry in read_cpld():
        sensors.append(entry)
    for entry in read_npu():
        sensors.append(entry)

    # A copy beside its original was the same sensor twice, the copy under its sysctl name - the
    # XGS 3300's NPU at 76.6 C as dev.octep.0.temperature.npu and at 76.5 C as the CPLD's. It is
    # kept only when the original was not read, which is when it is worth having.
    read_here = {s['name'] for s in sensors if s.get('celsius') is not None}
    sensors = [s for s in sensors if _mirror_of(s['name']) not in read_here]
    unreadable = [n for n in unreadable if _mirror_of(n) not in read_here]
    unreadable_why = [u for u in unreadable_why if _mirror_of(u['name']) not in read_here]

    # Confidence, last, because it is the only part that needs history.
    at = time.time()
    if tracker is not None:
        tracker.observe(sensors, at)
    for sensor in sensors:
        if sensor.get('celsius') is None:
            sensor['confidence'] = 'unknown'
            continue
        if tracker is not None:
            sensor['confidence'] = tracker.confidence(sensor)
        else:
            sensor['confidence'] = 'named' if sensor.get('named_by_driver') else 'unknown'

    hottest = {}
    for sensor in sensors:
        celsius = sensor.get('celsius')
        if celsius is None:
            continue
        group = sensor['group']
        if group not in hottest or celsius > hottest[group]:
            hottest[group] = celsius

    return {
        'at': at,
        'sensors': sensors,
        'groups': hottest,
        'present': [group for group in GROUPS if group in hottest],
        # A LIST OF NAMES, as it has always been. The reasons live beside it
        # in `unreadable_why` and not inside it, because the page joins this
        # list into a sentence - index.volt:320-324, `unreadable.join(', ')` -
        # and a list of objects renders there as "[object Object]". A warning
        # box that says that is worse than no warning box: it is the one line
        # on the page that appears exactly when something has gone wrong.
        'unreadable': unreadable,
        'unreadable_why': unreadable_why,
        'constant': [s['name'] for s in sensors if s.get('confidence') == 'constant'],
        'superio': superio_state,
        # Two stat() calls, always run. A machine that has a BMC and is not
        # being asked is worth a line on a page every cycle; a machine that
        # has none costs a microsecond to say so.
        'ipmi': read_ipmi()[1],
        'counts': {
            'found': len(sensors),
            'read': sum(1 for s in sensors if s.get('celsius') is not None),
            'sysctl': sum(1 for s in sensors if s['source'] == 'sysctl'),
            'smartctl': sum(1 for s in sensors if s['source'] == 'smartctl'),
            'superio': sum(1 for s in sensors if s['source'] == 'superio'),
            'cpld': sum(1 for s in sensors if s['source'] == 'cpld'),
            'npu': sum(1 for s in sensors if s['source'] == 'npu'),
        },
    }


# --------------------------------------------------------------------------
# History, and the one thing history proves

class Tracker(object):
    """Keeps what each sensor has done, so a constant can be told from a
    sensor that happens to be steady.

    This is the whole answer to the tz0 problem, and it is the only thing in
    this file that needs more than one reading to work. It is also the
    substrate the statistics on the page are drawn from: min, max, last, n and
    the span they cover, per sensor, which is everything a summary needs and
    nothing it does not.

    Bounded by construction: HISTORY samples per sensor, and sensors that
    stop appearing are dropped. A daemon that runs for a year must not grow.
    """

    def __init__(self, history=HISTORY):
        self.history = history
        self.samples = {}        # key -> [(at, celsius), ...]
        self.moved = set()       # keys that have been seen to change, ever

    def observe(self, sensors, at=None):
        """Record one reading of each sensor.

        `at` is the time the reading was taken. The caller passes the same
        timestamp it put in the reading document, so that the history and the
        status page agree; a test passes a recorded one, which is the only way
        a half-hour rule can be exercised without waiting half an hour.
        """
        now = time.time() if at is None else float(at)
        seen = set()
        for sensor in sensors:
            celsius = sensor.get('celsius')
            if celsius is None:
                continue
            key = sensor['key']
            seen.add(key)
            series = self.samples.setdefault(key, [])
            if series and series[-1][1] != celsius:
                self.moved.add(key)
            series.append((now, celsius))
            if len(series) > self.history:
                del series[:len(series) - self.history]

        for key in [k for k in self.samples if k not in seen]:
            # A sensor that has gone is not a sensor that is constant. Drop it
            # rather than letting an old flat series describe a device that
            # has been unplugged.
            del self.samples[key]
            self.moved.discard(key)

    def peer_swing(self, key, since):
        """(largest swing by any OTHER sensor since `since`, whose it was).

        The clause that makes `constant` honest, and the one the first draft
        got too cheap. On a firewall that has been idle in a cold room for an
        hour every sensor holds its value and none of them is a constant; a
        sensor that did not move is only suspicious when the machine
        demonstrably got hotter around it. `since` is a timestamp so that an
        old swing cannot be used to convict a sensor of standing still now.
        """
        best, whose = 0.0, None
        for other, series in self.samples.items():
            if other == key:
                continue
            values = [value for at, value in series if at >= since]
            if len(values) < 2:
                continue
            swing = max(values) - min(values)
            if swing > best:
                best, whose = swing, other
        return round(best, 1), whose

    def summary(self, key):
        """min, max, last, n and the seconds they span, or None."""
        series = self.samples.get(key)
        if not series:
            return None
        values = [value for _, value in series]
        return {
            'n': len(series),
            'seconds': round(series[-1][0] - series[0][0], 1),
            'min': min(values),
            'max': max(values),
            'last': values[-1],
            'distinct': len(set(values)),
        }

    def confidence(self, sensor):
        """measured, named, constant or unknown, for one sensor.

        Writes the evidence for a `constant` into the sensor, because an
        accusation with no evidence beside it is not something a page should
        print: the reader gets the sample count, the window, how far the
        machine moved around it and which sensor moved that far.

        `measured` is sticky. A sensor that has been seen to change is a
        sensor, for the rest of this process's life, even if it then holds a
        value all afternoon. The asymmetry is deliberate and it is in the safe
        direction: the cost of forgetting is that a real sensor gets accused,
        and that removes it from a fan decision.
        """
        key = sensor['key']
        if key in self.moved:
            return 'measured'

        series = self.samples.get(key) or []
        if (len(series) >= CONSTANT_MIN_SAMPLES
                and (series[-1][0] - series[0][0]) >= CONSTANT_MIN_SECONDS):
            swing, whose = self.peer_swing(key, series[0][0])
            if swing >= CONSTANT_PEER_SWING_C:
                sensor['constant_evidence'] = {
                    'samples': len(series),
                    'seconds': round(series[-1][0] - series[0][0], 1),
                    'held': series[-1][1],
                    'peer_swing_c': swing,
                    'peer': whose,
                }
                return 'constant'

        return 'named' if sensor.get('named_by_driver') else 'unknown'


# --------------------------------------------------------------------------
# Choosing

def groups_from(text):
    """A settings string such as 'cpu,pch' as a list of known group names.

    Anything unrecognised is dropped rather than carried: a group name that is
    not one of ours would show up later as a missing sensor and fail the
    machine safe for a reason that was really a typing mistake on a settings
    page. The model's own mask stops that first; this is the second line.
    """
    wanted = []
    for part in str(text or '').split(','):
        part = part.strip().lower()
        if part in GROUPS and part not in wanted:
            wanted.append(part)
    return wanted


def decide(reading, wanted):
    """The temperature a fan decision should be made from, and where from.

    Returns (celsius, [names it was taken from], [groups asked for that gave
    nothing]), unchanged from the signature the daemon already calls.

    The hottest reading among the groups asked for, never an average of them.
    An average has the property nobody wants here: one core at 85 C and three
    at 40 C averages to something a curve is happy with, and the machine still
    has a core at 85 C. What is being defended against is the hottest thing in
    the box, so the hottest thing in the box is what the curve is fed.

    A sensor whose confidence is `constant` is NOT eligible, and this is the
    behaviour change that matters. On the reference appliance the default
    selection was `cpu,pch` and is unaffected; a machine whose owner had
    selected `acpi` would, on this board, have been following 27.9 C for ever.

    REFUSING A SENSOR IS NOT THE SAME AS NOT HAVING ONE, and `missing` says
    only the second. A group that answered and was refused is left out of
    `missing` on purpose - see the long note below - because the caller reads
    `missing` as a broken contract and hands the fans back to the firmware on
    it, and a cold constant could never have raised the answer anyway. The
    refused sensors are named in reading['constant'], which is where a page
    should look to say so.

    A caller that asked for nothing but constants still gets None for the
    temperature and still fails safe: `best` is never set. That is the
    correct end of it - DESIGN.md section 4, line 6 of the contract already
    says a decision with no sensor behind it does not happen.

    A caller that gets None must not fall back to a constant. There is no
    argument to make it do so and none will be added.
    """
    names = []
    best = None
    missing = []

    for group in wanted:
        if group not in GROUPS:
            missing.append(group)
            continue
        answered = [entry for entry in reading.get('sensors', [])
                    if entry.get('group') == group
                    and entry.get('celsius') is not None]
        found = [entry for entry in answered
                 if entry.get('confidence') != 'constant']
        if not found:
            # A group that gave NO reading at all is missing, and a caller is
            # right to treat that as a broken contract. A group whose only
            # readings this file has convicted as constants is a different
            # fact: the sensor answered, this file will not follow it, and the
            # decision is still being made from something honest.
            #
            # They were the same list in the first draft, and that was a fan
            # safety bug rather than a wording one. fanctl.py fails its
            # sensors contract on a non-empty `missing` ALONE
            # (fanctl.py:1076-1082), and a failed contract hands the fans back
            # to the firmware - 20 percent on this appliance. So convicting
            # one slow-but-real sensor would have taken the plugin off the fan
            # altogether, for a sensor that could never have raised the answer
            # anyway: decide() takes the HOTTEST, and a cold constant is never
            # the hottest.
            #
            # V, 2026-09-22 (/root/fanwork2/adv_test.py): a chipset held at one
            # half-degree step for 1800 s while the cores swung 20 C was
            # convicted - which is a thing a 0.5 C sensor in a lump of metal
            # does - and decide() returned 55.0 C from the cores together with
            # missing=['pch', 'acpi']. Correct number, contract broken, fans
            # back to 20 percent.
            #
            # Refusing to FOLLOW a constant is unchanged, exactly as it was.
            # Only the blast radius of the accusation changes. A caller that
            # asked for nothing BUT constants still gets None for the
            # temperature and still fails safe, because `best` is never set.
            # The convicted sensors are named in reading['constant'].
            if not answered:
                missing.append(group)
            continue
        for entry in found:
            if best is None or entry['celsius'] > best:
                best = entry['celsius']
                names = [entry['name']]
            elif entry['celsius'] == best:
                # Said out loud rather than picked arbitrarily. Four cores at
                # the same value is the usual case on an idle firewall, and a
                # status line naming one of them would imply the other three
                # said something else.
                names.append(entry['name'])

    return best, names, missing


def suggested_groups(reading):
    """What a curve on THIS machine should follow, and the reason, in order.

    Returns [(group, reason_key), ...], best first, or [] when this machine
    has nothing a curve may follow. `reason_key` is a key the page
    translates - no sentence leaves this file.

    The ordering is an argument, not a preference:

      1. `cpu`, when it is there. It is the only group whose attachment is
         known by construction - a core's thermal sensor is ON the die - it
         is the fastest-moving temperature in an x86 box, and it is the one
         with a published limit to measure headroom against. On the reference
         appliance the cores sat at 82 C against a tjmax of 100 while the
         board's own thermal zones reported 27.9 (V, same minute). Anything
         that followed the zones would have decided the machine was cold.
      2. `pch`, second and never alone. It is real and it is slow: on this
         machine it moved 37.5 -> 39.0 over the minute in which the cores
         moved eleven degrees (V). A curve following only the chipset reacts
         to a stopped fan several minutes late. Its value is exactly that
         slowness - it is the reading that notices heat that has had time to
         soak into the chassis - which makes it a good second and a poor
         first.
      3. `board`, when there is one, it has been identified, AND it has been
         seen to move. A super-I/O input whose `attached` is 'unknown' is not
         offered as a default no matter how sensible its number looks - and
         nor is one whose attachment the CHIP claims but which has never
         changed. That second clause is this appliance's doing: a source that
         says cpu_die and holds one number is the exact shape of the failure
         this file exists to prevent, and being named by the hardware is not
         evidence of being alive. The helper refuses the common case at the
         register - a forwarded source sitting at its reset value arrives with
         no number at all - and this is the second line, for one that is stuck
         at something plausible instead of at zero. It costs a freshly booted
         machine one heat cycle before a live board input is RECOMMENDED; it
         never stops anybody selecting one.
      4. `acpi`, last, and only when it has been seen to MOVE. On this board
         it is a constant; on a board where the firmware wired it to something
         it may be the only sensor there is.

    `nvme`, `drive`, `nic` and `dimm` are never suggested. They are real
    temperatures and they do not describe what a chassis fan can change; a
    drive that is warm is warm because it is busy.
    """
    have = {}
    for sensor in reading.get('sensors', []):
        if sensor.get('celsius') is None or sensor.get('confidence') == 'constant':
            continue
        have.setdefault(sensor['group'], []).append(sensor)

    order = []
    if 'cpu' in have:
        order.append(('cpu', 'on_die_and_fastest'))
    if 'pch' in have:
        order.append(('pch', 'slow_but_real'))
    if any(s.get('attached') != 'unknown' and s.get('confidence') == 'measured'
           for s in have.get('board', [])):
        order.append(('board', 'identified_board_input'))
    if any(s.get('confidence') == 'measured' for s in have.get('acpi', [])):
        order.append(('acpi', 'moves_on_this_board'))
    return order


# --------------------------------------------------------------------------

def _cli():
    """sensors.py [groups] [--drives] [--superio] [--watch n] [--interval s]

    One shot prints what is here now. `--watch n` takes n samples `--interval`
    seconds apart and adds the per-sensor history to each one.

    `--interval` exists because of CONSTANT_MIN_SECONDS. Nothing is called a
    constant until half an hour of samples says so, so a short watch will
    never print one - which is correct, and is also a trap for anybody
    checking that the rule works. The run that settles it is

        sensors.py --watch 360 --interval 5

    thirty minutes, and it is meant to be left going while somebody makes the
    machine hot and lets it cool again.
    """
    import sys

    argv = sys.argv[1:]
    watch = 0
    interval = 5.0
    wanted = None
    drives = superio = False

    while argv:
        arg = argv.pop(0)
        if arg == '--drives':
            drives = True
        elif arg == '--superio':
            superio = True
        elif arg == '--watch':
            watch = int(argv.pop(0)) if argv else 12
        elif arg == '--interval':
            interval = float(argv.pop(0)) if argv else 5.0
        elif not arg.startswith('-'):
            wanted = arg
        else:
            sys.stderr.write((_cli.__doc__ or '').strip().splitlines()[0] + '\n')
            return 1

    tracker = Tracker() if watch else None
    reading = None
    for round_number in range(max(watch, 1)):
        reading = read(include_drives=drives, include_superio=superio,
                       tracker=tracker)
        if watch and round_number < watch - 1:
            time.sleep(interval)

    if tracker is not None:
        for sensor in reading['sensors']:
            summary = tracker.summary(sensor['key'])
            if summary:
                sensor['history'] = summary

    if wanted is not None:
        celsius, names, missing = decide(reading, groups_from(wanted))
        reading['decided'] = {'celsius': celsius, 'from': names, 'missing': missing}
    reading['suggested'] = suggested_groups(reading)

    json.dump(reading, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
