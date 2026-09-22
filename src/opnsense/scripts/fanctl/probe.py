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

    WHAT IS IN THIS MACHINE, ESTABLISHED BY READING IT - AND WHAT CANNOT BE
    ESTABLISHED WITHOUT WRITING TO IT.

    This file runs on appliances nobody here has ever opened. It writes
    nothing: not a duty, not a mode, not a bank select. Everything it reports
    it read, and everything it cannot read it says it cannot read, by name.

    THE SIX QUESTIONS, AND HOW HONESTLY EACH ONE CAN BE ANSWERED

      1. Is there a Super I/O this helper understands?
         ANSWERABLE BY READING. `fanctl-io identify` either names a chip from
         its own table or refuses with a code, and each refusal means a
         different thing: no /dev/io (2), not root or securelevel too high (3),
         a chip that answered but is not one this helper drives (4).

      2. How many channels has it?
         ANSWERABLE, WITH A CAVEAT THAT MATTERS. The count is the chip's, from
         the helper's table - it is how many sets of registers exist, not how
         many fan headers the board fitted and not how many of those have a fan
         in them. A five-channel chip on a two-fan appliance still reports five.

      3. Which channels have a tachometer?
         HALF-ANSWERABLE, AND THIS IS THE IMPORTANT ONE. A channel reading
         above the stall threshold has a tachometer AND a fan turning on it:
         that is a fact and it is the only fact of its kind available without
         writing. A channel reading zero is FOUR different machines wearing one
         number - no fan on the header, a fan with no tach wire, a fan that has
         stopped, or a header this chip does not sample - and nothing in
         software can separate them. So this file never writes "stopped" about
         a channel reading zero. It writes "silent", and says what silent
         covers, every time.

      4. Which channels respond to a duty change?
         NOT ANSWERABLE BY READING AT ALL. It takes a write and a tachometer to
         watch afterwards: see response.py. Two weaker things are offered
         instead, and both are marked as what they are:
           * the RECORD of a response test somebody ran here before, kept in
             measured.json - evidence, dated, on this machine;
           * a PASSIVE observation (`probe.py watch`), which watches the chip's
             own automatic curve move a channel's output and sees whether the
             tachometer follows. It costs nothing and writes nothing, and it
             only concludes anything on a machine whose chip is already
             changing that output while we look.

      5. Which channels are under an automatic curve?
         ANSWERABLE. Bits 6:4 of the mode register are the control law, and the
         helper reports both the nibble and its name. Nibbles 5..7 exist in the
         field and are named in no document available here, so they are
         reported as reserved and never described as anything else.

      6. What is the chip's own critical-duty backstop set to?
         ANSWERABLE. The helper reports, per channel, whether the chip's
         last-ditch duty is armed and what it is set to. This matters more than
         it looks: on a channel where it is armed, a controller that dies with
         a duty latched has something in HARDWARE behind it. On a channel where
         it is not, nothing in the machine will change that duty until a person
         does.

    WHAT THIS FILE REFUSES, AS BEHAVIOUR

    Every refusal below is a value in the answer, not a paragraph in a
    document, and each one names itself so a page and a log can say the same
    word:

      no-helper       the helper is not installed or not executable here
      no-device       /dev/io (or /dev/superio0) is not present on this machine
      no-permission   the helper must be root, with kern.securelevel below 1
      unknown-chip    a Super I/O answered and it is not one the helper drives
      chip-unreadable the helper reached the chip and could not read it
      no-tachometer   this channel reports no turning fan
      no-response     no response test has ever been run on this channel here
      not-in-table    the hardware table does not permit driving this channel
      held-elsewhere  the channel is held at a duty this caller did not ask for,
                      or has been moved out of the state a takeover leaves it in
      held-unknown    the channel is held in manual through this plugin's helper
                      and this caller has not said what it last wrote, so it
                      cannot tell its own hold from a hand-set one
      unstable-registers  the channel's settings read differently twice in a
                      second, so nothing about it is settled enough to act on

    unstable-registers is not theoretical, and neither is held-unknown. On the
    reference appliance, at the time this file was written, channel 1 was held
    at 100% by hand while the helper's saved record of that channel's original
    said SmartFan IV at raw 50 - 20% (V, `fanctl-io status`, 2026-09-22).
    Anything that "restores" such a channel gives the fan back to 20%, not to
    the 100% it is doing now; and a daemon that mistook that hold for its own
    could lower it. So a hold whose owner this call cannot establish is refused
    for driving and for testing, and the refusal quotes both states.

    WHAT A HOLD CANNOT SAY. The helper records THAT a channel was taken over,
    not WHO took it: this plugin's daemon and a person typing `fanctl-io duty`
    leave byte-for-byte the same thing behind (D, fanctl-io.c state_save_once).
    So a caller that is driving fans passes what it last wrote - see
    takeover_of and probe(expected=...) - and a caller that cannot is refused
    rather than allowed to guess. The refusal is not squeamishness: guessing
    the wrong way round freezes the plugin out of the fan it is holding, which
    is measured and described in takeover_of.

    AND NO REFUSAL HERE MAY BECOME A FAN NOBODY CAN SPEED UP. Both refusals
    about a hold - held-elsewhere and held-unknown - describe a channel already
    in manual at a known output, which nothing in the chip is going to raise. A
    duty strictly above that output cannot make the fan slower, so it is
    permitted while the owner of the hold is still unknown: every channel
    carries may_raise beside may_drive, and may_raise is never the narrower of
    the two. A daemon that restarts and no longer knows what it last wrote is
    refused a free hand over that channel and is still able to cool the machine.
