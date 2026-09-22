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

    The one way the rest of os-fanctl reaches the chip.

    Everything above this file - the daemon, the API, the page - asks in the
    language of fans: which channels are there, how fast is channel 2 turning,
    hold channel 1 at 40%, give channel 1 back. Nothing above this file can
    name a register, because fanctl-io does not accept one.

    One question above is not about fans and belongs here anyway: what
    temperatures the hardware monitor itself can see. `temps()` asks it. The
    chip has six temperature pins and a set of forwarded sources, FreeBSD
    exposes none of them - ncthwm(4) registers five sysctls and every one is a
    tachometer - so on this board the helper is the only way to read the only
    sensors the board has of its own. It is a read, it takes the same lock as
    every other command, and it writes nothing: an input the firmware switched
    off stays off, because turning one on is a configuration write to the chip
    that is cooling the machine.

    There are two paths in here and they are not interchangeable:

      * THE HELPER path (fanctl-io on /dev/io). It reads and it writes. It is
        the only way to set anything, it needs root, and it is x86 only.

      * THE SYSCTL path (dev.ncthwm.<n>.<NAME>). It reads tachometers and
        nothing else - no duty, no mode, no control, and only on the two chips
        ncthwm(4) has a table for (0xc562 NCT6779D and 0xd42a NCT6796D-E, D
        ncthwm.c:74-103). It exists so that the page still shows fan speeds on
        a machine where the helper cannot run at all.

    THEY MUST NOT BOTH BE USED AT ONCE, and that is not tidiness.
    ncthwm_query_fan_speed() takes no lock - there is no mutex in struct
    ncthwm_softc (D, ncthwm.c:52-57) - and it drives the same two I/O ports the
    helper drives: it writes the bank, writes an index, reads the data
    register. A sysctl read landing between the helper's index write and its
    data write puts the helper's byte in whichever register the kernel selected
    last. So this module refuses to read the sysctls while the helper is
    usable, by default, and says why. The kernel only touches that window when
    somebody reads those sysctls, so a firewall where nothing reads them has no
    race at all - which makes "do not read them" a real fix rather than a wish.

    WHAT WAS MEASURED, AND WHAT WAS NOT

    V, reference appliance (ex-Sophos XG 330 rev 2, OPNsense 26.7.4_1 /
    FreeBSD 15.1, Nuvoton NCT6779D): the window is at 0xa35/0xa36; the fan
    registers hold RPM directly with no divisor - `dev.ncthwm.0.SYSFAN`
    returned 941 = 0x03AD while an inb-only reader saw index 0xc1 and data
    0xad; five tachometer channels exist and exactly one of them turns, the
    other four reading 0 for three minutes while core temperature moved
    between 30 and 42 C.

    Not settled, and this module does not pretend otherwise: whether that one
    fan is under a fixed duty or under a SmartFan curve whose first segment is
    flat below 40 C. A 19 rpm span across a 12 C swing is consistent with
    both. `status()` reports the mode register and lets the caller read it;
    it does not conclude.

    The channel numbers here are the chip's, from 1. The names ncthwm(4)
    prints - SYSFAN, CPUFAN, AUXFAN0 - are that driver's own labels for the
    same five registers and say nothing about which fan in the chassis is on
    which header; they are carried as aliases and never as facts.
