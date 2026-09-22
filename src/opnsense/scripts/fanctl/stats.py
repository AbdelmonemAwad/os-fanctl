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

    Arithmetic over what history.py stored. No files, no hardware, no clock of
    its own - every function here takes what it needs as an argument, which is
    what lets the whole of it be exercised on a machine with no fan in it.

    Nothing in this file writes anything, opens anything or starts anything. It
    cannot change a duty and it cannot be on the path of one: its callers are
    history.sample(), which the daemon calls inside a try/except that logs and
    carries on, and the cron line, which is its own process. An exception
    raised here costs a minute of history and nothing else. That is a property
    worth keeping - if this file ever grows an import of something that touches
    the chip, that sentence stops being true.

    Two jobs, and the second one is the reason this file exists at all.

    THE EASY JOB is the summary: per sensor and per fan, over each window, the
    minimum, the maximum, the mean and the time spent above a threshold. It is
    arithmetic, and the only thing worth being careful about is that a window
    with gaps in it says so rather than quietly averaging over the samples it
    happens to have.

    THE HARD JOB is the one the owner of the reference appliance had to do by
    hand with a stopwatch: how much cooler does this chassis get for more fan?
    He measured 1332 rpm at 20% duty with the CPU at 81 C, then 5844 rpm at
    100% with the CPU at 75-80 C, and the chipset going UP two degrees - a 4.4x
    increase in fan speed for about six degrees (V, reported by the owner and
    consistent with this machine's own readings). The plugin has every number
    that measurement needed. It should not make anybody take it by hand again.

    AND THE NAIVE VERSION OF THAT STATISTIC IS WORSE THAN NOTHING.

    Regress temperature on duty across a whole history and the answer comes out
    saying that MORE FAN MAKES THE MACHINE HOTTER. It is not a bug in the
    arithmetic; it is the arithmetic answering a different question. A fan
    curve raises the duty BECAUSE the machine got hot, so in any history taken
    while a curve was running, high duty and high temperature arrive together
    and the correlation between them is positive. The cause runs backwards
    through the data.

    So nothing here correlates duty against temperature. Everything here is a
    PAIRED COMPARISON ACROSS ONE DUTY CHANGE: a window before it against a
    window after it. Five conditions decide whether a pair may count, and they
    are GATES - a pair that fails one is thrown away and the reason travels to
    the page with the count, so that a pair which was discarded can be seen to
    have been discarded:

      * THE DUTY MUST BE KNOWN ON BOTH SIDES. history.note_duty() is called
        with `applied=None` on the first write of a run - after every boot,
        every service start, every settings apply - and history.py records the
        step with no from-duty rather than inventing one (D, the hook in
        DESIGN's fanctl.py section: "history.py takes None and records the step
        without a from-duty rather than inventing one"). This file must not
        invent one either. The previous version read the duty as
        `(step.get('from_duty') or 0)`, which turned "unknown" into "zero per
        cent": a 50 -> 100 change was measured as a 100-point change and its
        slope came out at half the truth (V, measured 2026-09-22 on the
        reference appliance with the harness in /root/fanwork2/statsrev: two
        real 50 -> 100 pairs at 1.2 C per ten points, mixed with two
        first-write pairs, reported 0.9 and turned trend.good into trend.poor).

      * THE DUTY MUST REALLY HAVE MOVED, by at least MIN_STEP_PCT.

      * THE LOAD MUST NOT HAVE MOVED. Every sample carries the CPU busy
        percentage over the interval before it, and a pair whose before and
        after windows differ by more than MAX_BUSY_DRIFT_PCT is discarded -
        because a machine that got busier while the fan sped up will show the
        fan doing nothing, and one that went idle at the same moment will show
        the fan working miracles. This was not a theoretical worry on the
        reference machine: its CPU was at 75-81 C during the owner's test and
        at 66-68 C afterwards with the fan still at 100% (V,
        dev.cpu.0.temperature read 68.0C at 100% duty, 2026-09-22), which is a
        bigger swing than the entire effect being measured, and none of it was
        the fan.

      * EACH SIDE MUST BE MORE THAN ONE SAMPLE. See MIN_SAMPLES_PER_SIDE.

      * THERE MUST BE MORE THAN ONE PAIR. One pair is an anecdote. Below
        MIN_PAIRS this file reports how many it has and declines to state a
        figure, which is a different answer from zero and is kept different all
        the way to the page.

    SETTLING IS NOT ONE OF THE GATES and the previous version's docstring was
    wrong to count it among them. A reading that had not finished moving still
    gives the best figure there is; it is a floor under the real one, so it
    qualifies the answer instead of voiding it. What matters is that the three
    states stay three: settled, still moving, and NOT KNOWN - history.py sets
    `still_moving` to None when there were too few samples to split the tail
    and says in its own comment that unknown must stay unknown all the way to
    the page rather than defaulting to settled. The previous version was where
    it defaulted to settled: `if step.get('still_moving')` reads None as false
    (V, measured: three pairs with still_moving None produced qualifier None).
    Here the unknowns are counted separately and carry their own qualifier.

    WHAT COMES OUT is a median across pairs with the spread beside it, never a
    lone number: the spread is how a reader sees for themselves whether the
    chassis answers consistently. And the sign convention is fixed and stated
    once - POSITIVE MEANS MORE FAN IS COOLER - so that the chipset coming out
    negative reads as the finding it is rather than as a mistake.

    AND NO FIGURE MAY LEAVE THIS FILE DESCRIBING A DUTY NOBODY WATCHED. Two
    ways that happened before, both of them measured:

      * the answer carried `whole_range_c = degrees * 10`, which the page
        printed as "roughly N C across the whole range". Three pairs between
        40% and 60% - the narrowest span this file allows - produced 1.05 C per
        ten points and a whole-range claim of 10.5 C from 2.1 C of evidence (V,
        measured 2026-09-22). The fan cannot even reach most of that range:
        fanctl-io refuses below 30% and the shipped floor is 40% (D,
        hard_floor_pct in `fanctl-io identify`; DESIGN section 9). What is
        stated now is what the measured span bought, with the span beside it.

      * the median was compared against POOR_SLOPE_DEGREES with no reference to
        what the sensor can resolve. dev.cpu.0.coretemp.resolution is 1 on this
        machine (V, read 2026-09-22), so the smallest difference two readings
        can show is a whole degree, and across the smallest step this file
        accepts that IS 1.0 C per ten points - the exact value of
        POOR_SLOPE_DEGREES. Four pairs of a chassis that did nothing at all,
        each moving by one quantisation tick, came out as trend.good, 1.0 C per
        ten points, "roughly 10.0 C across the whole range" (V, measured
        2026-09-22). Now each pair carries the smallest slope its own step
        could have resolved, and a median that does not clear that floor is
        reported as trend.within_noise and not as an effect. The resolution
        itself is not read here - this file reads nothing - so the caller
        passes it, and history.py already keeps dev.cpu.0.coretemp.resolution
        in the same slow cache it takes tjmax and throttle_log from. Until it
        does pass it the floor is None, the answer says so, and the verdict is
        exactly what it was before.

    NOTHING IN THIS FILE RETURNS A SENTENCE. Verdicts are a code and the
    parameters the sentence needs, and the page builds the words through the
    translation catalogue like every other string in this plugin. A finished
    English sentence assembled in Python is a sentence no catalogue can reach,
    and half an Arabic page with English under every verdict is worse than
    either language alone. os-linkhealth learned that one the expensive way.
    The previous version broke its own rule in one place - `'sign': 'positive
    means more fan is cooler'` went into the stored document as English prose -
    and it is a code here.
"""

# Windows a person asks about, and the seconds in each. Named here once so that
# the page, the CLI and the stored document cannot drift apart.
WINDOWS = (
    ('hour', 3600),
    ('day', 86400),
    ('week', 604800),
    ('month', 2592000),
)

# The fixed ladder of temperatures that "time spent above" is counted against.
#
# Fixed, and deliberately not taken from the settings. A threshold that followed
# a setting would silently rewrite history every time somebody edited the
# settings page: yesterday's "four hours above the limit" would become "no time
# above the limit" because the limit moved, and no number that can do that
# belongs in a record. The ladder is absolute, it is stored beside the counts,
# and a question about a temperature between two rungs is answered with the
# bounds the rungs give rather than with a number that was never counted.
#
# It is also VERSIONED BY ITS LENGTH, which is the one thing the previous
# version did not respect: counts are positional, so a record written under a
# ladder of a different length must not be relabelled with today's rungs.
# merge_series() folds only the rungs every part agreed on and present() prints
# only what survived. A future release that adds a rung therefore shows the new
# rung from the day it was added and not before, which is the truth.
LADDER_C = (60, 70, 80)

# A duty change smaller than this is not evidence.
#
# R, and marked R deliberately, because the number is a judgement and the
# reason the previous version gave for it does not survive arithmetic. That
# reason was "below this the temperature change is smaller than the sensors'
# own resolution" - but the reference appliance gives about 0.75 C per ten
# points (V, from the owner's own 20 -> 100 measurement: 6 C over 80 points),
# so a ten-point step there moves the CPU by three quarters of one quantisation
# step of a sensor whose resolution is 1 C. Ten points does not clear the
# resolution on this machine; it is simply the smallest step worth recording.
# What actually protects the figure is the per-pair noise floor computed in
# response(), which uses the real step of each real pair instead of a constant.
MIN_STEP_PCT = 10

# How far the CPU busy percentage may differ between the two sides of a pair
# before that pair is thrown away.
#
# R. The previous comment called ten points "the loosest bound that still
# excludes the failure the reference machine showed", and that claim cannot be
# supported: the failure it refers to is a 15 C swing with no fan change, and
# nobody recorded what the CPU busy percentage did across it - the measurement
# was of temperature alone. Ten points is a judgement about what counts as the
# same load. It will be worth revisiting once this plugin's own history holds a
# month of paired busy and temperature, which is the first time anybody will be
# able to measure it rather than choose it.
MAX_BUSY_DRIFT_PCT = 10

# How long after a duty change a reading may be trusted.
#
# Two time constants sit on top of each other and only the slower one matters.
# The fan reaches its new speed in seconds - fanctl-io reports rpm_stable and
# the reference fan went 1332 to 5844 rpm well inside one sample (V). The
# CHASSIS does not: the owner's own measurement ran for two minutes and the CPU
# was still moving at the end of it, and the chipset - a passively cooled part
# with a large thermal mass - had moved two degrees in the wrong direction by
# then and may not have finished.
#
# So 240 seconds, which is four sampling intervals (D, history.LIVE_INTERVAL is
# 60), and the estimator does NOT simply assume that is enough. history.py
# compares the last two intervals of the settling window and marks the pair
# `still_moving`, or None where it could not tell; this file keeps those three
# states apart and says on the page that a moving figure is a LOWER BOUND on
# the effect rather than the effect. That is the honest shape for a number
# measured on a machine that could not be waited on for an hour: it is what the
# fan had bought so far.
SETTLE_SECONDS = 240

# The window averaged on each side of a duty change. Two sampling intervals, so
# a complete window holds two or three samples (D, history.LIVE_INTERVAL = 60,
# and history._window_mean's own comment: "two or three samples a fixed minute
# apart"). Long enough that a single bad reading is not the whole answer, short
# enough that the load has not had time to become a different load - and NOT
# long enough to average out a sensor that reports whole degrees, which the
# previous version claimed it was. It cannot be: when the true temperature
# holds steady at 74.4 C every sample in the window reads 74 C, and the mean of
# three identical readings is as quantised as one of them. That is what the
# noise floor in response() is for.
COMPARE_SECONDS = 120

# Still moving at the end of the settling window, in tenths of a degree.
#
# history.py marks a pair moving when the two halves of the tail differ by MORE
# than this, so on a sensor whose resolution is 1 C (V,
# dev.cpu.0.coretemp.resolution = 1, read 2026-09-22) a one-degree drift reads
# as settled and only two degrees or more marks it. That is coarse, and it is
# coarse in the safe direction for a threshold whose job is to catch a reading
# that has not arrived: it under-reports movement, so `still_moving` is a claim
# that something WAS moving and never a claim that nothing was.
STILL_MOVING_DECIC = 10

# Fewer pairs than this and no figure is stated at all.
MIN_PAIRS = 3

# How many samples a comparison window must hold before it is a mean rather
# than a reading.
#
# R, from the structure and not from a measurement: the window is
# COMPARE_SECONDS wide and the sampler runs every history.LIVE_INTERVAL
# seconds, so two or three samples is what a COMPLETE window holds and one
# sample means the window had a gap in it. The previous version never looked -
# history.py records the count in step['samples'] and nothing read it, so a
# pair built from one reading a side counted equally with a pair built from
# three (V, measured: usable() returned True for a step whose samples were
# [1, 1]). Where the count is missing altogether - an old record from before
# history.py wrote it - the pair is allowed through, because refusing a record
# for a field that did not exist when it was written would throw away real
# history to satisfy a rule about bookkeeping.
MIN_SAMPLES_PER_SIDE = 2

# The narrowest total duty span across all usable pairs that this file will
# state a per-ten-points figure from. Three pairs that all stepped 10 to 20 per
# cent describe what happens between 10 and 20 per cent and say nothing about
# what happens at 80, and a figure quoted without that span invites exactly
# that extrapolation.
MIN_SPAN_PCT = 20

# Below this, more fan is not the remedy. A chassis that gives back less than
# one degree for ten points of duty gives back about six degrees across the
# duty this fan can actually be asked for - 40% to 100%, because fanctl-io
# refuses below 30% and the shipped floor is 40% (D, hard_floor_pct in
# `fanctl-io identify`; DESIGN section 9) - and the fan noise between here and
# there is the entire cost. The reference appliance measures 0.75 and lands
# below this line, which is the answer the owner reached with a stopwatch.
POOR_SLOPE_DEGREES = 1.0


# ---------------------------------------------------------------------------
# Small arithmetic, written out rather than imported
# ---------------------------------------------------------------------------
#
# `statistics` costs real milliseconds to import on this appliance (V, measured
# 2026-09-22 on the reference machine: median over twelve runs, a bare
# interpreter start is 17.7 ms and `import statistics` makes it 31.3 ms, +13.6;
# `python3 -X importtime -c "import statistics"` attributes 14.1 ms of that to
# statistics and its `fractions` and `re` dependencies). This module is
# imported by a sampler that runs every minute for the life of the machine, so
# one small function is a cheaper answer than that import.
#
# `math` is NOT part of that argument and the previous version's comment was
# wrong to put it there, citing a measurement of `import fcntl,math` for a
# claim about `statistics`. Measured today on the same machine: `import math`
# costs 0.27 ms by -X importtime and is inside the run-to-run noise of a whole
# interpreter start. Nothing here needs math either way; the point stands for
# the module it was actually about.

def median(values):
    """The middle value, or the mean of the middle two. None for nothing."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _round(value, places=1):
    return None if value is None else round(value, places)


def _duty(step, key):
    """A duty off a step, or None - and None is never 0.

    The whole of the first gate is this function. `step.get(key) or 0` was the
    defect: it reads a missing duty, a null duty and nought per cent as the
    same thing, and only one of those three is a number.
    """
    value = (step or {}).get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolution(resolutions, sensor):
    """One sensor's resolution in tenths of a degree, or None if nobody said.

    `resolutions` is either one number for every sensor or a map from sensor
    name to number, WHICH IS NOT A REFINEMENT - the sensors on one machine do
    not agree. Measured on the reference appliance 2026-09-22 (V):

        dev.cpu.N.coretemp.resolution        1        - the sysctl says so
        dev.pchtherm.0.temperature           1 C      - no resolution sysctl
                                                        exists; every value the
                                                        driver reports is a
                                                        whole degree (47.0C,
                                                        t0temp 108.0C, ctt
                                                        115.0C)
        hw.acpi.thermal.tzN.temperature      0.1 C    - reads 27.9C and 29.9C,
                                                        so tenths

    Handing the CPU's whole degree to an ACPI zone would be ten times too
    coarse and would call a real half-degree effect noise, so a caller with a
    map should pass the map. A missing name falls back to the entry under None,
    and nothing at all means no floor rather than a guessed one.
    """
    if isinstance(resolutions, dict):
        value = resolutions.get(sensor)
        if value is None:
            value = resolutions.get(None)
        return value
    return resolutions


def _side(step, which):
    """One side of a step as a dict, whatever nonsense is in the record."""
    side = (step or {}).get(which)
    return side if isinstance(side, dict) else {}


def _temps(step, which):
    """The temperature map on one side, always a dict."""
    temps = _side(step, which).get('temp')
    return temps if isinstance(temps, dict) else {}


# ---------------------------------------------------------------------------
# Merging aggregates
# ---------------------------------------------------------------------------

def merge_series(parts):
    """Fold several aggregate records for one series into one.

    EXACT, and that is the whole reason the stored record holds a weighted SUM
    and the seconds it was taken over rather than a mean. Means do not merge:
    the average of twelve five-minute averages is only the hour's average when
    all twelve covered the same number of seconds, and the one case where they
    do not is the case that matters - the hour containing a reboot, where the
    plugin was not running for part of it. Sums and seconds merge by addition
    whatever the gaps, so a day built from hours built from five-minute slots
    carries the same number it would have had from the raw samples.

    `parts` are dicts as history.unpack() yields them:
        {'min': int|None, 'max': int|None, 'wsum': int, 'seconds': int,
         'above': [int, ...] or None}

    The ladder counts fold only as far as every part agrees. A part carrying
    two rungs and a part carrying three were counted against different ladders,
    and the exactness this function exists for is worth more than the third
    rung: it is kept as two, and present() prints two.
    """
    low = None
    high = None
    wsum = 0
    seconds = 0
    above = None

    for part in parts:
        if not isinstance(part, dict) or not part.get('seconds'):
            continue
        if part.get('min') is not None and (low is None or part['min'] < low):
            low = part['min']
        if part.get('max') is not None and (high is None or part['max'] > high):
            high = part['max']
        wsum += part.get('wsum') or 0
        seconds += part['seconds']
        rungs = part.get('above')
        if not rungs:
            continue
        if above is None:
            above = list(rungs)
            continue
        if len(rungs) < len(above):
            above = above[:len(rungs)]
        for index in range(len(above)):
            above[index] += rungs[index]

    return {'min': low, 'max': high, 'wsum': wsum, 'seconds': seconds, 'above': above}


def present(record, scale=10.0, ladder=LADDER_C):
    """One merged record as the numbers a page prints.

    `scale` is what the stored integer is divided by: temperatures are kept in
    tenths of a degree, and rpm and duty are kept as themselves.

    'mean' is TIME-WEIGHTED - wsum is the sum of value x seconds - so a window
    in which the sampler missed ten minutes reports the mean of the time it
    watched and not the mean of the samples it happens to hold. The two differ
    exactly when something went wrong, which is when a mean is read hardest.

    `ladder` is what the counts were counted against. It defaults to today's
    and it is a parameter so that a caller holding a record from an older
    ladder can say so instead of having the counts relabelled underneath it.
    """
    record = record if isinstance(record, dict) else {}
    seconds = record.get('seconds') or 0
    if not seconds:
        return {'min': None, 'max': None, 'mean': None, 'seconds': 0, 'above': {}}

    above = {}
    rungs = record.get('above')
    if rungs:
        for index, rung in enumerate(ladder):
            if index < len(rungs):
                above[rung] = int(rungs[index])

    return {
        'min': _round((record['min'] / scale) if record.get('min') is not None else None),
        'max': _round((record['max'] / scale) if record.get('max') is not None else None),
        'mean': _round((record.get('wsum') or 0) / scale / seconds),
        'seconds': int(seconds),
        'above': above,
    }


def summarise(series_records, scale=10.0):
    """{window name: presented record} for one series.

    `series_records` is {window name: [aggregate records]}.
    """
    out = {}
    for name, _ in WINDOWS:
        out[name] = present(merge_series(series_records.get(name) or []), scale)
    return out


def coverage(record, window_seconds):
    """How much of a window was actually watched, 0.0 to 1.0.

    Carried beside every summary and never folded into it. "38 C mean over the
    last day" from a plugin that was running for two hours of that day is a
    true statement about two hours and a misleading one about a day, and the
    only fix that does not throw the number away is to print how much of the
    window it covers.
    """
    if not window_seconds:
        return 0.0
    return min(1.0, ((record or {}).get('seconds') or 0) / float(window_seconds))


# ---------------------------------------------------------------------------
# The statistic that matters: what a duty change bought
# ---------------------------------------------------------------------------

def pair_slope(step, sensor, per_points=10):
    """Degrees per `per_points` points of duty, from one closed duty change.

    POSITIVE MEANS MORE FAN IS COOLER. The subtraction is written the way round
    that makes that true and is not to be tidied: before minus after, over
    after minus before. It holds for a change in either direction - a fan that
    slowed and a chassis that warmed is the same finding as a fan that sped up
    and a chassis that cooled (V, both directions checked on the reference
    appliance: 20 -> 100 with 81 -> 75 C and 100 -> 20 with 75 -> 81 C both
    return 0.75).

    Returns None when this step cannot speak about this sensor at all - the
    sensor was missing on one side, a duty was not known, or the duty did not
    move.
    """
    before = _temps(step, 'before').get(sensor)
    after = _temps(step, 'after').get(sensor)
    if before is None or after is None:
        return None

    from_duty = _duty(step, 'from_duty')
    to_duty = _duty(step, 'to_duty')
    if from_duty is None or to_duty is None:
        return None

    moved = to_duty - from_duty
    if not moved:
        return None

    # deci-Celsius in, degrees out.
    return ((before - after) / 10.0) / moved * per_points


def pair_floor(step, resolution_decic, per_points=10):
    """The smallest slope this pair could possibly have resolved, or None.

    A sensor that reports whole degrees cannot show a difference smaller than
    one degree, so across a step of N points the smallest slope that is not
    simply the reading crossing a quantisation boundary is
    resolution / N * per_points. Ten points of duty on a sensor of 1 C
    resolution gives 1.0 C per ten points - which is why the previous version,
    comparing against a flat POOR_SLOPE_DEGREES of 1.0, called a chassis that
    did nothing at all "trend.good, roughly 10 C across the whole range"
    (V, measured 2026-09-22 with four one-tick pairs).

    Conservative on purpose: it uses the full resolution and not the
    resolution divided by the sample count, because averaging does not help
    here. When the true temperature is steady at 74.4 C, every sample in the
    window reads 74 C and the mean of three of them is still 74.

    `resolution_decic` is THIS sensor's own resolution in tenths of a degree,
    which this file cannot read and must be given. None means nobody said, and
    then there is no floor and the answer says so rather than assuming one.
    """
    if not resolution_decic:
        return None
    from_duty = _duty(step, 'from_duty')
    to_duty = _duty(step, 'to_duty')
    if from_duty is None or to_duty is None or from_duty == to_duty:
        return None
    return (resolution_decic / 10.0) / abs(to_duty - from_duty) * per_points


def usable(step):
    """Whether one closed step may be counted, and if not, which rule said no.

    Returns (True, '') or (False, reason code). The code travels to the page
    with the step, because a reader who can see that eleven of fourteen changes
    were discarded for load drift has learned something real about their
    machine - that its temperature is set by what it is doing and not by its
    fan - which is the same lesson the slope teaches, arriving earlier.

    The order of the tests is the order of the reasons: a step whose from-duty
    is unknown must be reported as unknown and not as a small step, which is
    what happened when the duty was read as `or 0` (V, measured: from_duty
    None with to_duty 5 was rejected as 'step_too_small').
    """
    if not isinstance(step, dict):
        return False, 'unreadable'
    if not step.get('closed'):
        return False, 'open'

    from_duty = _duty(step, 'from_duty')
    to_duty = _duty(step, 'to_duty')
    if from_duty is None or to_duty is None:
        return False, 'duty_unknown'
    if abs(to_duty - from_duty) < MIN_STEP_PCT:
        return False, 'step_too_small'

    before_busy = _side(step, 'before').get('busy')
    after_busy = _side(step, 'after').get('busy')
    if before_busy is None or after_busy is None:
        return False, 'load_unknown'
    try:
        if abs(after_busy - before_busy) > MAX_BUSY_DRIFT_PCT:
            return False, 'load_moved'
    except TypeError:
        return False, 'load_unknown'

    if not _temps(step, 'before') or not _temps(step, 'after'):
        return False, 'no_temperature'

    # history.py records [before_n, after_n]. A record written before it did
    # carries nothing here and is let through; a record that carries the count
    # is held to it.
    samples = step.get('samples')
    if isinstance(samples, (list, tuple)) and len(samples) >= 2:
        try:
            if min(int(samples[0]), int(samples[1])) < MIN_SAMPLES_PER_SIDE:
                return False, 'too_few_samples'
        except (TypeError, ValueError):
            pass

    return True, ''


def response(steps, sensors=None, per_points=10, resolution_decic=None):
    """What this chassis gives back for more fan, from its own history.

    Returns, per sensor:

        {'degrees_per_10pct': median across pairs,
         'lowest', 'highest': the spread across pairs,
         'pairs': how many counted,
         'span_pct': the lowest duty any counted pair started from and the
                     highest it reached - the range the figure describes and
                     the range it may not be read outside,
         'span_c': what the measured span bought, which is the only
                   whole-range number this file will state,
         'still_moving': how many of those pairs had not finished settling,
         'settling_unknown': how many could not be told either way,
         'noise_floor': the smallest slope these pairs could have resolved,
                        or None when nobody supplied this sensor's resolution,
         'rpm_ratio': the median of (faster side / slower side), which is
                      always the higher duty over the lower one whichever
                      direction the change went,
         'usable': bool, 'why': code}

    and a `rejected` count per reason, so the page can say how many changes
    were looked at and why most of them did not count.

    `resolution_decic` is one number for every sensor, or a map from sensor
    name to number, or None for "nobody said". See _resolution(): the sensors
    on the reference appliance do not agree with each other, so the map is the
    right shape for a caller that has read the sysctls.
    """
    counted = []
    rejected = {}
    for step in steps or []:
        ok, why = usable(step)
        if ok:
            counted.append(step)
        else:
            rejected[why] = rejected.get(why, 0) + 1

    names = list(sensors or [])
    if not names:
        seen = []
        for step in counted:
            for name in _temps(step, 'before'):
                if name not in seen:
                    seen.append(name)
        names = seen

    per_sensor = {}
    for name in names:
        resolution = _resolution(resolution_decic, name)
        slopes = []
        floors = []
        moving = 0
        unknown = 0
        low_duty = None
        high_duty = None
        ratios = []

        for step in counted:
            slope = pair_slope(step, name, per_points)
            if slope is None:
                continue
            slopes.append(slope)

            floor = pair_floor(step, resolution, per_points)
            if floor is not None:
                floors.append(floor)

            if step.get('still_moving'):
                moving += 1
            elif step.get('still_moving') is None:
                # Not "settled". Nobody could tell, and that stays visible.
                unknown += 1

            from_duty = _duty(step, 'from_duty')
            to_duty = _duty(step, 'to_duty')
            for duty in (from_duty, to_duty):
                low_duty = duty if low_duty is None else min(low_duty, duty)
                high_duty = duty if high_duty is None else max(high_duty, duty)

            # The ratio is between the FASTER and the SLOWER side, decided by
            # which side carried the higher duty and not by which came first.
            # Taken as after-over-before it inverted on every downward change,
            # so a history of two steps down and one up reported 0.23 for a fan
            # that turns 4.4 times faster at full, under a sentence that reads
            # "the fan turns 0.23 times faster to do it" (V, measured
            # 2026-09-22).
            before_rpm = _side(step, 'before').get('rpm')
            after_rpm = _side(step, 'after').get('rpm')
            if before_rpm and after_rpm:
                if to_duty > from_duty:
                    ratios.append(after_rpm / float(before_rpm))
                else:
                    ratios.append(before_rpm / float(after_rpm))

        span = None if low_duty is None else (high_duty - low_duty)
        middle = median(slopes)
        entry = {
            'degrees_per_10pct': _round(middle, 2),
            'lowest': _round(min(slopes), 2) if slopes else None,
            'highest': _round(max(slopes), 2) if slopes else None,
            'pairs': len(slopes),
            'span_pct': span,
            'from_pct': low_duty,
            'to_pct': high_duty,
            # What the measured span bought, and nothing beyond it. For the
            # reference appliance's own 20 -> 100 pairs this is the 6 C the
            # owner measured with a stopwatch, not a figure for a 0 -> 100
            # range no fan on this machine can be asked for.
            'span_c': None if (middle is None or not span)
                      else _round(middle * span / float(per_points)),
            'still_moving': moving,
            'settling_unknown': unknown,
            'noise_floor': _round(median(floors), 3),
            'rpm_ratio': _round(median(ratios), 2),
        }

        # Three different kinds of "no answer", kept apart all the way out. Too
        # few pairs means come back later. Too narrow a span means the pairs
        # exist and describe a stretch of the range too short to quote a rate
        # from - the plugin HAS an answer about 20 to 30 per cent and must not
        # let it be read as an answer about the fan. Inside the noise means the
        # pairs exist, the span is wide enough, and the difference they show is
        # no bigger than the smallest difference the sensor can express.
        floor = entry['noise_floor']
        if entry['pairs'] < MIN_PAIRS:
            entry['usable'] = False
            entry['why'] = 'too_few_pairs'
        elif span is None or span < MIN_SPAN_PCT:
            entry['usable'] = False
            entry['why'] = 'span_too_narrow'
        elif floor is not None and middle is not None and abs(middle) <= floor:
            entry['usable'] = False
            entry['why'] = 'within_noise'
        else:
            entry['usable'] = True
            entry['why'] = ''

        per_sensor[name] = entry

    return {
        'sensors': per_sensor,
        'counted': len(counted),
        'looked_at': len(steps or []),
        'rejected': rejected,
        'settle_seconds': SETTLE_SECONDS,
        'compare_seconds': COMPARE_SECONDS,
        'resolution_decic': resolution_decic,
        # A code, not a sentence: the page says which way the sign runs, in
        # whichever language it is being read in.
        'sign': 'positive_is_cooler',
    }


# ---------------------------------------------------------------------------
# The verdict, as a code and its numbers
# ---------------------------------------------------------------------------

def verdict(answer, hottest_sensor, headroom_c=None, throttled=None):
    """The one thing this plugin should be able to say about this appliance.

    A code and the numbers its sentence needs - never the sentence. See the
    module docstring.

    The codes, and what each is for:

      trend.waiting      nothing has changed the duty enough to measure from.
                         The commonest state on a machine running a gentle
                         curve, and it is not a failure: it means the plugin
                         has not been given the experiment it needs. The page
                         should offer to run it (hold the fan at 100% for five
                         minutes, then let it go back to what it was doing)
                         rather than wait for it to happen by accident.
      trend.narrow       there are pairs, over too short a stretch of the duty
                         range to quote a rate from.
      trend.within_noise there are pairs over a wide enough stretch, and what
                         they show is no larger than one step of the sensor's
                         own resolution divided by the duty step. There is no
                         effect here big enough for these sensors to see, which
                         is a finding and not a failure - and it is a different
                         finding from "the fan buys little", because it does
                         not even establish the direction.
      trend.good         the fan buys real degrees. More of it is a remedy.
      trend.poor         the fan buys less than POOR_SLOPE_DEGREES per ten
                         points, and clears the noise floor while doing it.
                         THIS IS THE REFERENCE APPLIANCE'S ANSWER and it is the
                         useful one: a restricted duct and a fan with little
                         static pressure, where the noise is the whole cost and
                         the cooling is not the benefit.
      trend.inverse      this sensor got HOTTER with more fan. Not an error.
                         The chipset on the reference machine went up two
                         degrees when the fan went to full (V, reported by the
                         owner), which is what a chassis does when more air is
                         pulled across the hot parts and over that one.

    And two qualifiers, either of which attaches to whichever code was chosen:

      trend.lower_bound      the readings had not finished moving when the
                             window closed, so the figure is what the fan had
                             bought by then and not what it will buy.
      trend.settling_unknown there were too few samples to tell whether they
                             had finished moving. NOT the same as settled, and
                             the previous version reported it as settled
                             because None is falsy (V, measured 2026-09-22).

    `headroom_c` and `throttled` are the other half of the answer and the page
    should print them beside it. A chassis whose fan buys nothing is only a
    problem if the machine is running out of room, and on the reference
    appliance it is not: tjmax is 100 C (V, dev.cpu.0.coretemp.tjmax read
    100.0C on 2026-09-22), the cores sit in the sixties and seventies, and
    dev.cpu.0.coretemp.throttle_log has never been anything but 0 (V, read the
    same day). "The fan buys you six degrees and you have thirty-two to spare"
    is a complete answer. "The fan buys you six degrees" on its own invites
    somebody to buy a louder fan.

    A caution for the caller, because this function cannot check it: headroom
    is only meaningful when `hottest_sensor` is a core. tjmax belongs to the
    CPU package and subtracting it from a chipset or board-zone maximum is a
    number about nothing.
    """
    answer = answer if isinstance(answer, dict) else {}
    entry = (answer.get('sensors') or {}).get(hottest_sensor) or {}
    params = {
        'sensor': hottest_sensor,
        'pairs': entry.get('pairs', 0),
        'looked_at': answer.get('looked_at', 0),
        'degrees': entry.get('degrees_per_10pct'),
        'lowest': entry.get('lowest'),
        'highest': entry.get('highest'),
        'from_pct': entry.get('from_pct'),
        'to_pct': entry.get('to_pct'),
        'span_pct': entry.get('span_pct'),
        'rpm_ratio': entry.get('rpm_ratio'),
        'headroom_c': headroom_c,
        'throttled': throttled,
        'settle_seconds': SETTLE_SECONDS,
    }

    if not entry or entry.get('why') == 'too_few_pairs':
        params['needed'] = MIN_PAIRS
        params['step_pct'] = MIN_STEP_PCT
        return {'code': 'trend.waiting', 'params': params, 'qualifier': None}

    if entry.get('why') == 'span_too_narrow':
        params['needed_span_pct'] = MIN_SPAN_PCT
        return {'code': 'trend.narrow', 'params': params, 'qualifier': None}

    # Settling qualifies an answer, it does not replace one. A figure taken
    # before the chassis finished moving is still the best figure there is; it
    # is just a floor under the real one, and saying so is cheaper than
    # withholding it. Where some pairs moved and others could not be judged,
    # the stronger statement is the one that is made: something WAS still
    # moving, which is knowledge, over nobody could tell, which is not.
    if entry.get('still_moving'):
        qualifier = 'trend.lower_bound'
    elif entry.get('settling_unknown'):
        qualifier = 'trend.settling_unknown'
    else:
        qualifier = None

    degrees = entry.get('degrees_per_10pct')
    if degrees is None:
        return {'code': 'trend.waiting', 'params': params, 'qualifier': None}

    if entry.get('why') == 'within_noise':
        params['noise_floor'] = entry.get('noise_floor')
        return {'code': 'trend.within_noise', 'params': params, 'qualifier': qualifier}

    # What the measured span bought - and only the measured span. A rate per
    # ten points is abstract and "about six degrees between 20 and 100 per
    # cent" is not, but the second one is only allowed to name the duties
    # somebody actually watched.
    params['span_c'] = entry.get('span_c')

    if degrees < 0:
        return {'code': 'trend.inverse', 'params': params, 'qualifier': qualifier}
    if degrees < POOR_SLOPE_DEGREES:
        params['threshold'] = POOR_SLOPE_DEGREES
        return {'code': 'trend.poor', 'params': params, 'qualifier': qualifier}

    return {'code': 'trend.good', 'params': params, 'qualifier': qualifier}


def hottest(summary):
    """Which sensor ran hottest over a window - the one a verdict is about.

    `summary` is {sensor name: one presented record}, which is one window's
    worth and not the {window: record} map summarise() returns. Returns
    (name, maximum) or (None, None).

    By the maximum reached and not by the mean, for the same reason
    sensors.decide() takes the hottest group and never an average: what a
    cooling argument is about is the hottest thing in the box, and a sensor
    that spends most of its time cool and touches 90 C under load is that
    thing even though its mean is unremarkable.

    It answers which sensor, and nothing about what that sensor's limit is. A
    caller that pairs this with tjmax must satisfy itself that the name that
    came back is a core; see verdict().
    """
    best = None
    name = None
    for sensor, record in (summary or {}).items():
        value = (record or {}).get('max')
        if value is None:
            continue
        if best is None or value > best:
            best = value
            name = sensor
    return name, best
