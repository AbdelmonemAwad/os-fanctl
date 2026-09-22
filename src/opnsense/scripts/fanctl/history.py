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

    What this appliance has been doing, kept small enough to keep for ever.

    The plugin already knows, every cycle, what every temperature reads, what
    every fan is turning at, and what duty it asked for. It throws all of it
    away. This file is what stops that, and the reason it is worth a file is
    one measurement the owner of the reference appliance had to take by hand:
    with the fan at 20% the CPU sat at 81 C and the fan turned 1332 rpm; at
    100% it turned 5844 rpm and the CPU sat at 75-80 C, while the chipset went
    UP two degrees. A 4.4x increase in fan speed for about six degrees. That is
    the single most useful fact anybody could learn about this chassis and
    nothing in the machine remembered it. stats.py turns this record into that
    fact; this file is the record.

    WHAT IS SAMPLED, AND WHAT IT COSTS

    Once a minute: every temperature the sysctl tree exposes, every fan's
    tachometer, the duty this plugin believes each driven channel is at, and -
    the one field that is not about fans at all - the CPU busy percentage over
    the interval. The busy figure is there because without it the duty-versus-
    temperature statistic is not merely imprecise, it comes out backwards; see
    stats.py.

    Measured on the reference appliance, 2026-09-22 (V), FreeBSD 15.1-RELEASE-p3,
    Python 3.13.15, i5-6500, over 40 rounds each:

        sysctl -e over the four PREFIXES, as sensors.read() does it
                                              median 40.8 ms, p90 94.6, max 127.0
        sysctl -e over the seven named LEAVES  median  1.5 ms, p90  2.0, max   4.3
        the same seven leaves + kern.cp_times,
              in ONE call                      median  1.3 ms, p90  1.3, max   1.4
        fanctl-io status, all five channels    median  2.1 ms, p90  2.4, max   2.6

    THE PREFIX-WALK FIGURE DOES NOT REPRODUCE, and saying so is worth more than
    the figure. Re-measured on the same machine on 2026-09-22 with the fan held
    at 100%, 40 rounds: median 10.9 ms, p90 44.7, max 57.8 - a quarter of the
    median above, with the same long tail. The two leaf figures reproduced
    exactly (1.3 ms and 1.7 ms). The cost is dominated by dev.cpu.N.freq, which
    goes out through ACPI and therefore depends on what the CPU is doing at the
    moment it is asked, so neither run is wrong and neither is THE number. What
    survives both is the ratio and the shape: naming the leaves is between 8
    and 30 times cheaper than walking the prefixes, and it has no tail. That is
    what this arrangement rests on, and it does not need the larger number to
    be true. `history.py cost` re-measures the sampling path on whatever
    machine it is run on for exactly this reason.

    So ONE SAMPLE COSTS 3.4 ms of work (1.3 + 2.1), plus the live-ring write.
    take_reading() end to end - the sysctl call, the parse and the busy
    arithmetic - measured a median of 1.9 ms over 20 rounds on 2026-09-22
    (V, `history.py cost 20`), so the parse is about 0.6 ms of it.
    Where the daemon is running it costs less than that, because the daemon has
    already paid for the sensor read and hands it over.

    THE 40 ms IS THE FINDING, AND IT IS NOT WHERE ANYBODY WOULD LOOK. Walking
    `dev.cpu.` as a prefix evaluates all 81 nodes under it (V, `sysctl -N
    dev.cpu. | wc -l` = 81), and `dev.cpu.N.freq` alone costs a median 3.4 ms
    and a maximum of 46.3 ms on this machine because reading it goes out
    through ACPI. Naming the four `dev.cpu.N.temperature` leaves instead costs
    1.8 ms for all four. Discovery is done with `sysctl -N`, which prints names
    without running a single handler and costs 1.7 ms for all four prefixes, so
    the leaf list is found once an hour and read from a cache in between. A
    sensor plugged in during that hour is picked up at the next refresh, which
    is the whole price of the arrangement and is worth paying once an hour.

    WHAT WAS DELIBERATELY NOT OPTIMISED. Reading sysctls through ctypes and
    libc would take the 1.3 ms to something near zero, and it is not worth
    doing: the cron path pays 46 ms to START PYTHON before it reads anything
    (V, measured - a bare interpreter is 18 ms, json adds 12, subprocess 23),
    so removing 1.3 ms from a 49 ms sample is noise, and it would buy that
    noise with a new class of bug - buffer sizes and integer widths - in a
    plugin whose entire argument is that it is careful. `math` and `statistics`
    are not imported here for the same reason turned around: they cost 5 ms of
    interpreter start between them and two small functions in stats.py replace
    them.

    WHERE IT LIVES, AND HOW BIG IT GETS

    Four rings and a step record, each in its own file under /var/db/fanctl,
    and one file per ring is not tidiness - it is the whole write budget. A
    single document holding all four would have to be rewritten every minute
    because the newest ring changed, and the coarse rings would be carried
    through that write 1440 times a day for nothing.

        history-live.json   60 s   x  120 =  2 hours    rewritten every minute
        history-fine.json    5 min x  288 = 24 hours    rewritten every 5 min
        history-hour.json    1 h   x  720 = 30 days     rewritten hourly
        history-day.json     1 d   x  365 =  1 year     rewritten daily
        steps.json                   100 duty changes   rewritten on a change
        trend.json          the document the page reads, rewritten every 5 min

    Measured with every ring filled to its limit, on the reference appliance's
    own series - 7 temperatures, 1 fan with a tachometer, 1 duty, 1 busy - by
    `history.py selftest --sizes` (V, 2026-09-22):

        history-live.json    10,660 B     history-day.json   124,776 B
        history-fine.json    77,785 B     steps.json          72,434 B
        history-hour.json   221,943 B     trend.json           7,581 B
                                          TOTAL              515,179 B = 503 KiB

    and 49,045,992 bytes written per day (46.8 MiB) AT ONE DUTY CHANGE AN HOUR,
    of which the five-minute ring is nearly half. The duty-change rate is an
    assumption, not a measurement, and it is the one number here that somebody
    else's machine will not share: note_duty() rewrites the whole of steps.json
    at the moment of a change, close_steps() rewrites it when that change
    settles, and each close forces a trend write out of cadence. An earlier
    version of this figure weighted steps.json at zero writes a day, which is
    true only of a fan that never changes speed; a curve that moves the duty
    every cycle would write that file 17,280 times a day, which is 1.2 GiB onto
    a flash device. `history.py selftest --sizes` prints the rate it used
    beside the total. The arithmetic is in DESIGN.md §12.3, along with why the
    obvious way to measure the compressed size gives a wrong and flattering
    answer.

    THE WHOLE HISTORY IS BOUNDED AT 503 KiB and reaches that size after a year.
    Every ring has a fixed slot count and every slot has a fixed width. There is
    no path here that appends without discarding - including the layout table,
    which is compacted against the slots that are left at every write; see
    compact_layouts(), which is there because that sentence was not true when
    it was first written.

    The accumulators for the coarse rings are kept IN the live file rather than
    in their own, because they change every minute and the live file is the one
    that is rewritten every minute anyway. A five-minute slot is written once,
    when it closes.

    WHAT IS NOT KEPT, AND WHY

      * Channels with nothing to say. Four of the five channels on the
        reference appliance read 0 rpm for ever and carry a duty of 50% that
        nothing has ever changed (V, fanctl-io status, 2026-09-22). Recording
        them would be 8 of every 13 numbers in a slot describing four headers
        that no fan is on. A channel enters the record when it has reported a
        tachometer at least once, or when this plugin has driven it.
      * Anything the plugin did not measure. A gap is stored as a gap: every
        aggregate carries the seconds it actually watched, and the page prints
        the coverage beside the mean. A mean over a day that the sampler spent
        eleven hours of is a true statement about thirteen hours.
      * Text. The record is integers. Reasons, modes and contract lines belong
        in status.json, which is rewritten in full every cycle anyway.

    THIS FILE NEVER WRITES TO THE CHIP. It has no code that could: the only
    hardware call in it is chip.read_path(), which is read-only on both of its
    paths, and it is not called at all while the daemon is running - the daemon
    hands its own reading over instead, so that two processes are never walking
    the hardware-monitor window at once. That rule is chip.py's and this file
    keeps it rather than restating it.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

STATE_DIR = '/var/db/fanctl'

# Set only while the selftest is deliberately breaking something. It changes where a shielded
# failure is REPORTED and nothing else: to stderr, where the test output is, instead of to the
# operator's system log, where nineteen LOG_ERR lines from a passing test are indistinguishable
# from a real one. See _shielded() and the '/dev/null/state' check in selftest().
SELFTEST = False

# The files are named here and JOINED TO STATE_DIR AT THE MOMENT THEY ARE USED,
# never at import. It reads like a small ceremony for nothing and it is not: a
# path worked out at import is captured by every table that mentions it, so
# pointing this module at a scratch directory - which is the whole of how the
# selftest runs without touching a real appliance - moved some of the files and
# not others. Measured here on 2026-09-22: a selftest aimed at a temporary
# directory wrote its live ring there and its five-minute, hourly and daily
# rings into the REAL /var/db/fanctl of the firewall it was running on,
# seeding a plugin's history with 24 hours of invented readings. One variable
# and one function is the fix, and it is the same shape that makes the module
# testable in the first place.
LIVE_NAME = 'history-live.json'
FINE_NAME = 'history-fine.json'
HOUR_NAME = 'history-hour.json'
DAY_NAME = 'history-day.json'
STEPS_NAME = 'steps.json'
TREND_NAME = 'trend.json'

# Not one of this module's files: it is written by the daemon every cycle. Its
# AGE is how this file knows whether the daemon is running, without a pidfile
# and without asking rc - and that answer decides whether the cron sampler may
# touch the chip at all. See daemon_is_sampling().
STATUS_NAME = 'status.json'


def path(name):
    """Where one of this plugin's records lives, worked out now and not before."""
    return os.path.join(STATE_DIR, name)


VERSION = 1

# One sample a minute. Not the daemon's interval, which is ten seconds by
# default and is about how quickly a fan reacts; this is about what is worth
# keeping. Six samples a minute of a temperature that moves a degree a minute
# is five copies of the same number.
LIVE_INTERVAL = 60
LIVE_KEEP = 120