"""

import json
import os
import subprocess

HELPER = '/usr/local/opnsense/scripts/fanctl/fanctl-io'

# fanctl-io never blocks: it takes a non-blocking lock, does a handful of port
# accesses and exits. The timeout is here for the case the helper is not the
# program we think it is.
TIMEOUT = 10

# The chip's channel order, as ncthwm(4) names it. Index 0 is channel 1:
# ncthwm's first entry reads bank 4 offsets 0xc0/0xc1, which is register 0x4c0,
# which is NCT6779_REG_FAN[0] - the same channel the helper calls 1
# (D, ncthwm.c:76-80 against nct6775-core.c:366-367).
NCTHWM_NAMES = ('SYSFAN', 'CPUFAN', 'AUXFAN0', 'AUXFAN1', 'AUXFAN2')

# The widest chip the helper knows has seven channels (NCT6796D, NCT6797D,
# NCT6798D, NCT6799D). Only release_all() uses this, and only when the chip
# could not be identified at all: it is better to ask the helper about a
# channel that does not exist and be refused than to leave one held.
MAX_CHANNELS = 7

# fanctl-io's exit codes. Each one gets its own sentence because "it did not
# work" is not something a page can put in front of anybody.
CODES = {
    0: ('ok', 'the command succeeded'),
    1: ('usage', 'the helper was called wrongly - this is a bug in this plugin'),
    2: ('no-device', '/dev/superio0 or /dev/io is not present on this machine'),
    3: ('no-permission', 'the helper must run as root, with kern.securelevel below 1'),
    4: ('chip', 'this chip is not one the helper will drive'),
    5: ('channel', 'there is no such fan channel on this chip'),
    6: ('duty-range', 'a duty must be 1..100 - zero is not a duty'),
    7: ('floor', 'the duty is below the floor, and the helper refused it'),
    8: ('readback', 'the chip did not read back what was written to it'),
    9: ('busy', 'another fanctl-io holds the hardware-monitor window'),
    10: ('internal', 'the helper failed for a reason it could not classify'),
}


class ChipError(Exception):
    """Anything fanctl-io refused or could not do.

    It carries the helper's own exit code and its own words. Rewording a
    hardware refusal loses the only detail that lets somebody check it.
    """

    def __init__(self, code, message, command=None):
        self.code = code
        self.message = message
        self.command = command
        self.slug, self.explanation = CODES.get(code, ('unknown', 'unrecognised exit code'))
        super().__init__('%s (%s): %s' % (self.slug, code, message))


class Unavailable(ChipError):
    """The helper cannot reach the hardware here: no device, or no permission."""


class Refused(ChipError):
    """The helper reached the hardware and said no: wrong chip, wrong channel,
    wrong duty, below the floor. A refusal is an answer, not a failure."""


class Busy(ChipError):
    """Somebody else holds the window. Come back; do not force it."""


class ReadbackFailed(ChipError):
    """The chip did not hold what was written, or the window was disturbed
    mid-access. Nothing is assumed about what the fan is doing now."""


def _classify(code, message, command):
    if code in (2, 3):
        return Unavailable(code, message, command)
    if code in (4, 5, 6, 7):
        return Refused(code, message, command)
    if code == 9:
        return Busy(code, message, command)
    if code == 8:
        return ReadbackFailed(code, message, command)
    return ChipError(code, message, command)


class Chip(object):
    """The helper-backed path: reads and writes, needs root.

    There is deliberately no floor argument anywhere in this class, and none
    will be added. The floor lives in the helper and in the helper's own file;
    a bug in this file must not be able to stop a fan.
    """

    def __init__(self, helper=HELPER):
        self.helper = helper

    # -- plumbing ---------------------------------------------------------

    def installed(self):
        return os.access(self.helper, os.X_OK)

    def _run(self, *args):
        argv = [self.helper] + [str(a) for a in args]
        if not self.installed():
            raise Unavailable(2, '%s is not installed or not executable' % self.helper,
                              ' '.join(argv[1:]))
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            # The helper does not loop or sleep, so this is not slowness - it
            # is the wrong program at that path.
            raise ChipError(10, '%s did not exit within %ds' % (self.helper, TIMEOUT),
                            ' '.join(argv[1:]))
        except OSError as exc:
            raise Unavailable(2, '%s: %s' % (self.helper, exc), ' '.join(argv[1:]))

        try:
            answer = json.loads(proc.stdout or '{}')
        except ValueError:
            # The helper prints JSON on every path including its failures, so
            # output that is not JSON means the answer cannot be trusted at
            # all - which is a different thing from a command that failed.
            raise ChipError(10, '%s printed something that is not JSON: %r' % (
                self.helper, (proc.stdout or proc.stderr or '')[:200]),
                ' '.join(argv[1:]))

        if proc.returncode != 0 or not answer.get('ok'):
            code = answer.get('code', proc.returncode)
            raise _classify(int(code), answer.get('error', 'no message'),
                            ' '.join(argv[1:]))
        return answer

    # -- reading ----------------------------------------------------------

    def identify(self):
        """Which chip is here, how many channels it has, where its window is."""
        return self._run('identify')['chip']

    def channel_count(self):
        return int(self.identify()['channels'])

    def channels(self):
        """The channel numbers this chip has, as the chip numbers them."""
        return list(range(1, self.channel_count() + 1))

    def tach(self, channel):
        """One channel's tachometer, in RPM.

        Zero is returned as zero and nothing is inferred from it: a fan that
        has stopped and a header with no tachometer wire to this chip read the
        same, and this plugin cannot tell them apart (V: four of five channels
        read 0 on the reference appliance, which certainly has more than one
        fan).
        """
        return int(self._run('tach', int(channel))['rpm'])

    def status(self):
        """Every channel at once: speed, duty, mode, floor, saved original.

        One helper run for the whole chip, because every extra run is another
        pass over a window that is shared with the kernel.
        """
        return self._run('status')

    def channel_status(self, channel):
        for entry in self.status()['channels']:
            if int(entry['channel']) == int(channel):
                return entry
        raise Refused(5, 'channel %s is not on this chip' % channel, 'status')

    def alias(self, channel):
        """What ncthwm(4) would call this channel - a label, not a fact.

        Returned so a page can show the same word the sysctl shows and nobody
        has to guess whether they are looking at the same fan. It says nothing
        about which fan in the chassis it is.
        """
        index = int(channel) - 1
        if 0 <= index < len(NCTHWM_NAMES):
            return NCTHWM_NAMES[index]
        return None

    def temps(self):
        """Every temperature source the chip has, as the chip describes them.

        One helper run, read-only, under the same non-blocking lock every other
        command takes. The answer is the helper's own, whole:

            {'ok': True, 'code': 0, 'command': 'temp',
             'chip': {...}, 'map': 'NCT6779D',
             'vbat_raw': '0x41', 'diode_raw': '0x00', 'diode_mask': '0x02',
             'inputs': [{'index': 4, 'reg': '0x075', 'role': 'fan-control',
                         'fan_channel': 2, 'src': 1, 'src_name': 'SYSTIN',
                         'present': True, 'pin': True, 'type': 'thermistor',
                         'raw': '0x2f00', 'stable': True, 'celsius': 47.0,
                         'attached': 'unknown', 'selected_by': [2, 3, 4, 5]},
                        ...],
             'count': 7, 'inputs_on_chip': 7, 'note': '...'}

        The rows are passed up whole and nothing here reshapes them. The
        register a row came from, the source byte the chip reported and the raw
        value are the evidence; a method that boiled them down to a number
        would be throwing away the only part that lets somebody check it.
        sensors.py turns these into sensors - this carries them.

        EXIT CODES CLASSIFY EXACTLY AS tach() AND status() DO, because all
        three go through _run() and _run() is the only place in this file that
        looks at one. That is the whole of the promise, and it matters here
        more than it does for a tachometer: a chip that is busy and a chip that
        has no temperature map must not arrive as the same thing.

            4  Refused        this chip's temperatures are not mapped. Its fans
                              may still be driven; the helper refuses the
                              temperature table rather than naming the wrong
                              sensor, because the label tables differ across
                              this family.
            9  Busy           something else holds the window. Come back.
            8  ReadbackFailed a configuration register disagreed with itself
                              across two reads - the window was disturbed.
            2, 3 Unavailable  no device, or not root.

        A row that is not a temperature arrives AS that and never as a number:
        `celsius` is None and `unreadable` says which of unassigned,
        disabled-in-chip, source-silent, unstable or implausible it was.

        There is no method here that turns an input on, and none will be added.
        Linux's driver clears bit 0 of a disabled input's configuration
        register to enable it; that is a write to the chip that is at this
        moment the only thing controlling the fan cooling this machine, for the
        sake of a nicer number.
        """
        return self._run('temp')

    # -- writing ----------------------------------------------------------

    def set_duty(self, channel, percent):
        """Hold one channel at a fixed duty, 1..100.

        The helper checks the floor itself before it opens anything, so a
        percentage that is too low costs a process start and touches no
        hardware. It raises Refused with code 7 and names the floor and where
        the floor came from, which is what belongs on the page - not a
        rounded-up duty the user did not ask for. This method does not clamp:
        silently raising somebody's 20% to 35% teaches them the wrong thing
        about what this plugin is doing to their fans.
        """
        return self._run('duty', int(channel), int(percent))

    def release(self, channel):
        """Give one channel back to the hardware.

        Restores the mode and duty the channel had before this plugin first
        touched it, from the record the helper wrote at that moment. Where
        there is no such record the helper falls back to the chip's own "PWM
        control off" - full output, no thermal control - because after a
        failure nobody understood, a fan at full speed is the only state worth
        defending.
        """
        return self._run('release', int(channel))

    def release_all(self):
        """Hand every channel back, and keep going through failures.

        This is the panic path, so it does not stop at the first refusal and it
        does not need identify() to have worked: if the chip could not be
        identified, every channel will refuse for the same reason and the
        caller gets that reason seven times, which is still better than a
        channel left held because the loop gave up early.
        """
        try:
            channels = self.channels()
        except ChipError:
            channels = list(range(1, MAX_CHANNELS + 1))

        results = {}
        for channel in channels:
            try:
                results[channel] = {'released': True, 'detail': self.release(channel)}
            except ChipError as exc:
                results[channel] = {'released': False, 'code': exc.code,
                                    'slug': exc.slug, 'error': exc.message}
        return results


class SysctlFans(object):
    """Tachometers only, with no helper and no privilege beyond reading sysctls.

    This is the path that still works on a machine where /dev/io is gone, where
    the helper was never built, or where somebody has decided not to let a
    userland program write to I/O ports. It reads fan speeds and it can set
    nothing - and it says so, rather than presenting a read-only view as if the
    controls were merely disabled.

    It is also the path that must not run while the helper does: these sysctls
    walk the same two ports with no lock (D, ncthwm.c:52-57, 138-152). See the
    module docstring.
    """

    def __init__(self, unit=0):
        self.unit = unit

    def available(self):
        return self.read_raw() != {}

    def _sysctl(self, name):
        try:
            out = subprocess.run(['/sbin/sysctl', '-n', name], capture_output=True,
                                 text=True, timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        try:
            return int(out.stdout.strip())
        except ValueError:
            return None

    def read_raw(self):
        """{channel: rpm} for every ncthwm channel that answers."""
        speeds = {}
        for index, name in enumerate(NCTHWM_NAMES):
            value = self._sysctl('dev.ncthwm.%d.%s' % (self.unit, name))
            if value is not None:
                speeds[index + 1] = value
        return speeds

    def read(self):
        """The same, shaped like Chip.status() so a page can render one view.

        Every field the sysctls cannot know is None rather than a default. A
        zero duty here would read as "this fan is off", and the truth is "this
        path cannot see the duty at all".
        """
        return {
            'source': 'sysctl',
            'writable': False,
            'why': 'dev.ncthwm.* is read-only by construction: every sysctl the '
                   'driver registers carries CTLFLAG_RD and there is no ioctl '
                   '(ncthwm.c:218-224). Fan speeds can be read here; nothing can '
                   'be set.',
            'channels': [
                {'channel': channel, 'rpm': rpm, 'alias': NCTHWM_NAMES[channel - 1],
                 'mode': None, 'mode_name': None, 'duty_raw': None, 'duty_pct': None,
                 'output_raw': None, 'output_pct': None, 'floor_pct': None}
                for channel, rpm in sorted(self.read_raw().items())
            ],
            'note': 'A tachometer reading 0 may be a stopped fan or a fan with no '
                    'tachometer wired to this chip. This path cannot tell those apart.',
        }


def read_path(helper=HELPER, allow_sysctl_beside_helper=False):
    """Pick the reading path, and say which one and why.

    The helper is preferred when it works, because it is the only path that can
    also report duty and mode, and because using it means nothing else is
    walking the window. The sysctl path is the fallback, not an alternative.

    allow_sysctl_beside_helper exists so that the one person who really wants
    both has to type it, and so that the guard is visible in the code rather
    than living in a comment. It is a race, not a preference: see the module
    docstring.
    """
    chip = Chip(helper)
    try:
        status = chip.status()
        status['source'] = 'helper'
        status['writable'] = True
        for entry in status.get('channels', []):
            entry['alias'] = chip.alias(entry.get('channel'))
        return status
    except ChipError as exc:
        # The decision comes before the probe, and the order is the whole
        # point: Busy or a readback failure means the helper is live on this
        # machine and something is walking that window right now. Even asking
        # "are the sysctls there?" would walk it too, so nothing is read at all
        # in that case - not even to find out whether the fallback exists.
        if not allow_sysctl_beside_helper and not isinstance(exc, (Unavailable, Refused)):
            raise
        fallback = SysctlFans()
        if not fallback.available():
            raise
        answer = fallback.read()
        answer['helper_unavailable'] = {'code': exc.code, 'slug': exc.slug,
                                        'error': exc.message}
        return answer


if __name__ == '__main__':
    # Deliberately read-only. There is no command-line way to set a duty from
    # this module: a fan is set by the daemon, under a configuration somebody
    # saved on a page, or by fanctl-io itself with its own refusals in front.
    import sys

    try:
        print(json.dumps(read_path(), indent=2, sort_keys=True))
    except ChipError as error:
        print(json.dumps({'ok': False, 'code': error.code, 'slug': error.slug,
                          'error': error.message,
                          'means': error.explanation}, indent=2, sort_keys=True))
        sys.exit(error.code)