"""

import json
import os
import statistics
import sys
import time

try:
    import chip
except Exception as _failure:                   # ImportError and anything else
    chip = None
    _CHIP_IMPORT_ERROR = str(_failure)
else:
    _CHIP_IMPORT_ERROR = ''

# Mirrors of two numbers in fanctl.py, and the duplication is deliberate: this
# file must be importable by fanctl.py (for the page's Hardware tab) without
# importing it back, and a fan controller that cannot run its own probe because
# of an import cycle is worse than two copies of an integer. They are named
# here, once, and fanctl.py should import them from here rather than the other
# way round.
# STALL_RPM is R, and it is a bracket rather than a measurement. What was
# measured is on either side of it: a channel with a fan on it read 1332 rpm at
# the lowest duty its own chip ever asked for and 5133-5844 rpm at full, and
# every channel without one read exactly 0 and never a number in between (V,
# `fanctl-io status` on the reference appliance, 2026-09-22). 200 sits in a gap
# nothing has ever been seen in. The rpm at which THIS fan actually stops has
# not been measured and cannot be from here: that takes a write and somebody
# standing in front of the machine.
STALL_RPM = 200

# SPIN_SECONDS is R and it is the weakest number in this file. Nobody has
# measured how long this fan takes to settle at a new speed, because measuring
# it needs a write and this file does not write. It is used by response.py as
# the wait after a duty change and it was chosen to be longer than any fan's
# electrical response, not derived from one. Replace it with a measurement when
# somebody runs a response test with a clock.
SPIN_SECONDS = 6.0

# What counts as a tachometer having followed an output, both rules to be
# satisfied: a relative rule alone calls tachometer noise a response on a slow
# fan, an absolute rule alone misses a real response on a fast one. R, and the
# pair lives HERE rather than in response.py because response.py imports this
# module and not the other way round - the same direction the two constants
# above are named in. response.py's RESPONSE_RPM and RESPONSE_FRACTION are
# these same two values today (D, response.py:163-164) and should be taken from
# here so that they cannot drift apart.
RESPONSE_RPM = 120
RESPONSE_FRACTION = 0.15

# Below this many points, a difference in output is not a move: raw_to_pct
# folds 256 raw steps into 101 percentages (D, fanctl-io.c raw_to_pct), so a
# one-point difference can be a single raw step or the rounding at its edge.
OUTPUT_MOVE_POINTS = 2

# The manual nibble, which is what a takeover by this plugin leaves behind (D,
# fanctl-io.c cmd_duty writes MODE_MANUAL << 4; V, 2026-09-22: a channel taken
# over at 100% reads back mode 0, duty_raw 255, which mode_name calls "off" -
# the same nibble, and this is why the nibble is compared and not the name).
MODE_MANUAL = 0

# Where the plugin keeps what it has measured. Read here, never written here.
STATE_DIR = '/var/db/fanctl'
MEASURED = os.path.join(STATE_DIR, 'measured.json')
TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fans.json')

# The control laws, as the helper's mode_name() reports them (D, fanctl-io.c
# mode_name(), decoded from reg_to_pwm_enable() in nct6775-core.c:900-905).
# Nibble 0 with a duty of 255 is the chip's own "PWM control off", which is the
# same output as manual-at-full and a different statement about who is in
# charge - so the two are not merged here either.
AUTOMATIC_LAWS = ('thermal-cruise', 'speed-cruise', 'smartfan-iii', 'smartfan-iv')


class ProbeError(Exception):
    """Something the probe could not do. Always carries a slug and a sentence."""

    def __init__(self, slug, message):
        self.slug = slug
        self.message = message
        super().__init__(message)


def _read_json(path, default=None):
    """A small local reader rather than an import of fanctl.py.

    Five lines duplicated on purpose: importing the daemon from the probe would
    make the probe unrunnable whenever the daemon fails to import, and the
    probe is the thing somebody runs to find out why.
    """
    try:
        with open(path, 'r') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def load_table(path=TABLE):
    """The hardware table, or an empty one.

    Entries under "example_models" are NEVER loaded. That key exists so the
    file can carry a worked example of the shape a contributor has to fill in,
    and keeping it out of "models" is what stops the example from ever being
    matched by anything - no flag to check, no code to get wrong.
    """
    document = _read_json(path, default={}) or {}
    return document.get('models') or {}


# ---------------------------------------------------------------------------
# Reaching the chip, and saying precisely why not
# ---------------------------------------------------------------------------

# Fields of a channel that are SETTINGS: they describe how the chip has been
# told to behave, and a setting that reads differently twice in a second is not
# a setting, it is a disturbed read. The tachometer is not in this list because
# it legitimately moves, and the helper checks that one itself (rpm_stable).
_SHOULD_NOT_MOVE = ('mode', 'duty_raw', 'output_raw', 'critical_duty_raw',
                    'critical_duty_armed', 'floor_pct')

# The same list for a channel under one of the chip's OWN automatic laws, and
# the two fields it drops are the whole correction. Under SmartFan IV the duty
# register and the readback register are not settings at all - they are the
# chip's output, and the chip moves them whenever the temperature moves. This
# file's header records the measurement: a duty written under that law read
# back as the chip's own 0x32, twice (V, 2026-09-22). watch() below depends on
# exactly that movement as its only evidence. Calling it "unstable" refused to
# drive precisely the machines this plugin exists for - the ones whose firmware
# curve is already running and is wrong (V, 2026-09-22: two reads of a
# smartfan-iv channel a second apart, duty_raw 50 then 56, answered
# unstable-registers, "either something else is writing to this chip").
_SHOULD_NOT_MOVE_AUTOMATIC = tuple(field for field in _SHOULD_NOT_MOVE
                                   if field not in ('duty_raw', 'output_raw'))


def access(helper=None, confirm=True):
    """How this machine can be reached, and by what.

    Returns a dict with 'path' - 'helper', 'sysctl' or 'none' - and, when the
    helper could not be used, the slug and the helper's own words. Nothing here
    guesses: every sentence in the failure case comes from chip.py, which got
    it from fanctl-io, which got it from the machine.

    `confirm` reads the chip TWICE and marks every setting that disagreed
    between the two passes. It costs one extra walk over the window and it is
    worth it here, where the probe is something a person runs occasionally and
    never something the daemon does every cycle. The reason it exists is a
    measurement: on the reference appliance, channel 4's critical-duty register
    read 204 at one moment and 4 - consistently, three times over - a quarter
    of an hour later, with nothing in this session having written anything (V,
    2026-09-22). Either something else wrote to that chip or one of those reads
    was disturbed by the kernel's own reader walking the same two ports without
    a lock, and this plugin cannot tell which. What it CAN do is stop
    presenting a number it is not sure of as a fact.
    """
    if chip is None:
        return {'path': 'none', 'writable': False, 'slug': 'no-helper',
                'detail': 'the hardware layer (chip.py) could not be imported: %s'
                          % _CHIP_IMPORT_ERROR,
                'chip': {}, 'channels': [], 'hard_floor_pct': None}

    device = chip.Chip(helper) if helper else chip.Chip()

    # A missing helper is said in its own words, and this is a correction.
    # chip.py raises the SAME exit code for a helper that is not installed and
    # for a machine with no /dev/io (D, chip.py Chip._run, Unavailable(2) on
    # both), and the code that mapped that exit code straight to 'no-device'
    # therefore told people "/dev/superio0 or /dev/io is not present on this
    # machine" about a machine nobody had asked (V, 2026-09-22: with the helper
    # path pointed at a file that does not exist on the reference appliance,
    # whose /dev/io demonstrably works, the answer was slug "no-device", means
    # "/dev/superio0 or /dev/io is not present on this machine"). The one thing
    # that can be checked without touching the chip is whether the helper is
    # there, so it is checked first and named first.
    if not device.installed():
        return _with_sysctl({
            'path': 'none', 'writable': False, 'slug': 'no-helper',
            'detail': '%s is not installed or not executable' % device.helper,
            'means': ('the program this plugin reads and writes the chip with is not on '
                      'this machine. Nothing has been read from the chip, and nothing is '
                      'claimed about the chip or about this machine\'s I/O devices.'),
            'chip': {}, 'channels': [], 'hard_floor_pct': None})

    try:
        status = device.status()
    except chip.ChipError as failure:
        answer = {'path': 'none', 'writable': False, 'slug': _slug_for(failure.code),
                  'detail': failure.message, 'means': failure.explanation,
                  'code': failure.code, 'chip': {}, 'channels': [],
                  'hard_floor_pct': None}
        # The read-only fallback, and only for the failures that mean the
        # helper is not live on this machine at all. A Busy or a read-back
        # failure means something IS walking that window right now, and the
        # sysctls walk the same two ports with no lock - so on those the probe
        # reports nothing rather than joining a race to find out. (chip.py
        # makes the same distinction for the same reason; this is the second
        # place it is enforced, not a different rule.)
        if failure.code in (2, 3, 4):
            answer = _with_sysctl(answer)
        return answer

    channels = status.get('channels') or []
    if confirm:
        channels = _confirm(device, channels)

    return {'path': 'helper', 'writable': True, 'slug': '', 'detail': '',
            'chip': status.get('chip') or {},
            'channels': channels,
            'hard_floor_pct': status.get('hard_floor_pct'),
            'channel1_output': status.get('channel1_output') or '',
            'note': status.get('note') or ''}


# One vocabulary for the whole file. The header promises that a page and a log
# say the same word for the same condition, and watch() used to raise the
# helper's own slugs ('chip', 'busy', 'readback'), which are not words this
# file defines (D, chip.py CODES).
_SLUGS = {2: 'no-device', 3: 'no-permission', 4: 'unknown-chip'}


def _slug_for(code):
    return _SLUGS.get(code, 'chip-unreadable')


def _with_sysctl(answer):
    """Add the tachometer-only view to a failure answer, if that path exists."""
    fallback = chip.SysctlFans()
    if fallback.available():
        reading = fallback.read()
        answer.update({'path': 'sysctl', 'channels': reading['channels'],
                       'sysctl_note': reading['note'], 'sysctl_why': reading['why']})
    return answer


def _confirm(device, channels):
    """Read the settings a second time and mark the ones that disagreed.

    A failed second read is not a disagreement: it is reported as such and
    nothing is marked, because "the second pass could not be taken" and "the
    two passes disagreed" are different facts about the machine.

    Which fields count depends on who is driving the channel, and that is the
    correction described at _SHOULD_NOT_MOVE_AUTOMATIC: on a channel under the
    chip's own law the duty and readback registers are the chip's output and
    are SUPPOSED to move. When they do, it is recorded as what it is - the chip
    moving its own fan while we watched, which is the same evidence watch()
    goes looking for - and not as a disturbed read.
    """
    try:
        again = {int(entry['channel']): entry
                 for entry in (device.status().get('channels') or [])}
    except chip.ChipError as failure:
        for entry in channels:
            entry['second_read'] = 'failed: %s' % failure.message
        return channels

    for entry in channels:
        twin = again.get(int(entry.get('channel', 0)))
        if twin is None:
            entry['second_read'] = 'the second pass did not report this channel at all.'
            continue

        automatic = law_of(entry)[0] == 'automatic'
        fields = _SHOULD_NOT_MOVE_AUTOMATIC if automatic else _SHOULD_NOT_MOVE
        moved = [field for field in fields
                 if field in entry and field in twin and entry[field] != twin[field]]
        entry['second_read'] = 'agreed' if not moved else 'disagreed'

        if automatic:
            drifted = [field for field in ('duty_raw', 'output_raw')
                       if field in entry and field in twin and entry[field] != twin[field]]
            if drifted:
                entry['chip_moved'] = drifted
                entry['chip_moved_detail'] = (
                    'between the two reads the chip changed this channel by itself: %s. That '
                    'is what an automatic law does, it is not an unstable register, and it is '
                    'evidence that this chip is driving this channel right now.'
                    % ', '.join('%s (%s then %s)' % (field, entry[field], twin[field])
                                for field in drifted))

        if moved:
            entry['unstable'] = moved
            entry['unstable_detail'] = (
                'read twice within a second, these disagreed: %s. A setting that will not '
                'hold still is not a setting - either something else is writing to this '
                'chip, or a read was disturbed by the kernel reader walking the same two '
                'ports. Nothing here is driven on the strength of a register in that state.'
                % ', '.join('%s (%s then %s)' % (field, entry[field], twin[field])
                            for field in moved))
    return channels


# ---------------------------------------------------------------------------
# What one channel is, read
# ---------------------------------------------------------------------------

def name_for(entry):
    """The name this plugin calls a channel by: the driver's alias, or FAN<n>.

    Same rule as fanctl.py's Hardware.name_for, and for the same reason: the
    alias is what the sysctl prints and what the table is written in, and a
    chip whose channels ncthwm(4) has no table for has no aliases at all.
    """
    number = int(entry.get('channel'))
    alias = entry.get('alias')
    if not alias and chip is not None:
        alias = chip.Chip().alias(number)
    return str(alias) if alias else 'FAN%d' % number


def law_of(entry):
    """(kind, sentence) for the control law a channel is under.

    kind is 'off', 'manual', 'automatic' or 'reserved'. Each of the four means
    something different for whether taking the channel over is a change the fan
    can feel, which is why they are not collapsed into "not manual".
    """
    mode = entry.get('mode')
    name = str(entry.get('mode_name') or '')
    duty = entry.get('duty_raw')

    if mode is None:
        return 'unknown', ('this path cannot read the control law at all - it reads '
                           'tachometers and nothing else.')
    mode = int(mode)
    if mode == 0 and duty == 255:
        return 'off', ('PWM control is off on this channel: the chip is not modulating it '
                       'and the output is full. Taking it over would be a change the fan '
                       'can hear.')
    if mode == 0:
        return 'manual', ('this channel is under a fixed duty - %s%% - and nothing is '
                          'changing it with temperature.'
                          % (entry.get('duty_pct') if entry.get('duty_pct') is not None
                             else '?'))
    if name in AUTOMATIC_LAWS:
        return 'automatic', ('the chip is driving this channel itself, under %s. Its duty '
                             'register holds what the chip last wrote, and the chip puts '
                             'its own value back over anything written there while that law '
                             'is in charge (V: a duty written first read back as the chip\'s '
                             '0x32, twice).' % name)
    return 'reserved', ('the control law on this channel is nibble %d, which is not named in '
                        'any document available here. It is reported and not interpreted; '
                        'release writes back exactly this value.' % mode)


def tach_of(entry):
    """(state, sentence) for what the tachometer says. Never the word "stopped".

    'turning' is a fact: there is a tachometer here and a fan on it.
    'silent' is four possibilities and says so, every time it is used.
    """
    rpm = entry.get('rpm')
    if rpm is None:
        return 'unreadable', 'the tachometer for this channel could not be read.'
    rpm = int(rpm)
    if rpm >= STALL_RPM:
        stable = entry.get('rpm_stable')
        return 'turning', ('%d rpm%s. There is a tachometer on this channel and a fan '
                           'turning on it.'
                           % (rpm, '' if stable is not False else
                              ', and the two reads of that register disagreed, so the '
                              'number is one sample and not a settled speed'))
    if rpm > 0:
        return 'crawling', ('%d rpm, below the %d rpm at which this plugin will call a fan '
                            'turning. Either a fan is barely moving or this is noise on the '
                            'register.' % (rpm, STALL_RPM))
    return 'silent', ('no tachometer signal. This chip is not being told about a fan here, '
                      'and that reads identically for a header with no fan on it, a fan '
                      'with no tachometer wire, a fan that has stopped, and a header this '
                      'chip does not sample. Nothing in software can tell those apart, so '
                      'this channel is never called stopped and is never driven.')


def backstop_of(entry):
    """What the chip itself will do if the thing driving this channel dies.

    The chip's critical-duty register is the only piece of fan safety in this
    machine that does not depend on any software staying alive, so it is
    reported per channel rather than as a property of the board.
    """
    armed = entry.get('critical_duty_armed')
    raw = entry.get('critical_duty_raw')
    percent = None if raw is None else int((int(raw) * 100 + 127) // 255)
    if armed is None:
        return {'armed': None, 'duty_pct': None,
                'detail': 'this path cannot read the chip\'s own backstop.'}
    if armed:
        return {'armed': True, 'duty_pct': percent, 'raw': raw,
                'detail': ('the chip\'s own critical duty is armed at %d%%. If whatever is '
                           'driving this channel dies with a duty latched, the chip still '
                           'has a last-ditch setting of its own.' % (percent or 0))}
    return {'armed': False, 'duty_pct': percent, 'raw': raw,
            'detail': ('the chip\'s own critical duty is NOT armed on this channel (it is '
                       'set to %d%% and disabled). A duty latched here is latched until '
                       'something runs release: nothing in hardware will change it.'
                       % (percent or 0))}


def takeover_of(entry, expected_duty_raw=None):
    """Who is holding this channel, and would a release undo something.

    THE THREE STATE NAMES ARE THE ONES response.py ALREADY READS (D,
    response.py:477-481) and they are not renamed here: renaming them would
    have left that file's two `if` branches matching nothing, so a response
    test - which writes a duty and then releases the channel - would have
    stopped refusing to run on a channel somebody is holding. The meanings are
    corrected underneath the names, and what the correction needs beyond them
    is carried in 'reason' and 'expectation' rather than in a fourth name.

      'free'   the helper holds no record: nobody has taken this channel over
               through this plugin, so a release would write nothing.
      'ours'   the helper holds a record and the channel is in the manual mode
               a takeover leaves it in. Something asked this helper for a fixed
               duty here. WHICH something is not written anywhere the chip can
               be asked: this plugin's daemon and a person typing `fanctl-io
               duty` at the console leave byte-for-byte the same thing behind,
               so 'ours' means "held through this plugin" and never more than
               that. 'expectation' says whether the caller recognised the duty:
               'matched' (it is the caller's own), 'not-given' (the caller did
               not say what it last wrote).
      'stale'  either the channel is held in manual at a duty the caller says
               it did NOT ask for ('reason': 'duty-not-ours'), or it is not in
               manual at all and the record would restore a state nobody here
               put there ('reason': 'moved'). Both are refused for a response
               test; only the first is refused a duty, because the second is a
               channel the chip has taken back and taking it over again is an
               ordinary takeover.

    THE CORRECTION THIS REPLACES, AND WHY IT MATTERED. The earlier version
    called the state 'stale' whenever the channel was not IDENTICAL to the
    record - which is every channel this plugin is driving, because taking a
    channel over is exactly what makes it differ from the record of what it was
    before. Measured (V, 2026-09-22, against the code as it stood): a channel
    with saved_original mode 4 / raw 50, now manual at raw 128 - the ordinary
    state of a channel this plugin is holding at 50% - answered state 'stale',
    "something has taken this channel over", and may_drive refused it
    'held-elsewhere'. Under the contract in this file's header, where every
    caller that can write must refuse on that state, the plugin would have taken
    a fan over once and then never been permitted to raise it again. A fan that
    cannot be raised while the firewall heats is the failure this plugin exists
    to prevent, and it would have been this file that caused it. Meanwhile
    'ours' - record present AND current state identical to the record - was only
    reachable if the original was itself manual at exactly the raw later written
    to it, since fanctl-io deletes the record on release (D, fanctl-io.c
    cmd_release, state_forget): dead code standing where the live case should
    have been.

    `expected_duty_raw` is how a caller that knows what it last wrote gets the
    other half of the safety back. Pass it and a hold that is not yours is
    caught and named; pass nothing and a hold is reported as a hold whose owner
    is unknown, which is what may_drive then refuses on. The reference case is
    real: the owner was holding channel 1 at 100% by hand while the record said
    SmartFan IV at raw 50 (V, `fanctl-io status`, 2026-09-22), and a daemon that
    believed that hold was its own could lower the fan under him.
    """
    if not entry.get('saved_original'):
        return {'state': 'free', 'detail': ('the helper holds no record of ever having '
                                            'taken this channel over.')}

    saved_mode = entry.get('saved_mode')
    saved_duty = entry.get('saved_duty_raw')
    saved_pct = None if saved_duty is None else _pct(saved_duty)
    now_mode = entry.get('mode')
    now_raw = entry.get('duty_raw')
    now_pct = entry.get('duty_pct')
    common = {'saved_mode': saved_mode, 'saved_duty_pct': saved_pct,
              'duty_pct': now_pct, 'duty_raw': now_raw}

    if now_mode is None or int(now_mode) != MODE_MANUAL:
        # The record is left over: the channel is back under a law of the
        # chip's own, so the chip is driving this fan and will go on driving
        # it. Taking it over from here is an ordinary takeover and is not
        # refused - refusing it would leave this plugin locked out of the
        # channel until somebody deleted a file, which is the same kind of
        # freeze this function was corrected for. What IS no longer trustworthy
        # is the record: a release would write a state nobody here put there.
        identical = (saved_mode is not None and int(saved_mode) == int(now_mode)
                     and (saved_duty is None or now_raw is None
                          or int(saved_duty) == int(now_raw)))
        return dict(common, state='stale', reason='moved', release_trustworthy=False,
                    detail=(
            ('the helper holds a record for this channel and the channel is not in the '
             'manual mode a takeover leaves it in: it is under mode %s at %s%%, which is '
             'exactly what the record would restore. Something put it back without '
             'clearing the record - a release that did not finish, or a hand. The chip is '
             'driving this fan again; the record is left over and a release from here '
             'would be writing a state this plugin did not put there.'
             % (now_mode, now_pct))
            if identical else
            ('the helper holds a record for this channel and the channel is not in the '
             'manual mode a takeover leaves it in: it is under mode %s at %s%%, while the '
             'record a release would restore says mode %s at %s%%. Something other than '
             'this plugin has moved it. The chip is driving this fan; the record is no '
             'longer a description of anything and a release from here would move the fan '
             'to a third state.' % (now_mode, now_pct, saved_mode, saved_pct))))

    if expected_duty_raw is not None and now_raw is not None \
            and int(now_raw) != int(expected_duty_raw):
        return dict(common, state='stale', reason='duty-not-ours', expectation='mismatched',
                    expected_duty_pct=_pct(expected_duty_raw), detail=(
                        'this channel is held in manual at %s%% (raw %s), and the caller '
                        'last asked for %s%% (raw %s). Somebody has moved it since. '
                        'Nothing here will write over a duty it did not ask for: it may be '
                        'the hand that is cooling this machine.'
                        % (now_pct, now_raw, _pct(expected_duty_raw), expected_duty_raw)))

    return dict(common, state='ours', holder='this plugin\'s helper',
                expectation='matched' if expected_duty_raw is not None else 'not-given',
                detail=(
                    'this channel is held in manual at %s%%, by something that went through '
                    'this plugin\'s helper - the record of its original (mode %s at %s%%) is '
                    'still there. Whether that something is this plugin\'s daemon or a person '
                    'at the console is not written anywhere the chip can be asked: both leave '
                    'the same bytes behind.%s'
                    % (now_pct, saved_mode, saved_pct,
                       '' if expected_duty_raw is not None else
                       ' Nobody has told this call what it last wrote, so it cannot tell its '
                       'own hold from a hand-set one.')))


def _pct(raw):
    """Raw duty to percent, rounded as the helper rounds it (D, raw_to_pct)."""
    return None if raw is None else int((int(raw) * 100 + 127) // 255)



def response_record(name, measured=None):
    """What a response test on this channel measured here, if one ever ran.

    Read from measured.json, where response.py writes it. A record is evidence
    about THIS machine with a date on it; the absence of one is not evidence
    about anything.
    """
    document = measured if measured is not None else (_read_json(MEASURED) or {})
    record = ((document.get('channels') or {}).get(name) or {}).get('response')
    if not record:
        return {'measured': False,
                'detail': ('no response test has been run on this channel on this machine. '
                           'Whether a duty written here reaches a fan is not known, and '
                           'cannot be known by reading.')}
    return {
        'measured': True,
        'responded': bool(record.get('responded')),
        # Carried rather than reduced to a boolean: "no-change" and "inverted"
        # are both "did not respond" and they mean entirely different things to
        # whoever reads the page - the first is an unknown, the second is a
        # channel that did something nobody understands.
        'verdict': record.get('verdict') or '',
        'at': record.get('at'),
        'from_pct': record.get('from_pct'),
        'to_pct': record.get('to_pct'),
        'rpm_before': record.get('rpm_before'),
        'rpm_after': record.get('rpm_after'),
        'detail': record.get('detail') or '',
    }


# ---------------------------------------------------------------------------
# The refusals, as behaviour
# ---------------------------------------------------------------------------

def table_permits(entry, name):
    """(bool, sentence) - does the hardware table permit driving this channel?

    Two gates, and the channel one is the narrower. A model may be permitted
    while a particular channel on it is not: on the reference appliance the
    fan control on channel 1 is measured, and the other four channels are
    headers nobody has identified. An entry marks those with "drive": false and
    a reason, and this is the function that enforces it.
    """
    if not entry:
        return False, ('this machine matches no entry in the hardware table, so nothing is '
                       'known about which header turns which fan.')
    if not entry.get('control'):
        return False, str(entry.get('control_reason')
                          or 'the hardware table does not permit control on this model.')
    for fan in entry.get('fans') or []:
        if fan.get('channel') == name:
            if fan.get('drive') is False:
                return False, str(fan.get('drive_reason')
                                  or 'the table permits this model and not this channel.')
            return True, 'the table permits this model and this channel.'
    return False, ('the table permits this model but describes no fan on %s, and an '
                   'undescribed header is not a fan.' % name)


def _gate(view, entry, table_entry, require_response):
    """Every reason not to write to this channel, in the order to show them.

    Returned as a list rather than the first one, because two different
    questions are asked of it: may a duty be written at all, and may a HIGHER
    duty be written. They differ by exactly the two reasons about who owns a
    hold, and by nothing else.

    Every field is read with .get(): this is called with views built by
    channel_view() alone, which does not carry the access keys (V, 2026-09-22:
    the earlier may_drive on such a view raised KeyError 'access' - an uncaught
    traceback where a refusal belonged, in the one file whose job is to refuse
    well).
    """
    reasons = []
    if view.get('access') != 'helper':
        return [(view.get('access_slug') or 'no-helper',
                 view.get('access_detail')
                 or 'this channel was not read through the helper, so nothing may be '
                    'written to it.')]
    if view.get('unstable'):
        reasons.append(('unstable-registers', view.get('unstable_detail') or ''))

    takeover = view.get('takeover') or {}
    state = takeover.get('state')
    if state == 'stale' and takeover.get('reason') == 'duty-not-ours':
        reasons.append(('held-elsewhere', takeover.get('detail') or ''))
    elif state == 'ours' and takeover.get('expectation') != 'matched':
        # Not a refusal about the hardware: a refusal about what this call
        # knows. A caller that passes what it last wrote gets past it, and one
        # that cannot is not allowed to guess - see takeover_of.
        reasons.append(('held-unknown', takeover.get('detail') or ''))
    # 'stale' with reason 'moved' is deliberately NOT here: the chip has that
    # channel back and is driving the fan with it, so taking it over is an
    # ordinary takeover. Refusing it would lock this plugin out of the channel
    # until somebody deleted a file by hand - the same freeze, in a new place.
    # What that state does cost is the release record, and takeover_of marks it
    # release_trustworthy: false for whoever hands the channel back.

    if view.get('tach_state') != 'turning':
        reasons.append(('no-tachometer', view.get('tach_detail') or ''))

    permitted, why = table_permits(table_entry if table_entry is not None else entry,
                                   view.get('name'))
    if not permitted:
        reasons.append(('not-in-table', why))

    record = view.get('response') or {}
    if require_response and not record.get('measured'):
        reasons.append(('no-response', record.get('detail') or ''))
    elif require_response and not record.get('responded'):
        reasons.append(('no-response', 'a response test was run on this channel and the '
                                       'tachometer did not follow the duty. Whatever this '
                                       'channel drives, it is not the fan this tachometer '
                                       'counts.'))
    return reasons


# The two reasons that are about WHOSE hold this is rather than about the
# hardware. They are the only ones a higher duty is allowed past, and the
# argument is short enough to check: both of them describe a channel that is
# already in manual at a known output, nothing in the chip is going to raise it,
# and a duty strictly above what it is putting out now cannot make the fan
# slower than it is. Everything else in the gate - an unreadable chip, unstable
# registers, no tachometer, a channel the table forbids - is a reason not to
# write ANY duty, and stays a reason.
_ABOUT_THE_HOLDER = ('held-elsewhere', 'held-unknown')


def may_drive(view, entry, table_entry=None, require_response=True):
    """(bool, slug, sentence) - may this plugin write a duty to this channel?

    This is the whole of the refusal behaviour for unknown hardware, in one
    place, in the order the reasons should be shown. It answers about
    PERMISSION and EVIDENCE only. The floor is judged separately, by
    fanctl.floor_for, and the six-line contract in fanctl.py is what decides
    moment to moment - this function decides what may be offered at all.
    """
    reasons = _gate(view, entry, table_entry, require_response)
    if reasons:
        return False, reasons[0][0], reasons[0][1]

    # The sentence has to match the evidence that was actually required. The
    # earlier one said "a response test measured on this machine saw the
    # tachometer follow a duty change" on every success, including calls made
    # with require_response=False on a channel where no test had ever been run
    # (V, 2026-09-22: may_drive(..., require_response=False) returned exactly
    # that sentence while the channel's response record said measured: false).
    # That is the plugin claiming a measurement nobody took.
    record = view.get('response') or {}
    if record.get('measured') and record.get('responded'):
        return True, '', ('this channel has a turning fan, the table permits it, and a '
                          'response test measured on this machine saw the tachometer follow '
                          'a duty change.')
    return True, '', ('this channel has a turning fan and the table permits it. No response '
                      'test has been run on it here, and this answer was asked for without '
                      'requiring one, so whether a duty written here reaches this fan is '
                      'still unmeasured.')


def may_raise(view, entry, table_entry=None, require_response=True):
    """(bool, slug, sentence) - may a duty ABOVE this channel's output be written?

    This exists so that a refusal about ownership can never become a fan that
    nobody may speed up. A channel held in manual by a hand this call cannot
    identify is refused by may_drive, and rightly: writing over somebody's hold
    can lower a fan that is cooling a machine. But the firewall can heat up
    while that is true, and the daemon that would raise the fan is the same
    daemon being refused. A duty strictly above what the channel is putting out
    now cannot make the fan slower, so it is permitted with the hold's owner
    still unknown, and every other refusal in the gate still applies.

    The caller enforces the "strictly above" part: this function is about
    permission, and the number is the caller's. fanctl-io refuses anything below
    the floor underneath both of them (D, fanctl-io.c cmd_duty, EX_FLOOR_).
    """
    reasons = [pair for pair in _gate(view, entry, table_entry, require_response)
               if pair[0] not in _ABOUT_THE_HOLDER]
    if reasons:
        return False, reasons[0][0], reasons[0][1]
    output = view.get('output_pct')
    if output is None:
        output = view.get('duty_pct')
    return True, '', ('a duty above the %s%% this channel is putting out now may be written '
                      'to it: whatever is holding it, a higher duty cannot make this fan '
                      'slower than it is turning now.' % output)


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

def channel_view(entry, measured=None, expected_duty_raw=None):
    """Everything readable about one channel, and every refusal it earns.

    `expected_duty_raw` is what the caller last wrote to this channel, if it
    knows; it is passed straight to takeover_of and is what lets a hold be
    recognised as the caller's own rather than guessed at.
    """
    name = name_for(entry)
    law, law_detail = law_of(entry)
    state, detail = tach_of(entry)
    return {
        'name': name,
        'number': entry.get('channel'),
        'rpm': entry.get('rpm'),
        'rpm_stable': entry.get('rpm_stable'),
        'duty_pct': entry.get('duty_pct'),
        'output_pct': entry.get('output_pct'),
        'mode': entry.get('mode'),
        'mode_name': entry.get('mode_name'),
        'law': law,
        'law_detail': law_detail,
        'tach_state': state,
        'tach_detail': detail,
        'helper_floor_pct': entry.get('floor_pct'),
        'helper_floor_source': entry.get('floor_source'),
        'backstop': backstop_of(entry),
        'takeover': takeover_of(entry, expected_duty_raw),
        'response': response_record(name, measured),
        'unstable': entry.get('unstable') or [],
        'unstable_detail': entry.get('unstable_detail') or '',
        'chip_moved': entry.get('chip_moved') or [],
        'chip_moved_detail': entry.get('chip_moved_detail') or '',
        'second_read': entry.get('second_read') or 'not taken',
    }



def probe(helper=None, table_path=TABLE, model=None, entry=None,
          require_response=True, expected=None):
    """The whole machine, read. Writes nothing, and cannot be made to.

    `entry` is the hardware-table entry fanctl.py matched by SMBIOS. When it is
    not given and `model` is, the table at `table_path` is consulted for it -
    which is what `table_path` is for, and the earlier version accepted that
    argument and never used it, so load_table() was dead and every channel was
    refused 'not-in-table' on a machine the table describes (V, 2026-09-22:
    probe() mentioned table_path once, in its own signature, and load_table
    nowhere). With neither, nothing is looked up and every channel is refused
    'not-in-table', which remains the correct answer for a machine whose model
    has not been identified.

    `expected` is {channel name: the raw duty this caller last wrote}, for a
    caller that is already driving fans here. Without it a held channel is
    reported as held by somebody unknown and refused - see takeover_of.
    """
    reached = access(helper)
    measured = _read_json(MEASURED) or {}
    if entry is None and model:
        entry = load_table(table_path).get(str(model))
    expected = expected or {}

    views = []
    for item in reached.get('channels') or []:
        view = channel_view(item, measured, expected.get(name_for(item)))
        view['access'] = reached['path']
        view['access_slug'] = reached.get('slug') or ''
        view['access_detail'] = reached.get('detail') or ''
        ok, slug, why = may_drive(view, entry, entry, require_response)
        view['may_drive'] = ok
        view['refusal'] = slug
        view['refusal_detail'] = why
        # Reported for every channel, and it is never narrower than may_drive:
        # a fan that may not be changed may still always be sped up. See
        # may_raise for why that is safe and why it has to exist.
        up_ok, up_slug, up_why = may_raise(view, entry, entry, require_response)
        view['may_raise'] = up_ok
        view['raise_refusal'] = up_slug
        view['raise_refusal_detail'] = up_why
        views.append(view)

    turning = [view for view in views if view['tach_state'] == 'turning']
    drivable = [view for view in views if view['may_drive']]
    raisable = [view for view in views if view['may_raise']]

    return {
        'status': 'ok',
        'reached': reached['path'] != 'none',
        'at': time.time(),
        'access': {
            'path': reached['path'],
            'writable': bool(reached.get('writable')),
            'slug': reached.get('slug') or '',
            'detail': reached.get('detail') or '',
            'means': reached.get('means') or '',
            'hard_floor_pct': reached.get('hard_floor_pct'),
        },
        'chip': reached.get('chip') or {},
        'channel1_output': reached.get('channel1_output') or '',
        'model': model or '',
        'table_matched': bool(entry),
        'channels': views,
        'summary': {
            'channels': len(views),
            'with_a_turning_fan': len(turning),
            'may_be_driven': len(drivable),
            'may_be_raised': len(raisable),
            'sentence': _summary(reached, views, turning, drivable),
        },
        'unknowable': [
            'Which fan in the chassis is on which channel. The chip numbers its '
            'channels and knows nothing about the box they are in.',
            'Whether a channel reading no tachometer signal has a fan on it. Four '
            'different machines read as zero here and no software can separate them.',
            'Whether a duty written to a channel reaches a fan. That takes a write and a '
            'tachometer watched afterwards - see response.py, which is never automatic.',
            'The duty at which a fan stops. That is a measurement taken at the boundary, '
            'on the machine, by somebody standing in front of it.',
            'Whether a channel drives its fan by pulse width or by voltage, except on '
            'channel 1 of this chip family, which has a bit for it. A duty on a voltage '
            'channel stalls in a different place and no floor here was chosen for one.',
            'Whether a channel held in manual is held by this plugin or by a person at '
            'the console. The helper records that a takeover happened, not who asked for '
            'it, and the two leave the same bytes behind.',
        ],
    }



def _summary(reached, views, turning, drivable):
    if reached['path'] == 'none':
        return ('No fan controller could be reached on this machine: %s'
                % (reached.get('detail') or 'the helper gave no reason.'))
    if reached['path'] == 'sysctl':
        return ('Fan speeds are being read through the kernel driver, which publishes '
                'tachometers and nothing else. Nothing can be set from here: %s'
                % (reached.get('detail') or ''))
    return ('%s, %d channels. %d report a turning fan; %d may be driven. Everything else on '
            'this page was read, not inferred.'
            % ((reached.get('chip') or {}).get('name', 'an unnamed chip'), len(views),
               len(turning), len(drivable)))


# ---------------------------------------------------------------------------
# The passive response observation
# ---------------------------------------------------------------------------

def watch(seconds=120.0, interval=5.0, helper=None):
    """Watch the chip drive its own fans, and see whether the tachometers follow.

    WRITES NOTHING. This is the only evidence about response that can be had
    without taking a channel over, and it is available exactly when the chip's
    own automatic law is moving an output while we look: if the duty coming out
    of a channel changes and its tachometer changes with it, that channel
    drives that fan - measured, on this machine, at no cost at all.

    Its weakness is the honest half: on a machine that is idle and cool,
    nothing moves, and the answer is "inconclusive, nothing changed" rather
    than "no response". A firewall under no load overnight will give that
    answer every time, and that is not a fault in the observation.

    One helper run per sample for the whole chip (`status`), not one per
    channel: every extra run is another walk over a window the kernel's own
    reader touches without taking a lock.

    The bounds are R: 10 seconds because two samples are the fewest that can be
    compared at all, 900 because a read-only observation that runs longer than a
    quarter of an hour belongs in the daemon's own history rather than in a
    command somebody is waiting on, and an interval of at most half the run so
    that two samples always exist to compare. None of the three is a
    measurement and none of them decides anything about a fan.
    """
    device = (chip.Chip(helper) if helper else chip.Chip()) if chip else None
    if device is None:
        raise ProbeError('no-helper', 'the hardware layer (chip.py) could not be imported.')

    seconds = max(10.0, min(900.0, float(seconds)))
    interval = max(1.0, min(60.0, float(interval)))
    interval = min(interval, seconds / 2.0)

    series = {}
    samples = 0
    started = time.monotonic()
    while True:
        try:
            status = device.status()
        except chip.ChipError as failure:
            # The file's own vocabulary, not the helper's: the header promises a
            # page and a log say the same word for the same condition, and
            # chip.py's slugs ('chip', 'busy', 'readback') are not words this
            # file defines (D, chip.py CODES).
            raise ProbeError(_slug_for(failure.code), failure.message)
        samples += 1
        for item in status.get('channels') or []:
            name = name_for(item)
            record = series.setdefault(name, {'rpm': [], 'output': [], 'duty': []})
            record['rpm'].append(item.get('rpm'))
            record['output'].append(item.get('output_pct'))
            record['duty'].append(item.get('duty_pct'))
        # Sleep BETWEEN samples and not after the last one, and never past the
        # window somebody asked for: the earlier loop slept once more than it
        # sampled, so a 120-second watch held somebody at a prompt for 125.
        elapsed = time.monotonic() - started
        if elapsed >= seconds:
            break
        time.sleep(min(interval, seconds - elapsed))

    return {
        'status': 'ok',
        'at': time.time(),
        'seconds': round(time.monotonic() - started, 1),
        'samples': samples,
        'wrote': 'nothing',
        'channels': [dict(_correlate(record), name=name)
                     for name, record in sorted(series.items())],
        'note': ('This is a passive observation. It concludes something only where the chip '
                 'moved a channel\'s output while it was watched; on an idle machine it '
                 'concludes nothing, which is not the same as a channel that does not '
                 'respond.'),
    }



def _correlate(record):
    """What one channel's samples support, and nothing further.

    The comparison is between two PAIRED samples - the one where the output was
    lowest and the one where it was highest - and not between the extremes of
    two independent series. That is the correction here, and it was measurable:
    output 50, 51, 52, 52 against a tachometer falling 4500, 3600, 3100, 3000
    was answered "the chip moved this channel's output by 2% and its tachometer
    moved 1500 rpm with it. This channel drives the fan this tachometer counts -
    measured" (V, 2026-09-22, against the code as it stood). The tachometer had
    moved 1500 rpm in the OPPOSITE direction, which is evidence against that
    channel driving that fan, and it was reported as evidence for it.

    Five outcomes, kept apart deliberately: 'followed' is evidence of response,
    'moved-against-the-output' and 'output-moved-tach-did-not' are evidence
    against, and the other two are the absence of evidence rather than evidence
    of absence.
    """
    pairs = [(output, rpm) for output, rpm in zip(record['output'], record['rpm'])
             if output is not None and rpm is not None]
    if len(pairs) < 2:
        return {'verdict': 'unreadable', 'output_span': None, 'rpm_span': None,
                'detail': 'not enough readable samples to say anything.'}

    outputs = [output for output, _ in pairs]
    speeds = [rpm for _, rpm in pairs]
    # First sample at each extreme, which is arbitrary between ties and stated
    # so rather than hidden.
    low = min(pairs, key=lambda pair: pair[0])
    high = max(pairs, key=lambda pair: pair[0])

    output_span = high[0] - low[0]
    rpm_span = max(speeds) - min(speeds)
    rpm_change = high[1] - low[1]
    summary = {'output_span': output_span, 'rpm_span': rpm_span,
               'rpm_change_across_the_move': rpm_change,
               'output_min': min(outputs), 'output_max': max(outputs),
               'rpm_min': min(speeds), 'rpm_max': max(speeds),
               'rpm_median': int(statistics.median(speeds))}

    if output_span < OUTPUT_MOVE_POINTS:
        summary.update({'verdict': 'nothing-moved',
                        'detail': ('the chip held this channel\'s output within %d%% for the '
                                   'whole observation, so nothing about response can be read '
                                   'from it.' % output_span)})
        return summary

    if max(speeds) < STALL_RPM:
        summary.update({'verdict': 'output-moved-tach-silent',
                        'detail': ('the chip moved this channel\'s output by %d%% and its '
                                   'tachometer stayed silent throughout. That is consistent '
                                   'with a header with no fan, a fan with no tach wire, and '
                                   'a fan that is not there - and it is not, by itself, '
                                   'evidence that a duty here does nothing.' % output_span)})
        return summary

    # Both rules, and they are the pair named at RESPONSE_RPM above.
    threshold = max(RESPONSE_RPM, int(RESPONSE_FRACTION * statistics.median(speeds)))

    if rpm_change >= threshold:
        summary.update({'verdict': 'followed',
                        'detail': ('between the sample where this channel\'s output was '
                                   'lowest and the one where it was highest, the output rose '
                                   '%d%% and the tachometer rose %d rpm with it. This channel '
                                   'drives the fan this tachometer counts - measured, on this '
                                   'machine, without writing anything.'
                                   % (output_span, rpm_change))})
        return summary

    if rpm_change <= -threshold:
        summary.update({'verdict': 'moved-against-the-output',
                        'detail': ('between the sample where this channel\'s output was '
                                   'lowest and the one where it was highest, the output rose '
                                   '%d%% and the tachometer FELL %d rpm. A fan does not speed '
                                   'up by being asked for less, so this is evidence against '
                                   'this channel driving the fan this tachometer counts - or '
                                   'against one of the two readings. Worth a response test '
                                   'with somebody in front of the machine.'
                                   % (output_span, -rpm_change))})
        return summary

    summary.update({'verdict': 'output-moved-tach-did-not',
                    'detail': ('between the sample where this channel\'s output was lowest '
                               'and the one where it was highest, the output rose %d%% and '
                               'the tachometer moved %d rpm. Either this channel does not '
                               'drive the fan this tachometer counts, or the fan was already '
                               'at the end of its range. Worth a response test with somebody '
                               'in front of the machine.%s'
                               % (output_span, rpm_change,
                                  '' if rpm_span < threshold else
                                  ' The tachometer did swing %d rpm across the whole '
                                  'observation, so something moved it - just not this.'
                                  % rpm_span))})
    return summary


# ---------------------------------------------------------------------------

def _print(document):
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')


def main(argv):
    verb = argv[1] if len(argv) > 1 else 'probe'
    try:
        if verb == 'probe':
            _print(probe())
        elif verb == 'watch':
            seconds = float(argv[2]) if len(argv) > 2 else 120.0
            interval = float(argv[3]) if len(argv) > 3 else 5.0
            _print(watch(seconds, interval))
        else:
            _print({'status': 'failed',
                    'detail': 'usage: probe.py [probe | watch [seconds] [interval]]'})
            return 2
    except ProbeError as refusal:
        _print({'status': 'failed', 'slug': refusal.slug, 'detail': refusal.message})
        return 1
    except Exception as failure:                # chip.ChipError and anything like it
        # A hardware refusal reaching this point is a bug in this file, and a
        # traceback is the one answer a page cannot use. It prints JSON on every
        # path, including the ones nobody thought of, and names the exception.
        if chip is not None and isinstance(failure, chip.ChipError):
            _print({'status': 'failed', 'slug': _slug_for(failure.code),
                    'detail': failure.message})
            return 1
        if isinstance(failure, ValueError):
            raise
        _print({'status': 'failed', 'slug': 'internal',
                'detail': '%s: %s' % (type(failure).__name__, failure)})
        return 1
    except ValueError as bad:
        _print({'status': 'failed', 'detail': 'that is not a number: %s' % bad})
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