FINE_INTERVAL = 300
FINE_KEEP = 288          # 24 hours

HOUR_INTERVAL = 3600
HOUR_KEEP = 720          # 30 days

DAY_INTERVAL = 86400
DAY_KEEP = 365           # a year

RINGS = (
    ('fine', FINE_NAME, FINE_INTERVAL, FINE_KEEP),
    ('hour', HOUR_NAME, HOUR_INTERVAL, HOUR_KEEP),
    ('day', DAY_NAME, DAY_INTERVAL, DAY_KEEP),
)

# How many closed duty changes are kept. A hundred is far more than the
# estimator can use - it wants a median over pairs and stops learning much
# after a dozen - and it is the single largest thing in the record per entry,
# because each one carries every sensor's name twice. Measured: 724 bytes a
# step, so a hundred of them is 72 KB and two hundred was an eighth of the
# whole history spent on the thing it needs least of.
STEPS_KEEP = 100

# How often the page's summary document is rewritten. NOT every sample: it
# summarises windows of an hour and upwards, and recomputing "the last month"
# sixty times an hour changes nothing in it. The live reading a page shows
# beside it comes from status.json, which the daemon rewrites every cycle, so
# nothing on the page is stale for being five minutes behind here.
#
# It is written immediately, whatever the cadence says, when a duty change
# closes - because that is the moment the verdict can change, and a verdict
# that had moved and was not shown for five minutes is the one case where the
# cadence would cost something.
TREND_INTERVAL = 300

# A gap longer than this means the sampler was not running. The seconds are
# counted as unobserved rather than attributed to the sample that ends them:
# a machine that was off for an hour did not spend that hour at the
# temperature it booted to.
MAX_GAP = LIVE_INTERVAL * 3

# How often the leaf list is rediscovered. See the module docstring: discovery
# is cheap (1.7 ms) but it is not free, and a temperature sensor appearing is
# an event that happens when a drive is plugged in, not every minute.
LEAF_REFRESH = 3600

# The sysctl trees temperatures are looked for under. Kept in step with
# sensors.GROUPS, and checked against it at import rather than trusted - the
# two files are edited by different hands and a group added there and missed
# here would be a sensor that shows on the page and never enters the record.
PREFIXES = ('dev.cpu.', 'dev.pchtherm.', 'hw.acpi.thermal.', 'dev.nvme.')

# Leaves that are not temperatures and are read on the slow cadence with the
# discovery walk, because they are what turns a temperature into a judgement.
#
#   tjmax          the die temperature the CPU protects itself at. Without it,
#                  "the cores reached 81 C" is a number; with it, "81 C with
#                  19 C to spare" is an answer.
#   throttle_log   whether this CPU has EVER slowed itself down for heat since
#                  boot. On the reference appliance it reads 0 (V, 2026-09-22),
#                  which settles the question the fan measurement raises: the
#                  fan buys six degrees, and the machine has not once needed
#                  them.
SLOW_LEAVES = ('dev.cpu.0.coretemp.tjmax', 'dev.cpu.0.coretemp.throttle_log')

SYSCTL = '/sbin/sysctl'

# Series kinds. One flat namespace so a layout is one list of strings.
TEMP = 'T'
RPM = 'R'
DUTY = 'D'
BUSY = 'B'

# The ladder of temperatures "time spent above" is counted against. Imported
# from stats so there is one copy; the ladder is stored in every ring header as
# well, so a record written under one ladder is still readable if the ladder is
# ever changed.
try:
    import stats
except ImportError:                                     # pragma: no cover
    sys.path.insert(0, HERE)
    import stats

LADDER_C = stats.LADDER_C


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def ensure_dir():
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR, mode=0o750, exist_ok=True)
        os.chmod(STATE_DIR, 0o750)


def read_json(path, default=None):
    """One of this plugin's records, or the default, and ALWAYS a document.

    A file that is valid JSON but is not an object counts as a file that could
    not be read. Every caller in this module does .get() or .setdefault() on
    the answer, so a bare list or a bare null does not produce a wrong record -
    it produces an AttributeError, and an AttributeError raised out of
    note_duty() is raised inside the daemon's control loop. Measured here on
    2026-09-22: a steps.json containing `[]` raised "'list' object has no
    attribute 'setdefault'" out of note_duty(), a live file containing `null`
    raised "'NoneType' object has no attribute 'get'" out of sample(), and a
    status.json containing `[1,2,3]` raised it out of daemon_is_sampling().
    See _shielded() for why nothing in this file may raise into that loop, and
    this is the layer that stops most of it happening in the first place.
    """
    fallback = {} if default is None else default
    try:
        with open(path, 'r') as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return fallback
    return document if isinstance(document, dict) else fallback


def write_json(path, payload, mode=0o640):
    """Write a file in one step, so a reader never sees half of it.

    The same shape as os-linkhealth's state.write() and for the same reason:
    the page reads these files while the sampler writes them, and a rename over
    a complete file is the only way that is safe without a lock between two
    processes that have no other reason to know about each other.
    """
    ensure_dir()
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tmp-')
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(payload, stream, separators=(',', ':'))
        os.chmod(temporary, mode)
        os.rename(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _shielded(what, function, *args, **kwargs):
    """Run one of the two entry points the control loop calls, and never throw.

    THIS IS NOT DEFENSIVE STYLE, IT IS THE FAN. The daemon's control loop ends
    every path through a finally that hands the hardware back:

        except Exception as failure: ... return 1
        finally: gave_back = restore(hardware)      (D, fanctl.py:1865-1877)

    and handing the hardware back on the reference appliance means SmartFan IV
    with duty_raw 50 - 20%, 1332 rpm - against the 5844 rpm the fan was turning
    (V, fanctl-io status, 2026-09-22). So an exception raised out of this file,
    inside that loop, ends with the fan SLOWER THAN IT WAS. note_duty() is
    called between a duty write and the tachometer check that follows it, which
    is the worst second of the cycle to leave.

    The paths are real and were measured here on 2026-09-22, each one raising
    straight out of sample() or note_duty() with nothing between it and the
    loop:

        NotADirectoryError   the state directory could not be made
        IsADirectoryError    the target name was occupied at the rename
        AttributeError       a state file was valid JSON and not a document
        OSError              anything else a full or read-only /var gives

    read_json() now refuses a document-shaped answer it did not get, which
    removes the third of those at the source. This is the backstop for the rest
    and for the next one nobody thought of. The record is a nicety; the fan is
    not. An error is returned, carried in the answer, and sent to syslog, where
    an appliance keeps such things. It is never raised.
    """
    try:
        return function(*args, **kwargs)
    except Exception as failure:            # noqa: BLE001 - deliberate: see above
        detail = '%s: %s' % (type(failure).__name__, failure)
        if SELFTEST:
            # A failure the test asked for. It is still reported - a silent one would
            # make the check worthless - but into the test's own output, so the
            # appliance's log does not carry an error nobody should act on.
            sys.stderr.write('selftest: the record failed as intended (%s): %s\n'
                             % (getattr(function, '__name__', 'call'), detail))
            return {'recorded': False, 'error': detail}
        try:
            import syslog                   # imported here, so the fast path never pays
            syslog.openlog('fanctl', syslog.LOG_PID, syslog.LOG_DAEMON)
            syslog.syslog(syslog.LOG_ERR,
                          'the history record failed and was skipped (%s): %s'
                          % (what, detail))
        except Exception:                   # noqa: BLE001 - nor may saying so raise
            pass
        return {'recorded': False, 'why': 'the record could not be written',
                'where': what, 'error': detail}


# ---------------------------------------------------------------------------
# Reading, cheaply
# ---------------------------------------------------------------------------

def _run(args):
    """Whatever the command printed, whatever it exited with.

    The exit status is discarded on purpose and it is the same reason
    sensors._run() gives: sysctl(8) exits non-zero when ANY name it was given
    is unknown while still printing every one that exists, and a machine with
    no pchtherm or no nvme is the ordinary case. Throwing the output away on a
    non-zero exit would mean one absent sensor took every present one with it.
    """
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return done.stdout or ''
    except (OSError, subprocess.SubprocessError):
        return ''


def discover_leaves():
    """Every temperature sysctl on this machine, by name, running no handler.

    `sysctl -N` prints names and does not fetch values, so this walks all four
    trees for 1.7 ms (V) where fetching them costs up to 127 ms. It is the
    difference between a sampler that is free and one that is not, and it is
    the only reason the expensive prefixes never appear in the fast path.
    """
    names = []
    for line in _run([SYSCTL, '-N'] + list(PREFIXES)).splitlines():
        name = line.strip()
        if name.endswith('.temperature'):
            names.append(name)
    return sorted(names)


def read_values(leaves, extra=()):
    """The named leaves and kern.cp_times, in ONE sysctl call.

    One call, because a second process launch costs more than everything this
    one does: the seven leaves plus cp_times together measured 1.28 ms, and the
    bare cost of starting sysctl at all is 1.16 ms (V). kern.cp_times is always
    in the list, including when the list is otherwise empty - the load figure
    is wanted on every path, and asking for it alone still costs one launch.
    """
    wanted = list(leaves) + list(extra) + ['kern.cp_times']
    values = {}
    for line in _run([SYSCTL, '-e'] + wanted).splitlines():
        name, _, value = line.partition('=')
        name = name.strip()
        if name:
            values[name] = value.strip()
    return values


def busy_percent(previous, current):
    """CPU busy over the interval between two kern.cp_times readings.

    Returns (percent, counters) - percent is None when there is nothing to
    compare against yet, or when the counters went backwards, which happens
    across a reboot and means the interval spans a machine that was not
    running. A busy figure invented across a reboot would then be used to throw
    away, or to keep, a duty-change pair on the strength of a number that
    described nothing.

    The layout is the kernel's: hw.ncpu blocks of five counters - user, nice,
    system, interrupt, idle - and idle is the last of each five.
    """
    try:
        counters = [int(part) for part in (current or '').split()]
    except ValueError:
        return None, None
    if not counters or len(counters) % 5:
        return None, None
    if not previous or len(previous) != len(counters):
        return None, counters

    total = 0
    idle = 0
    for index, value in enumerate(counters):
        delta = value - previous[index]
        if delta < 0:
            return None, counters        # rebooted, or the counters wrapped
        total += delta
        if index % 5 == 4:
            idle += delta

    if total <= 0:
        return None, counters
    return int(round((total - idle) * 100.0 / total)), counters


def take_reading(cache):
    """One sample's worth of temperatures and load, and the refreshed cache.

    `cache` carries the leaf list and the previous cp_times counters between
    runs; it lives in the live ring's header so that a cron invocation, which
    has no memory of its own, is as cheap as a daemon that does.
    """
    now = time.time()
    leaves = cache.get('leaves') or []
    slow = {}

    if not leaves or now - (cache.get('leaves_at') or 0) >= LEAF_REFRESH:
        leaves = discover_leaves()
        cache['leaves'] = leaves
        cache['leaves_at'] = now
        extra = SLOW_LEAVES
    else:
        extra = ()

    raw = read_values(leaves, extra)

    temps = {}
    for name in leaves:
        value = stats_parse(raw.get(name))
        if value is not None:
            temps[name] = int(round(value * 10))         # deci-Celsius

    for name in extra:
        value = raw.get(name)
        if value is not None:
            slow[name] = value

    percent, counters = busy_percent(cache.get('cp_times'), raw.get('kern.cp_times'))
    if counters is not None:
        cache['cp_times'] = counters

    if slow:
        cache['slow'] = dict(cache.get('slow') or {}, **slow)

    return {'at': now, 'temps': temps, 'busy': percent}, cache


def stats_parse(raw):
    """A temperature in Celsius, through sensors.py's own parser where it is there.

    The parsing rules - the trailing C, the decikelvin spelling, the
    plausibility bounds that stop a loose sensor reading -273 - belong to
    sensors.py and are not copied here. This is the one place the two files
    touch, and it falls back to a reduced copy only when sensors.py cannot be
    imported at all, so that a sampler still works on a tree where the sensor
    layer is missing.
    """
    try:
        import sensors
        return sensors.parse(raw)
    except ImportError:
        pass

    if raw is None:
        return None
    text = str(raw).strip()
    if text.endswith('C'):
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if number > 200:
        number = (number / 10.0) - 273.15
    if number < -40.0 or number > 150.0:
        return None
    return round(number, 1)


def read_fans():
    """Every fan channel, read-only, or {} when the chip cannot be reached.

    Called ONLY from the cron path, and only when the daemon is not running -
    see sample(). The daemon passes its own reading in, because two processes
    reading this chip means two processes walking the same two I/O ports with
    nothing serialising them against the kernel, which is the race the whole
    plugin is arranged around.
    """
    try:
        import chip
    except ImportError:
        return {}

    try:
        view = chip.read_path()
    except Exception:                                   # noqa: BLE001 - a refusal is an answer
        return {}

    fans = {}
    for entry in view.get('channels') or []:
        name = entry.get('alias') or ('FAN%s' % entry.get('channel'))
        # 'driven' is left UNKNOWN here and is not inferred from the mode. A
        # channel in manual mode is not a channel this plugin drives: four of
        # the five on the reference appliance are in manual mode at a duty of
        # 50% that the vendor's firmware set and nothing has changed since
        # (V, fanctl-io status, 2026-09-22), and reading those as "driven"
        # would put four permanently silent channels into every slot of every
        # ring - the exact eight numbers in thirteen the record is arranged to
        # leave out. Only the caller that did the driving knows, so only the
        # daemon path fills this in; see earned().
        fans[name] = {'rpm': entry.get('rpm'), 'duty': entry.get('duty_pct'),
                      'driven': None}
    return fans


def daemon_is_sampling(now=None):
    """Whether the daemon wrote a status document recently enough to be alive.

    Deliberately not a pidfile check. What this needs to know is not "is a
    process there" but "is something else already reading this chip every few
    seconds", and a document with a timestamp in it answers that directly,
    including the case of a daemon that is running and wedged - which a pidfile
    would answer wrongly and in the dangerous direction.
    """
    now = now or time.time()
    status = read_json(path(STATUS_NAME))
    written = status.get('at') or 0
    interval = status.get('interval') or 10
    return bool(written) and (now - written) < max(60.0, interval * 3)


# ---------------------------------------------------------------------------
# Layouts and packing
# ---------------------------------------------------------------------------

def series_for(reading, fans, recorded):
    """The ordered list of series ids this sample can fill.

    `recorded` is the set of fan channels that have earned a place: see the
    module docstring. A channel is added the first time it reports a tachometer
    or carries a duty this plugin set, and once added it stays, so that a fan
    that stops does not vanish from the record at the moment it becomes
    interesting.
    """
    ids = [BUSY + '|cpu']
    for name in sorted(reading.get('temps') or {}):
        ids.append(TEMP + '|' + name)
    for name in sorted(recorded):
        # Both columns, always, for a channel that has earned its place. A
        # tachometer without the duty beside it cannot answer the one question
        # this record exists for, and a duty that is unknown this minute is a
        # null in a column rather than a column that comes and goes.
        ids.append(RPM + '|' + name)
        ids.append(DUTY + '|' + name)
    return ids


def earned(fans, already):
    """Which channels belong in the record, adding any that have just earned it.

    Two ways in, and nothing else: the channel has reported a turning fan at
    least once, or this plugin is driving it. Four of the five channels on the
    reference appliance qualify under neither - they read 0 rpm for ever and
    carry a duty of 50% that nothing has changed (V) - and leaving them out is
    eight of every thirteen numbers in a slot saved.

    THE SECOND RULE IS ONLY REACHABLE FROM THE DAEMON, and that is stated here
    rather than left to be discovered: 'driven' is set by the caller that did
    the driving. read_fans(), which is the cron path's reader, cannot know it
    and does not guess - so on a machine where the daemon is not running, a
    channel this plugin once drove that has no tachometer wired to it stays out
    of the record. Measured here on 2026-09-22: earned() on the dict shape
    read_fans() produces, for a channel at 0 rpm, returns nothing; the same
    channel with 'driven' set is admitted.

    Membership only ever grows within a run. A fan that stops is at its most
    interesting at the moment it stops, and a rule that dropped it from the
    record for reading 0 would delete the evidence.
    """
    keep = set(already or ())
    for name, entry in (fans or {}).items():
        rpm = entry.get('rpm')
        if (rpm is not None and rpm > 0) or entry.get('driven'):
            keep.add(name)
    return sorted(keep)


def layout_index(document, ids):
    """The index of this series list in the file's layout table, adding it if new.

    A layout table rather than one fixed column order, because a sensor can
    appear - a drive plugged in, a driver loaded - and the alternative to a new
    layout is throwing the record away or silently writing the new sensor into
    the old sensor's column. Most machines will have exactly one layout for
    ever; the cost of the arrangement is one integer per slot.
    """
    layouts = document.setdefault('layouts', [])
    for index, existing in enumerate(layouts):
        if existing == ids:
            return index
    layouts.append(list(ids))
    return len(layouts) - 1


def compact_layouts(document):
    """Drop the layouts no remaining slot refers to, and renumber the rest.

    layout_index() appends and nothing ever discarded, and that was the one
    place in this file where "the whole history is bounded" was a hope rather
    than an arrangement: every distinct SET of readable sensors that had ever
    been seen stayed in the table for ever, and sensors that come and go
    independently of each other make two to the power of their number of them.
    Measured here on 2026-09-22: two sensors answering on alternate samples
    produced four layouts in forty samples, 614 bytes of a 4,252-byte live
    file, none of them ever removed.

    Called at the moment a document is written, which is the moment the slots
    that keep a layout alive are exactly the slots about to be stored.

    A slot whose index does not point at a layout is DROPPED rather than
    renumbered. It is already unreadable - unpack_live() and
    unpack_aggregate() both skip it - and renumbering around it would leave it
    pointing at a different layout afterwards, which is one sensor's numbers
    stored under another sensor's name. That is the single failure the layout
    table exists to prevent, and it is not worth one salvaged slot.
    """
    layouts = document.get('layouts') or []
    slots = document.get('slots') or []
    good = [slot for slot in slots
            if len(slot) > 1 and isinstance(slot[1], int) and 0 <= slot[1] < len(layouts)]
    used = sorted({slot[1] for slot in good})
    if len(used) == len(layouts) and len(good) == len(slots):
        return document

    renumbered = {old: new for new, old in enumerate(used)}
    document['layouts'] = [list(layouts[old]) for old in used]
    for slot in good:
        slot[1] = renumbered[slot[1]]
    document['slots'] = good
    return document


def values_for(ids, reading, fans):
    """The sample's numbers in layout order. None where nothing was read."""
    out = []
    temps = reading.get('temps') or {}
    for series in ids:
        kind, _, name = series.partition('|')
        if kind == BUSY:
            out.append(reading.get('busy'))
        elif kind == TEMP:
            out.append(temps.get(name))
        elif kind == RPM:
            entry = (fans or {}).get(name) or {}
            out.append(entry.get('rpm'))
        elif kind == DUTY:
            entry = (fans or {}).get(name) or {}
            out.append(entry.get('duty'))
        else:
            out.append(None)
    return out


def unpack_live(document):
    """The live ring as dicts: [{'at': t, 'values': {series id: value}}, ...]."""
    layouts = document.get('layouts') or []
    out = []
    for slot in document.get('slots') or []:
        if len(slot) < 2:
            continue
        index = slot[1]
        if not isinstance(index, int) or index >= len(layouts):
            continue
        ids = layouts[index]
        values = {}
        for position, series in enumerate(ids):
            if position + 2 < len(slot):
                values[series] = slot[position + 2]
        out.append({'at': slot[0], 'values': values})
    return out


def _block_width(kind):
    """How many numbers one series takes in an aggregate slot.

    Four for everything - minimum, maximum, the weighted sum and THE SECONDS
    THAT SUM WAS TAKEN OVER - and the ladder of seconds-above on top of that
    for temperatures only. "How long was the fan above 60 rpm" is not a
    question anybody asks, and three zero columns for every rpm and duty series
    in every slot of every ring is a sixth of the whole record spent on
    nothing.

    The per-series seconds is the one field that looks redundant beside the
    bucket's own `observed` and is not. A sensor that answered for two minutes
    of a five-minute slot has a weighted sum covering two minutes, and dividing
    it by the slot's five would report a sensor that read 70 C as having
    averaged 28 C. The bucket's seconds says how long the SAMPLER ran; the
    series' seconds says how long THAT SENSOR answered, and only the second one
    can divide that sum.
    """
    return 4 + len(LADDER_C) if kind == TEMP else 4


def pack_aggregate(bucket):
    """One accumulator as the flat list a ring slot holds."""
    out = [bucket['at'], bucket['layout'], bucket['n'],
           int(bucket['observed']), int(bucket['unobserved'])]
    for series in bucket['ids']:
        kind = series[0]
        record = bucket['series'].get(series) or {}
        out.append(record.get('min'))
        out.append(record.get('max'))
        out.append(record.get('wsum') or 0)
        out.append(int(record.get('seconds') or 0))
        if kind == TEMP:
            rungs = record.get('above') or [0] * len(LADDER_C)
            out.extend(int(value) for value in rungs)
    return out


def unpack_aggregate(document, slot):
    """One ring slot as {'at', 'n', 'observed', 'unobserved', 'series': {...}}."""
    layouts = document.get('layouts') or []
    if len(slot) < 5:
        return None
    index = slot[1]
    if not isinstance(index, int) or index >= len(layouts):
        return None
    ids = layouts[index]

    series = {}
    position = 5
    for name in ids:
        kind = name[0]
        width = _block_width(kind)
        if position + width > len(slot):
            break
        record = {'min': slot[position], 'max': slot[position + 1],
                  'wsum': slot[position + 2] or 0,
                  'seconds': int(slot[position + 3] or 0)}
        if kind == TEMP:
            record['above'] = list(slot[position + 4:position + 4 + len(LADDER_C)])
        series[name] = record
        position += width

    return {'at': slot[0], 'n': slot[2], 'observed': slot[3], 'unobserved': slot[4],
            'series': series}


# ---------------------------------------------------------------------------
# Accumulating
# ---------------------------------------------------------------------------

def new_bucket(at, ids, layout):
    return {'at': at, 'ids': list(ids), 'layout': layout, 'n': 0,
            'observed': 0, 'unobserved': 0, 'series': {}}


def add_to_bucket(bucket, ids, values, seconds):
    """Fold one live sample into an open accumulator.

    Weighted by the seconds the sample stands for, not counted. Everything
    downstream - the mean, the time above a threshold, the merge from five
    minutes to an hour to a day - is exact under gaps only because what is
    stored is a sum of value x seconds and the seconds beside it. See
    stats.merge_series().
    """
    if seconds <= 0:
        return
    bucket['n'] += 1
    bucket['observed'] += seconds

    for position, series in enumerate(ids):
        if position >= len(values):
            break
        value = values[position]
        if value is None:
            # Counted nowhere. The series' own seconds does not advance, so a
            # sensor that went quiet dilutes nothing: its mean stays the mean
            # of the time it was answering, and the gap shows up as its seconds
            # falling behind the bucket's.
            continue
        record = _series_record(bucket, series)
        if record['min'] is None or value < record['min']:
            record['min'] = value
        if record['max'] is None or value > record['max']:
            record['max'] = value
        record['wsum'] += value * seconds
        record['seconds'] += seconds
        if series[0] == TEMP:
            for index, rung in enumerate(LADDER_C):
                if value >= rung * 10:                   # deci-Celsius
                    record['above'][index] += seconds


def _series_record(bucket, series):
    return bucket['series'].setdefault(
        series, {'min': None, 'max': None, 'wsum': 0, 'seconds': 0,
                 'above': [0] * len(LADDER_C) if series[0] == TEMP else None})


def fold_aggregate(bucket, child):
    """Fold a closed finer slot into a coarser accumulator.

    The coarse rings are built from the fine ones and never from the raw
    samples, so an hour costs twelve merges rather than sixty, and a day costs
    twenty-four rather than 1440 - and because the merge is exact (sums and
    seconds), the answer is the same either way.

    `unobserved` is NOT carried up. It is derived at the moment a bucket
    closes, as the period minus what was observed, which is exact whether the
    sampler missed a minute, missed an hour, or never ran during that period at
    all. Adding the children's unobserved to that would count the same silence
    twice, and a bucket with no children at all would report none.
    """
    bucket['n'] += child.get('n') or 0
    bucket['observed'] += child.get('observed') or 0
    for series, record in (child.get('series') or {}).items():
        into = _series_record(bucket, series)
        if record.get('min') is not None and (into['min'] is None or record['min'] < into['min']):
            into['min'] = record['min']
        if record.get('max') is not None and (into['max'] is None or record['max'] > into['max']):
            into['max'] = record['max']
        into['wsum'] += record.get('wsum') or 0
        into['seconds'] += record.get('seconds') or 0
        if into.get('above') is not None and record.get('above'):
            for index, value in enumerate(record['above']):
                if index < len(into['above']):
                    into['above'][index] += value


# ---------------------------------------------------------------------------
# Duty changes: opening, closing and judging a step
# ---------------------------------------------------------------------------

def note_duty(channel, from_duty, to_duty, reason='', now=None, source='daemon'):
    """Record that a duty was changed. Shielded: see _shielded()."""
    return _shielded('note_duty', _note_duty, channel, from_duty, to_duty,
                     reason, now, source)


def _note_duty(channel, from_duty, to_duty, reason='', now=None, source='daemon'):
    """Record that a duty was changed, at the moment it was changed.

    Called by the daemon immediately after a write it verified. It exists
    because the sampler alone cannot see a change that happened and reversed
    between two samples, and because the daemon knows the exact second while
    the sampler can only know the minute - and the whole statistic downstream
    is a before-and-after around that second.

    It writes a small file and does nothing else. It does not read the chip, it
    does not compute anything, and it must not become slow: it is called from
    inside the control loop, between a write and the tachometer check that
    follows it.
    """
    now = float(now or time.time())
    if from_duty is not None and to_duty is not None and int(from_duty) == int(to_duty):
        return
    document = read_json(path(STEPS_NAME), {'version': VERSION, 'open': [], 'closed': []})
    document.setdefault('open', []).append({
        'at': now, 'channel': channel,
        'from_duty': None if from_duty is None else int(from_duty),
        'to_duty': None if to_duty is None else int(to_duty),
        'reason': reason, 'source': source, 'closed': False,
    })
    # Bounded even in the pathological case of a controller that changes duty
    # every cycle and a settling window that never closes any of them.
    document['open'] = document['open'][-STEPS_KEEP:]
    write_json(path(STEPS_NAME), document)


def _window_mean(samples, start, end, series_prefix=None, keep=None):
    """Mean of each series over [start, end], time-unweighted.

    Unweighted here, and weighted in the rings, and the difference is
    deliberate: a comparison window is two or three samples a fixed minute
    apart, so a weight would be the same number three times, and an unweighted
    mean over a window with a gap in it is caught by the sample count that is
    returned beside it rather than papered over.

    `keep` is the guard that stops a window straddling the change it is
    supposed to sit on one side of. See close_steps(): a sample taken at the
    duty this window is not about is dropped, however its timestamp compares
    with the change's.
    """
    picked = [entry for entry in samples if start <= entry['at'] <= end]
    if keep is not None:
        picked = [entry for entry in picked if keep(entry)]
    if not picked:
        return {}, 0

    totals = {}
    counts = {}
    for entry in picked:
        for series, value in entry['values'].items():
            if value is None:
                continue
            if series_prefix and not series.startswith(series_prefix):
                continue
            totals[series] = totals.get(series, 0) + value
            counts[series] = counts.get(series, 0) + 1

    return ({series: totals[series] / float(counts[series]) for series in totals},
            len(picked))


def close_steps(document, live, now):
    """Judge every open step whose settling window has run out.

    Returns True when anything changed, so the caller can skip the write when
    nothing did - which is almost every minute.
    """
    samples = unpack_live(live)
    changed = False
    still_open = []

    for step in document.get('open') or []:
        at = step['at']
        if now < at + stats.SETTLE_SECONDS:
            still_open.append(step)
            continue

        changed = True
        channel = step['channel']

        # A window that straddles the change it sits beside is the one way this
        # statistic can be wrong while looking right, and it is not a
        # hypothetical: it was measured here on 2026-09-22, where a step the
        # sampler inferred a second after the daemon had announced the same one
        # took a "before" mean of 79.0 C across a change from 81.0 to 75.0 and
        # reported 0.5 C per ten points for a chassis that gives 0.75.
        #
        # So the windows are filtered by the duty the sample was taken at, not
        # only by when it was taken. A sample at the new duty is not part of the
        # before, whatever its timestamp says, and a sample still at the old one
        # is not part of the after. Where the duty column is not readable at all
        # the sample is kept - that is the ordinary case on a channel this
        # plugin only watches, and dropping it would leave nothing to compare.
        duty_series = DUTY + '|' + channel

        def at_duty(wanted):
            def keep(entry):
                value = entry['values'].get(duty_series)
                return value is None or wanted is None or int(value) == int(wanted)
            return keep

        before, before_n = _window_mean(samples, at - stats.COMPARE_SECONDS, at,
                                        keep=at_duty(step.get('from_duty')))
        after, after_n = _window_mean(
            samples, at + stats.SETTLE_SECONDS - stats.COMPARE_SECONDS,
            at + stats.SETTLE_SECONDS, keep=at_duty(step.get('to_duty')))

        # Was it still moving when the window closed? Compare the two halves of
        # the settling window's tail. Where there are not enough samples to
        # split, the answer is None - unknown - and it stays unknown all the
        # way to the page rather than defaulting to "settled", which is the
        # reading that would make the figure look better than it is.
        half = at + stats.SETTLE_SECONDS - stats.COMPARE_SECONDS / 2.0
        early, early_n = _window_mean(samples, at + stats.SETTLE_SECONDS
                                      - stats.COMPARE_SECONDS, half, TEMP,
                                      keep=at_duty(step.get('to_duty')))
        late, late_n = _window_mean(samples, half, at + stats.SETTLE_SECONDS, TEMP,
                                    keep=at_duty(step.get('to_duty')))
        moving = None
        if early_n and late_n:
            moving = False
            for series in early:
                if series in late and abs(late[series] - early[series]) > stats.STILL_MOVING_DECIC:
                    moving = True
                    break

        def temps(values):
            return {series.partition('|')[2]: int(round(value))
                    for series, value in values.items() if series.startswith(TEMP + '|')}

        def one(values, kind, name):
            value = values.get(kind + '|' + name)
            return None if value is None else int(round(value))

        step['closed'] = True
        step['samples'] = [before_n, after_n]
        step['still_moving'] = moving
        step['before'] = {
            'temp': temps(before),
            'busy': one(before, BUSY, 'cpu'),
            'rpm': one(before, RPM, channel),
            'duty': one(before, DUTY, channel),
        }
        step['after'] = {
            'temp': temps(after),
            'busy': one(after, BUSY, 'cpu'),
            'rpm': one(after, RPM, channel),
            'duty': one(after, DUTY, channel),
        }
        ok, why = stats.usable(step)
        step['usable'] = ok
        step['why'] = why
        document.setdefault('closed', []).append(step)

    document['open'] = still_open
    document['closed'] = (document.get('closed') or [])[-STEPS_KEEP:]
    return changed


def detect_step(live, ids, values, now, steps):
    """A duty change the daemon did not announce, seen between two samples.

    The fallback, not the mechanism. It catches a fan changed by something that
    is not this plugin - a `fanctl.py set` from the command line, the chip's own
    SmartFan curve moving under a channel this plugin is only watching - which
    is exactly the case where nobody called note_duty() and exactly the case
    where the reader most wants to know what the change bought. The step is
    marked with the source it was found by, so a step that was inferred is
    never mistaken on the page for one that was commanded.

    AND IT MUST NOT REPORT A CHANGE THE DAEMON ALREADY DID. The daemon calls
    note_duty() at the moment of the write and this function sees the same
    change in the next sample's duty column up to a minute later, so without
    this check every commanded change is recorded twice. Measured here on
    2026-09-22: fourteen closed steps where seven changes had happened. Two
    separate harms, and the second is the worse one - the duplicates are not
    independent observations of anything, so they would narrow the reported
    spread while adding no information, which is precisely the shape of a
    statistic that looks more certain the more it repeats itself.

    The two are matched on THE CHANNEL AND THE DUTY THAT WAS ARRIVED AT, inside
    one sampling gap, and not on the timestamp alone. The timestamps do not
    line up and cannot be made to: note_duty() records the second of the write,
    this function can only say that the duty was one thing at the last sample
    and another at this one, and the write may have happened anywhere in
    between - or, as the first version of this check assumed wrongly, a second
    BEFORE the last sample, which is where five of the twelve duplicates in the
    measurement above came from. "The same channel reached the same duty within
    a sample of each other" is the same event by any reading.
    """
    samples = unpack_live(live)
    if not samples:
        return []
    last = samples[-1]['values']

    announced = set()
    for step in (steps.get('open') or []) + (steps.get('closed') or []):
        if now - (step.get('at') or 0) <= MAX_GAP and step.get('to_duty') is not None:
            announced.add((step.get('channel'), int(step['to_duty'])))

    found = []
    for position, series in enumerate(ids):
        if not series.startswith(DUTY + '|'):
            continue
        channel = series.partition('|')[2]
        new = values[position] if position < len(values) else None
        old = last.get(series)
        if new is None or old is None or int(new) == int(old):
            continue
        if (channel, int(new)) in announced:
            continue
        found.append({'channel': channel, 'from_duty': int(old), 'to_duty': int(new)})
    return found


# ---------------------------------------------------------------------------
# One sample
# ---------------------------------------------------------------------------

def sample(reading=None, fans=None, now=None, source='cron'):
    """Record one sample, if one is due. Shielded: see _shielded()."""
    return _shielded('sample', _sample, reading, fans, now, source)


def _sample(reading=None, fans=None, now=None, source='cron'):
    """Record one sample, if one is due. The whole of the sampling path.

    Called two ways and the difference matters:

      from the DAEMON, every cycle, with the reading and the fan view it has
      already taken. Nothing is read here at all; the call costs a comparison
      and, once a minute, a write. This is the normal path on a machine where
      fan control is on.

      from CRON, every minute, with nothing. It reads for itself - but only
      after checking that the daemon is not running, because the daemon reading
      the chip and a cron job reading the chip is two processes on the same two
      I/O ports. Where the daemon IS running, this returns immediately and the
      daemon's own call has already done the work.

    Returns a short dict saying what it did, which is what the CLI prints and
    what makes the cron line testable by hand.
    """
    now = float(now or time.time())
    ensure_dir()

    live = read_json(path(LIVE_NAME), {'version': VERSION, 'layouts': [], 'slots': [],
                            'cache': {}, 'open': {}, 'recorded': []})

    last_at = 0
    if live.get('slots'):
        last_at = live['slots'][-1][0]

    # A RECORD STAMPED IN THE FUTURE STOPS THE RECORD, and it is not a
    # hypothetical: the appliances this runs on are a decade old and their
    # CMOS batteries are the same age. One sample taken while the clock was
    # wrong is enough, because every later sample is then measured against it
    # and answers "not due". Measured here on 2026-09-22: one sample with the
    # clock ten years fast stopped the record for ten years - next_in
    # 315,359,700 seconds - and nothing in the file ever recovered; a clock
    # stepped back one hour by ntpd at boot, which is the ordinary case, cost
    # 65 minutes.
    #
    # The future-stamped slots go and the rest of the record stays. They are
    # real readings filed under a time the machine was never at, so they
    # cannot be kept in a series anybody means to average; and an open bucket
    # whose period is in the future would file this sample into it.
    if last_at > now + LIVE_INTERVAL:
        live['slots'] = [slot for slot in (live.get('slots') or [])
                         if slot and slot[0] <= now + LIVE_INTERVAL]
        live['open'] = {name: bucket for name, bucket in (live.get('open') or {}).items()
                        if (bucket or {}).get('at', 0) <= now}
        last_at = live['slots'][-1][0] if live['slots'] else 0
        dropped_future = True
    else:
        dropped_future = False

    if source == 'cron' and daemon_is_sampling(now):
        return {'recorded': False, 'why': 'the daemon is sampling'}

    if now - last_at < LIVE_INTERVAL:
        return {'recorded': False, 'why': 'not due', 'next_in':
                round(LIVE_INTERVAL - (now - last_at), 1)}

    cache = live.get('cache') or {}
    if reading is None:
        reading, cache = take_reading(cache)
    elif 'temps' not in reading:
        # A sensors.read() document from the daemon. It carries every
        # temperature and no load figure, because nothing else in this plugin
        # wants one - so cp_times is fetched here, in the one sysctl call this
        # path makes. The cost is 1.2 ms, and it falls to nothing the day
        # sensors.read() is asked to carry the counter itself.
        reading = from_sensors_document(reading, cache)
        percent, counters = busy_percent(cache.get('cp_times'),
                                         read_values([], ()).get('kern.cp_times'))
        if counters is not None:
            cache['cp_times'] = counters
        reading['busy'] = percent
    live['cache'] = cache

    if fans is None:
        # Only the cron path reads for itself, and only because it has already
        # established that the daemon is not running. A daemon that passed
        # nothing gets nothing: this function will not open the chip behind a
        # caller that holds it.
        fans = read_fans() if source == 'cron' else {}

    recorded = earned(fans, live.get('recorded'))
    live['recorded'] = recorded

    ids = series_for(reading, fans, recorded)
    index = layout_index(live, ids)
    values = values_for(ids, reading, fans)

    steps = read_json(path(STEPS_NAME), {'version': VERSION, 'open': [], 'closed': []})
    touched = False

    # A duty this plugin did not announce, noticed between two samples.
    for change in detect_step(live, ids, values, now, steps):
        steps.setdefault('open', []).append({
            'at': now, 'channel': change['channel'],
            'from_duty': change['from_duty'], 'to_duty': change['to_duty'],
            'reason': 'observed', 'source': 'observed', 'closed': False})
        steps['open'] = steps['open'][-STEPS_KEEP:]
        touched = True

    # What this sample stands for. Clamped, because a gap longer than a few
    # intervals is the sampler not having run and those seconds belong to
    # nobody: counting them here would put the machine at this temperature for
    # an hour it may have spent switched off. The seconds that were not watched
    # are not thrown away either - they come back out at the moment a bucket
    # closes, as the period minus what it observed.
    span = LIVE_INTERVAL if not last_at else min(MAX_GAP, max(1, int(now - last_at)))
    missed = max(0, int(now - last_at) - span) if last_at else 0

    live.setdefault('slots', []).append([int(now), index] + values)
    live['slots'] = live['slots'][-LIVE_KEEP:]
    live['ladder'] = list(LADDER_C)
    live['version'] = VERSION

    rolled = roll(live, ids, index, values, span, now)

    # After the slot list has been trimmed and before it is stored, because
    # that is when a layout stops being referred to. See compact_layouts().
    compact_layouts(live)
    write_json(path(LIVE_NAME), live)

    closed_a_step = close_steps(steps, live, now)
    if closed_a_step:
        touched = True
    if touched:
        write_json(path(STEPS_NAME), steps)

    # See TREND_INTERVAL. A closed step overrides the cadence, and so does a
    # trend document that is not there at all - a page asking on the first
    # minute should get an answer, even if the only honest answer in it is that
    # nothing has been measured yet.
    where = path(TREND_NAME)
    due = now - ((read_json(where) or {}).get('at') or 0) >= TREND_INTERVAL
    wrote_trend = False
    if closed_a_step or due or not os.path.exists(where):
        write_trend(live, steps, now)
        wrote_trend = True

    answer = {'recorded': True, 'at': int(now), 'series': len(ids),
              'seconds': span, 'unobserved': missed, 'closed': rolled,
              'trend_written': wrote_trend, 'source': source}
    if dropped_future:
        answer['dropped_future_slots'] = True
    return answer


def from_sensors_document(reading, cache):
    """A sensors.read() document turned into this file's compact shape.

    The daemon has already taken that reading for its own decision and passing
    it here is what makes the daemon's sampling cost nothing: no second sysctl
    call, no second process, no second pass over anything.
    """
    temps = {}
    for entry in reading.get('sensors') or []:
        celsius = entry.get('celsius')
        if celsius is not None:
            temps[entry['name']] = int(round(celsius * 10))
    return {'at': reading.get('at') or time.time(), 'temps': temps, 'busy': None}


def _close_bucket(bucket, file_name, interval, keep):
    """Write one finished accumulator into its ring, and hand it back unpacked.

    `unobserved` is worked out here and only here: the period, minus what was
    observed in it. That is exact under every kind of gap - a missed minute, a
    missed hour, a bucket that was created and never sampled into - and it is
    the reason nothing upstream accumulates the figure. See fold_aggregate().
    """
    ring = read_json(path(file_name), {'version': VERSION, 'layouts': [], 'slots': []})

    # EVERY SERIES THE BUCKET GATHERED, and not only the ones that were still
    # answering at the moment it closed. roll() replaces bucket['ids'] with the
    # current sample's when a sensor appears or goes, and pack_aggregate() packs
    # against that list, so a sensor that answered early and then went quiet was
    # written out of the slot altogether. Measured here on 2026-09-22: a sensor
    # present for the first two minutes of a five-minute slot and absent at the
    # close left no column and no seconds in the slot at all - 120 seconds of
    # real measurement discarded, and nothing downstream able to tell that from
    # a sensor that was never there. A record that drops what it measured is
    # worse than one that never measured it.
    bucket['ids'] = list(bucket['ids']) + [name for name in sorted(bucket['series'])
                                           if name not in bucket['ids']]

    # The layout table is per file, so the bucket's index is remapped rather
    # than carried across - two files that happened to discover sensors in a
    # different order would otherwise write one file's columns under the
    # other file's names.
    bucket['layout'] = layout_index(ring, bucket['ids'])
    bucket['unobserved'] = max(0, interval - int(bucket['observed']))
    ring.setdefault('slots', []).append(pack_aggregate(bucket))
    ring['slots'] = ring['slots'][-keep:]
    ring['ladder'] = list(LADDER_C)
    ring['interval'] = interval
    ring['version'] = VERSION
    compact_layouts(ring)
    write_json(path(file_name), ring)
    return unpack_aggregate(ring, ring['slots'][-1])


def roll(live, ids, index, values, span, now):
    """Fold this sample upward, closing any ring slot whose period has ended.

    The accumulators live in the live document - see the module docstring - and
    a coarse ring's file is opened only when one of its slots closes. On a
    machine that has been up a week that is 288 writes a day to the fine file,
    24 to the hourly one and one to the daily one, against 1440 to the live
    one.

    THE PERIOD EACH LEVEL IS WORKING ON COMES FROM WHAT IT IS ABSORBING, not
    from the clock. The five-minute level takes its period from `now`; the hour
    takes its period from the timestamp of the five-minute slot that just
    closed, and the day from the hour's. It matters at exactly one moment and
    that moment happens every night: the five-minute slot that closes at
    00:00:03 is the slot for 23:55, it belongs in the hour for 23:00 and in
    yesterday, and a loop that had asked the clock would have filed all three
    of them under the new day.

    Propagation stops as soon as a level does not close, because a level that
    is still open has nothing to give the one above it.
    """
    closed = []
    open_buckets = live.setdefault('open', {})
    carry = None                    # the slot that just closed, one level down
    child_at = now                  # the timestamp the next level is filing

    for position, (name, file_name, interval, keep) in enumerate(RINGS):
        bucket = open_buckets.get(name)
        period = int(child_at // interval) * interval

        produced = None
        if bucket is not None and bucket.get('at') != period:
            produced = _close_bucket(bucket, file_name, interval, keep)
            closed.append(name)
            bucket = None

        if bucket is None:
            bucket = new_bucket(period, ids, index)
            open_buckets[name] = bucket

        if bucket['ids'] != list(ids):
            # A sensor appeared or vanished mid-period. The columns change
            # under the open bucket; the part gathered before the change keeps
            # its own per-series sums, and a series that has gone simply stops
            # advancing its seconds. Nothing is mixed and nothing is silent.
            bucket['ids'] = list(ids)
            # Carried for completeness only: _close_bucket() recomputes it
            # against the ring's own layout table before anything is packed,
            # so a stale index here can never reach a file. compact_layouts()
            # renumbers the live table under these buckets for the same reason
            # - nothing reads this number except the line that overwrites it.
            bucket['layout'] = index

        if position == 0:
            add_to_bucket(bucket, ids, values, span)
        elif carry is not None:
            fold_aggregate(bucket, carry)

        if produced is None:
            break               # nothing closed here, so nothing to pass up
        carry = produced
        child_at = produced['at']

    return closed


# ---------------------------------------------------------------------------
# The document the page reads
# ---------------------------------------------------------------------------

def window_records(live, name, seconds, now):
    """The aggregate records covering one window, per series.

    Which ring answers which window is decided here and stated in the answer,
    because "the last hour" taken from two-minute samples and "the last month"
    taken from daily ones are different kinds of number and a page that showed
    them in one row without saying so would be inviting a comparison that is
    not there.
    """
    if seconds <= 2 * 3600:
        # From the raw live ring, aggregated on the spot. A hundred and twenty
        # slots is nothing to walk and it is the only ring with the resolution
        # an hour deserves.
        bucket = new_bucket(0, [], 0)
        samples = unpack_live(live)
        previous = None
        for entry in samples:
            # A stamp in the future is not in any window. It is what a machine
            # whose clock was wrong for a while left behind, and counting it
            # would put readings the appliance has not taken yet inside every
            # window at once. See sample(), which drops them at the source.
            if entry['at'] > now:
                continue
            if entry['at'] < now - seconds:
                previous = entry
                continue
            span = LIVE_INTERVAL if previous is None else min(
                MAX_GAP, max(1, int(entry['at'] - previous['at'])))
            ids = sorted(entry['values'])
            add_to_bucket(bucket, ids, [entry['values'][series] for series in ids], span)
            previous = entry
        return dict(bucket['series']), 'live'

    # The finest ring that actually HOLDS the window, which is its interval
    # times the number of slots it keeps - the two numbers already in RINGS.
    # This read `interval * 400` and discarded `keep`, and 400 is a number
    # nothing in this plugin measures: it made the fine ring claim 33 hours of
    # the 24 it holds and the hourly ring claim 16.7 days of the 30 it holds,
    # so the month window - 30 days, exactly what the hourly ring keeps - fell
    # through to the DAILY ring. Measured here on 2026-09-22 over two days and
    # five hours of samples: the month window was answered from the daily ring
    # with 121,500 seconds of coverage where the hourly ring held 189,900 of
    # the 190,800 seconds actually measured. The difference is the day that has
    # not closed yet: on any machine, the month view was missing everything
    # since midnight, which is the part of the month somebody looking at it
    # most likely came to see.
    for ring_name, file_name, interval, keep in RINGS:
        if seconds <= interval * keep:
            ring = read_json(path(file_name), {'layouts': [], 'slots': []})
            gathered = {}
            for slot in ring.get('slots') or []:
                if slot and now - seconds <= slot[0] <= now:
                    record = unpack_aggregate(ring, slot)
                    if not record:
                        continue
                    for series, part in record['series'].items():
                        gathered.setdefault(series, []).append(part)
            return ({series: stats.merge_series(parts)
                     for series, parts in gathered.items()}, ring_name)

    return {}, 'none'


def write_trend(live, steps, now=None):
    """Everything a page shows that is not the live reading, in one small file.

    Written every sample and read by the page, so the page never walks a ring.
    Its size does not grow: it is one record per series per window plus one
    verdict, which on the reference appliance is ten series and four windows.

    The rings stay on disk for the graph, which asks for one of them by name
    and gets it raw.
    """
    now = float(now or time.time())

    per_window = {}
    sources = {}
    for name, seconds in stats.WINDOWS:
        records, source = window_records(live, name, seconds, now)
        sources[name] = source
        for series, record in records.items():
            per_window.setdefault(series, {})[name] = record

    series_out = {}
    for series, windows in per_window.items():
        kind, _, label = series.partition('|')
        scale = 10.0 if kind == TEMP else 1.0
        entry = {'kind': kind, 'name': label,
                 'windows': {window: stats.present(record, scale)
                             for window, record in windows.items()}}
        for window, seconds in stats.WINDOWS:
            if window in entry['windows']:
                entry['windows'][window]['coverage'] = round(
                    stats.coverage(windows.get(window) or {}, seconds), 3)
        series_out[series] = entry

    # The current reading, straight off the newest live slot, so the page has
    # one place to read "now" and "over the last day" from and they cannot
    # disagree about which sample they mean.
    current = {}
    samples = unpack_live(live)
    if samples:
        for series, value in samples[-1]['values'].items():
            if value is None:
                continue
            current[series] = value / 10.0 if series.startswith(TEMP + '|') else value

    temperature_day = {
        series.partition('|')[2]: entry['windows'].get('day') or {}
        for series, entry in series_out.items() if entry['kind'] == TEMP
    }
    hot_name, hot_max = stats.hottest(temperature_day)

    cache = live.get('cache') or {}
    slow = cache.get('slow') or {}
    tjmax = stats_parse(slow.get('dev.cpu.0.coretemp.tjmax'))

    # NOT `or 0`. A machine with no coretemp - an AMD one, an older Intel one,
    # anything that is not the chip this was written on - has no
    # throttle_log to read, and `or 0` turned that absence into the number
    # zero, which is not "unknown": it is the sentence "this CPU has never
    # once slowed itself down for heat", printed on the page beside the fan
    # verdict and passed to stats.verdict() as evidence. Measured here on
    # 2026-09-22: with no sensors present at all the trend document still
    # carried "throttle_log": 0. tjmax two lines up gets this right by going
    # through stats_parse(), which answers None for what is not there; this
    # line now does the same.
    throttled = None
    raw_throttle = slow.get('dev.cpu.0.coretemp.throttle_log')
    if raw_throttle is not None:
        try:
            throttled = int(str(raw_throttle).strip())
        except ValueError:
            throttled = None

    headroom = None
    if tjmax is not None and hot_max is not None:
        headroom = round(tjmax - hot_max, 1)

    answer = stats.response(steps.get('closed') or [],
                            sensors=[series.partition('|')[2]
                                     for series in series_out if series.startswith(TEMP + '|')])
    call = stats.verdict(answer, hot_name, headroom_c=headroom, throttled=throttled)

    write_json(path(TREND_NAME), {
        'version': VERSION,
        'at': now,
        'current': current,
        'series': series_out,
        'windows': [{'name': name, 'seconds': seconds, 'from': sources.get(name)}
                    for name, seconds in stats.WINDOWS],
        'ladder_c': list(LADDER_C),
        'hottest': {'sensor': hot_name, 'max_c': hot_max,
                    'tjmax_c': tjmax, 'headroom_c': headroom,
                    'throttle_log': throttled},
        'response': answer,
        'verdict': call,
        'steps': {'open': len(steps.get('open') or []),
                  'closed': len(steps.get('closed') or [])},
        'rings': {name: {'interval': interval, 'keep': keep}
                  for name, _, interval, keep in RINGS},
    })


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def show(which='trend'):
    known = {'trend': TREND_NAME, 'live': LIVE_NAME, 'fine': FINE_NAME,
             'hour': HOUR_NAME, 'day': DAY_NAME, 'steps': STEPS_NAME}
    if which not in known:
        return {'error': 'no such record', 'known': sorted(known)}
    where = path(known[which])
    return read_json(where, {'error': 'nothing recorded yet', 'path': where})


def selftest(sizes=False):
    """Exercise the whole path with no hardware and no clock of its own.

    The point of the exercise is not that the arithmetic runs. It is that the
    duty-versus-temperature estimator can be shown to REFUSE: the third case
    below is a machine whose load moved at the same moment as its fan, which is
    the failure this statistic exists to avoid, and a version of this file that
    reported a slope for it would look exactly like a working one until
    somebody acted on the number.
    """
    import shutil

    scratch = tempfile.mkdtemp(prefix='fanctl-history-selftest-')
    was = STATE_DIR
    # ONE name is moved, and every file follows it, because every file is
    # worked out from it at the moment it is opened. See path().
    globals()['STATE_DIR'] = scratch

    report = {'checks': [], 'ok': True}

    def check(name, got, want):
        passed = got == want
        report['checks'].append({'check': name, 'got': got, 'want': want, 'ok': passed})
        if not passed:
            report['ok'] = False

    try:
        names = ['dev.cpu.%d.temperature' % n for n in range(4)]
        names += ['dev.pchtherm.0.temperature',
                  'hw.acpi.thermal.tz0.temperature', 'hw.acpi.thermal.tz1.temperature']

        # A day of minutes. The fan sits at 20% for the first half and 100%
        # for the second, the CPU is six degrees cooler at 100%, the chipset
        # is two degrees WARMER, and the load never moves. That is the
        # reference appliance's own measurement, written as data.
        start = 1790000000
        for minute in range(0, 1440):
            at = start + minute * 60
            duty = 20 if minute < 720 else 100
            rpm = 1332 if duty == 20 else 5844
            cpu = 810 if duty == 20 else 750
            pch = 540 if duty == 20 else 560
            reading = {'at': at, 'busy': 37,
                       'temps': dict([(names[i], cpu - i * 5) for i in range(4)],
                                     **{names[4]: pch, names[5]: 279, names[6]: 299})}
            fans = {'SYSFAN': {'rpm': rpm, 'duty': duty, 'driven': True}}
            if minute == 720:
                note_duty('SYSFAN', 20, 100, reason='selftest', now=at - 1)
            sample(reading=reading, fans=fans, now=at, source='daemon')

        trend = read_json(path(TREND_NAME))
        steps = read_json(path(STEPS_NAME))
        check('one duty change was closed', len(steps.get('closed') or []), 1)

        closed = (steps.get('closed') or [{}])[0]
        check('the closed step is usable', closed.get('usable'), True)

        answer = stats.response(steps.get('closed') or [])
        cpu0 = (answer['sensors'] or {}).get('dev.cpu.0.temperature') or {}
        # 81.0 C to 75.0 C over 80 points of duty = 0.75 C per 10 points.
        check('the CPU slope is 0.75 C per 10 points', cpu0.get('degrees_per_10pct'), 0.75)
        pch0 = (answer['sensors'] or {}).get('dev.pchtherm.0.temperature') or {}
        # 54.0 C to 56.0 C: the chipset got HOTTER, and the sign says so.
        check('the chipset slope is negative', (pch0.get('degrees_per_10pct') or 0) < 0, True)
        check('one pair is not enough to state a figure', cpu0.get('usable'), False)
        check('and it says why', cpu0.get('why'), 'too_few_pairs')

        # Three more changes, all with the load HELD, to get past MIN_PAIRS.
        at = start + 1440 * 60
        for round_number in range(3):
            for phase, duty in ((0, 20), (1, 100)):
                rpm = 1332 if duty == 20 else 5844
                cpu = 810 if duty == 20 else 750
                note_duty('SYSFAN', 100 if phase == 0 else 20, duty,
                          reason='selftest', now=at - 1)
                for minute in range(12):
                    at += 60
                    reading = {'at': at, 'busy': 37,
                               'temps': dict([(names[i], cpu - i * 5) for i in range(4)],
                                             **{names[4]: 540 if duty == 20 else 560,
                                                names[5]: 279, names[6]: 299})}
                    sample(reading=reading,
                           fans={'SYSFAN': {'rpm': rpm, 'duty': duty, 'driven': True}},
                           now=at, source='daemon')

        steps = read_json(path(STEPS_NAME))
        # Seven changes happened: one in the long day and six in the rounds.
        # Seven is the number that must come out. Fourteen is what came out
        # before the sampler was stopped from re-reporting what the daemon had
        # already announced, and every one of the seven extras was a pair whose
        # before-window straddled the change it was measuring.
        check('each duty change is recorded exactly once',
              len(steps.get('closed') or []), 7)
        check('and none of them is left open', len(steps.get('open') or []), 0)

        answer = stats.response(steps.get('closed') or [])
        cpu0 = (answer['sensors'] or {}).get('dev.cpu.0.temperature') or {}
        check('every pair agrees, so the spread is nil',
              [cpu0.get('lowest'), cpu0.get('highest')], [0.75, 0.75])
        check('now there are enough pairs', cpu0.get('usable'), True)
        check('and the span is the full 80 points', cpu0.get('span_pct'), 80)
        call = stats.verdict(answer, 'dev.cpu.0.temperature', headroom_c=19.0, throttled=0)
        check('the verdict is that the fan buys little', call.get('code'), 'trend.poor')
        # span_c and not whole_range_c, which this check used to assert. That field
        # was degrees_per_10pct * 10 - what the fan would buy across the whole range
        # if the relationship held all the way out, which nobody measured, and it
        # came out at 7.5 C here. It was removed on purpose. span_c is what the
        # WATCHED span bought: 0.75 C per ten points over the 80 points the check
        # above just asserted, which is 6.0. Written out because the difference
        # between 7.5 and 6.0 is the whole reason the field changed, and a bare
        # number here would send the next reader to derive it again.
        check('and it carries what the watched span bought',
              (call.get('params') or {}).get('span_c'), 6.0)

        # THE ONE THAT MUST REFUSE. Same fan change, but the machine went from
        # busy to idle at the same moment. The temperature fell by 20 C and the
        # fan had nothing to do with it.
        confounded = [{
            'at': 1, 'channel': 'SYSFAN', 'from_duty': 20, 'to_duty': 100, 'closed': True,
            'before': {'temp': {'dev.cpu.0.temperature': 810}, 'busy': 95, 'rpm': 1332},
            'after': {'temp': {'dev.cpu.0.temperature': 610}, 'busy': 4, 'rpm': 5844},
        } for _ in range(6)]
        refused = stats.response(confounded)
        check('a pair whose load moved is not counted', refused['counted'], 0)
        check('and the reason is recorded',
              (refused['rejected'] or {}).get('load_moved'), 6)

        # A change too small to mean anything.
        tiny = [{
            'at': 1, 'channel': 'SYSFAN', 'from_duty': 60, 'to_duty': 65, 'closed': True,
            'before': {'temp': {'dev.cpu.0.temperature': 700}, 'busy': 40, 'rpm': 3000},
            'after': {'temp': {'dev.cpu.0.temperature': 699}, 'busy': 40, 'rpm': 3200},
        } for _ in range(6)]
        check('a five-point change is not evidence',
              (stats.response(tiny)['rejected'] or {}).get('step_too_small'), 6)

        # Nothing changed at all: the commonest history there is.
        flat = [{
            'at': 1, 'channel': 'SYSFAN', 'from_duty': 60, 'to_duty': 60, 'closed': True,
            'before': {'temp': {'dev.cpu.0.temperature': 700}, 'busy': 40, 'rpm': 3000},
            'after': {'temp': {'dev.cpu.0.temperature': 700}, 'busy': 40, 'rpm': 3000},
        }]
        flat_call = stats.verdict(stats.response(flat), 'dev.cpu.0.temperature')
        check('a history where nothing moved claims nothing',
              flat_call.get('code'), 'trend.waiting')

        check('coverage is reported', 'coverage' in
              ((trend.get('series') or {}).get('T|dev.cpu.0.temperature') or
               {}).get('windows', {}).get('day', {}), True)

        # Nothing about temperature, and the most important checks in the file.
        # Each of these was a measured failure before it was a check.

        # THE RECORD MAY NOT THROW INTO THE CONTROL LOOP. See _shielded().
        was_dir = globals()['STATE_DIR']
        globals()['STATE_DIR'] = '/dev/null/state'       # cannot be made, on any Unix
        globals()['SELFTEST'] = True                     # so the failure goes to stderr
        try:
            answer = sample(reading={'at': 1, 'busy': 1, 'temps': {}}, fans={},
                            now=1, source='daemon')
            check('a state directory that cannot be made is an answer, not a throw',
                  answer.get('recorded'), False)
            answer = note_duty('SYSFAN', 20, 100, now=1)
            check('and note_duty answers too, because it is called mid-write',
                  isinstance(answer, dict) and answer.get('recorded'), False)
        finally:
            globals()['STATE_DIR'] = was_dir
            globals()['SELFTEST'] = False

        # The rest of the new checks write files, so they are given a state
        # directory of their own: the sizes measured under --sizes below must
        # be what the rings actually reach, not what a check left behind.
        main_live = read_json(path(LIVE_NAME))
        globals()['STATE_DIR'] = os.path.join(scratch, 'checks')
        try:
            # A state file that is valid JSON and is not a document.
            ensure_dir()
            with open(path(STEPS_NAME), 'w') as handle:
                handle.write('[]')
            check('a steps file that is not a document reads as absent',
                  read_json(path(STEPS_NAME), {'open': []}).get('open'), [])

            # A SENSOR THAT GOES QUIET KEEPS WHAT IT MEASURED. Two minutes of a
            # five-minute slot, then silence, then the close.
            globals()['STATE_DIR'] = os.path.join(scratch, 'vanish')
            at = 1790000100                              # on a five-minute boundary
            for minute in range(6):
                temps = {'dev.cpu.0.temperature': 700}
                if minute < 2:
                    temps['dev.nvme.0.temperature'] = 400
                sample(reading={'at': at, 'busy': 30, 'temps': temps},
                       fans={'SYSFAN': {'rpm': 3000, 'duty': 50}}, now=at,
                       source='daemon')
                at += 60
            fine = read_json(path(FINE_NAME))
            first = unpack_aggregate(fine, (fine.get('slots') or [[]])[0]) or {'series': {}}
            check('a sensor that went quiet keeps its seconds in the closed slot',
                  (first['series'].get('T|dev.nvme.0.temperature') or {}).get('seconds'),
                  120)

            # A CLOCK THAT WENT FORWARD AND CAME BACK does not stop the record.
            globals()['STATE_DIR'] = os.path.join(scratch, 'clock')
            at = 1790000100
            for minute in range(3):
                sample(reading={'at': at, 'busy': 30,
                                'temps': {'dev.cpu.0.temperature': 700}},
                       fans={'SYSFAN': {'rpm': 3000, 'duty': 50}}, now=at,
                       source='daemon')
                at += 60
            ahead = at + 10 * 365 * 86400                # a flat CMOS battery
            sample(reading={'at': ahead, 'busy': 30,
                            'temps': {'dev.cpu.0.temperature': 700}},
                   fans={'SYSFAN': {'rpm': 3000, 'duty': 50}}, now=ahead,
                   source='daemon')
            answer = sample(reading={'at': at, 'busy': 30,
                                     'temps': {'dev.cpu.0.temperature': 700}},
                            fans={'SYSFAN': {'rpm': 3000, 'duty': 50}}, now=at,
                            source='daemon')
            check('a sample stamped ten years ahead does not stop the record',
                  answer.get('recorded'), True)
            check('and the stamps that were in the future are gone',
                  answer.get('dropped_future_slots'), True)

            # An absent throttle_log is not a zero. Nothing in this selftest
            # ever read a sysctl, so the cache carries no coretemp leaves at
            # all - which is exactly the shape of a machine with no coretemp.
            globals()['STATE_DIR'] = os.path.join(scratch, 'throttle')
            write_trend(main_live, {'open': [], 'closed': []}, 1790000000)
            check('a CPU with no throttle_log is not reported as never throttled',
                  (read_json(path(TREND_NAME)).get('hottest') or {}).get('throttle_log'),
                  None)
        finally:
            globals()['STATE_DIR'] = was_dir

        # Every named window is answered by a ring that HOLDS it.
        holds = {}
        for window, window_seconds in stats.WINDOWS:
            if window_seconds <= 2 * 3600:
                holds[window] = True
                continue
            holds[window] = any(window_seconds <= interval * keep
                                for _, _, interval, keep in RINGS)
        check('every window is covered by a ring that holds it',
              sorted(name for name, ok in holds.items() if not ok), [])
        check('the month comes from the hourly ring, which holds exactly a month',
              next(name for name, _, interval, keep in RINGS
                   if 2592000 <= interval * keep), 'hour')

        # And the layout table does not grow without bound. Two sensors that
        # answer on alternate samples make four distinct layouts; none of them
        # may outlive the last slot that refers to it.
        globals()['STATE_DIR'] = os.path.join(scratch, 'layouts')
        at = 1790000100
        for minute in range(LIVE_KEEP + 40):
            temps = {'dev.cpu.0.temperature': 700}
            if minute % 2 == 0:
                temps['dev.nvme.0.temperature'] = 400
            if minute % 3 == 0:
                temps['hw.acpi.thermal.tz0.temperature'] = 300
            if minute >= 40:                             # both settle down for good
                temps = {'dev.cpu.0.temperature': 700}
            sample(reading={'at': at, 'busy': 30, 'temps': temps},
                   fans={'SYSFAN': {'rpm': 3000, 'duty': 50}}, now=at, source='daemon')
            at += 60
        settled = read_json(path(LIVE_NAME))
        check('a layout no slot refers to any more is gone',
              len(settled.get('layouts') or []), 1)
        check('and every slot still points at its own columns',
              sorted({series for entry in unpack_live(settled)
                      for series in entry['values']}),
              ['B|cpu', 'D|SYSFAN', 'R|SYSFAN', 'T|dev.cpu.0.temperature'])
        globals()['STATE_DIR'] = was_dir

        if sizes:
            # Fill every ring to its limit so the numbers are the steady state
            # and not the first hour. This is what DESIGN.md's arithmetic is
            # checked against.
            for file_name, keep in ((FINE_NAME, FINE_KEEP), (HOUR_NAME, HOUR_KEEP),
                                    (DAY_NAME, DAY_KEEP)):
                ring = read_json(path(file_name), {'version': VERSION, 'layouts': [], 'slots': []})
                slots = ring.get('slots') or []
                if not slots:
                    continue
                pattern = slots[-1]
                while len(slots) < keep:
                    slots.append(list(pattern))
                ring['slots'] = slots[-keep:]
                write_json(path(file_name), ring)

            live = read_json(path(LIVE_NAME))
            slots = live.get('slots') or []
            while len(slots) < LIVE_KEEP:
                slots.append(list(slots[-1]))
            live['slots'] = slots[-LIVE_KEEP:]
            write_json(path(LIVE_NAME), live)

            steps = read_json(path(STEPS_NAME))
            closed_steps = steps.get('closed') or []
            while closed_steps and len(closed_steps) < STEPS_KEEP:
                closed_steps.append(dict(closed_steps[-1]))
            steps['closed'] = closed_steps[-STEPS_KEEP:]
            write_json(path(STEPS_NAME), steps)

            report['sizes'] = {}
            total = 0
            for name, file_name in (('live', LIVE_NAME), ('fine', FINE_NAME),
                                    ('hour', HOUR_NAME), ('day', DAY_NAME),
                                    ('steps', STEPS_NAME), ('trend', TREND_NAME)):
                where = path(file_name)
                size = os.path.getsize(where) if os.path.exists(where) else 0
                report['sizes'][name] = size
                total += size
            report['sizes']['total'] = total
            report['sizes']['note'] = ('bytes on disk with every ring full, '
                                       'before filesystem compression')
            # A DUTY CHANGE IS NOT FREE, and this table said it was: steps.json
            # was weighted at zero writes a day, which is true only of a machine
            # whose fan never changes speed. note_duty() rewrites the whole
            # file at the moment of the change - 72,434 bytes of it once the
            # ring is full - close_steps() rewrites it again when that change
            # settles, and each close forces a trend write out of cadence as
            # well. So the rate is stated, it is an ASSUMPTION and not a
            # measurement, and the answer carries it where somebody reading the
            # figure can see what it was computed against. A curve that moves
            # the duty every cycle instead of once an hour would write steps.json
            # 17,280 times a day, which is 1.2 GiB, and the point of saying so
            # is that nobody should learn it from a worn flash device.
            changes_per_day = 24
            report['assumed_duty_changes_per_day'] = changes_per_day
            report['writes_per_day'] = {
                'live': 1440, 'fine': 288, 'hour': 24, 'day': 1,
                'trend': 288 + changes_per_day, 'steps': 2 * changes_per_day,
            }
            report['bytes_written_per_day'] = sum(
                report['writes_per_day'][name] * report['sizes'][name]
                for name in report['writes_per_day'])

        return report
    finally:
        globals()['STATE_DIR'] = was
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv):
    command = argv[1] if len(argv) > 1 else 'show'

    if command == 'sample':
        print(json.dumps(sample(source='cron'), indent=2, sort_keys=True))
        return 0

    if command == 'show':
        print(json.dumps(show(argv[2] if len(argv) > 2 else 'trend'),
                         indent=2, sort_keys=True))
        return 0

    if command == 'trend':
        print(json.dumps(read_json(path(TREND_NAME), {'error': 'nothing recorded yet'}),
                         indent=2, sort_keys=True))
        return 0

    if command == 'cost':
        # What one sample costs on THIS machine, not on the one this was
        # written for. Read-only: it takes real readings and throws them away.
        cache = {}
        rounds = int(argv[2]) if len(argv) > 2 else 20
        take_reading(dict(cache))                       # warm, uncounted
        spent = []
        for _ in range(rounds):
            started = time.perf_counter()
            _, cache = take_reading(cache)
            spent.append((time.perf_counter() - started) * 1000.0)
        spent.sort()
        print(json.dumps({
            'rounds': rounds,
            'min_ms': round(spent[0], 2),
            'median_ms': round(spent[len(spent) // 2], 2),
            'max_ms': round(spent[-1], 2),
            'leaves': len(cache.get('leaves') or []),
        }, indent=2, sort_keys=True))
        return 0

    if command == 'selftest':
        report = selftest(sizes='--sizes' in argv)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get('ok') else 1

    print(json.dumps({'error': 'unknown command', 'known':
                      ['sample', 'show', 'trend', 'cost', 'selftest']}, indent=2))
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
