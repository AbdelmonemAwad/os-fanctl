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

    THE RESPONSE TEST: does a duty written to this channel reach a fan?

    It is the only question about fan control that cannot be answered by
    reading, and the only way to answer it is to change a duty and watch a
    tachometer. This file does that, and it is built so that the worst outcome
    of every step - including being killed outright in the middle - is a fan
    running FASTER than it was running before. Nothing here can leave a fan
    slower than it found it.

    THAT SENTENCE USED TO BE FALSE, and the correction is the substance of this
    version. The duty was computed in plan(), from a status read, and written
    some seconds later: BEFORE_SAMPLES * SAMPLE_GAP of tachometer sampling
    stands between the two, measured end to end at 3.015 s on the reference
    appliance (V, 2026-09-22). A channel under an automatic control law moves
    in those seconds, because moving is what an automatic control law is for.
    The duty written from the stale figure could then be below the duty the
    chip had climbed to, and the write would SLOW the fan.

    Measured against a fake chip whose SmartFan IV channel ramps the way a real
    curve ramps (V, 2026-09-22, /root/fanwork2/ramptest.py):

        a curve rising 10 points/s    plan read 20%, wrote 50%, the chip was
                                      commanding 53%  - a 3 point drop
        a curve stepping 25 points/s  plan read 20%, wrote 51%, the chip was
                                      commanding 100% - a 49 POINT DROP

    The second is the ordinary shape of a curve reacting to a temperature step.
    It held the fan 49 points below what the chip was asking for, for
    SPIN_SECONDS plus the after-samples - about nine seconds - before the
    release. On a firewall whose curve had just called for 100%, that is the
    hot firewall this plugin exists to prevent.

    SAFE BY CONSTRUCTION, and each of these is a line of code rather than an
    intention:

    1. IT ONLY EVER MOVES UPWARD, AND UPWARD IS MEASURED AT THE LAST MOMENT.
       The target is the channel's present output plus a margin, held to at
       least the helper's floor and at most 100. Downward is where a fan stalls
       and nobody knows where that is on an unmeasured machine - that is what
       the floor test is for, and the floor test is a different, later, more
       dangerous thing that must not happen until this one has said there is a
       fan here at all.

       "Present output" means present at the instant of the write, not present
       when the page drew the plan. plan()'s figure is a forecast and is
       labelled one. run() reads the channel again, inside the lock, with the
       deadman already armed and nothing left to do but write, and computes the
       duty it actually writes from THAT reading - a number milliseconds old
       instead of seconds old.

       And if the two readings disagree, it does not write at all. It refuses
       with 'moving' and quotes both figures. Two separate things are wrong
       with testing a channel that is moving on its own: the duty computed from
       the older figure may land below where the chip has got to, and a
       tachometer that rises after the write may be rising because of the curve
       rather than because of the write. The second is how a test tells
       somebody a channel drives a fan when it has measured nothing of the
       kind - V, 2026-09-22: against a 10 points/s ramp the uncorrected file
       returned `responded`, "2307 rpm at 20% became 3050 rpm at 50%", for a
       rise that was entirely the fake chip's own curve.

       The takeover itself is upward-safe for the same reason the helper's own
       write order is: switching a channel to manual copies nothing and
       disturbs nothing, because at that instant the duty register holds what
       the CHIP last wrote - the speed the fan is already turning at. The duty
       write that follows is the only thing that moves the fan (D, the
       corrected fanctl-io.c cmd_duty: the mode write is conditional on
       cur_mode != MODE_MANUAL and comes first, the duty write second; the
       opposite order was measured refusing with `wrote 0xff ... read back
       0x32`, the chip's own 20%).

       Note what the helper does NOT do. cmd_duty reads the channel's present
       duty, records it, and never compares it with the duty being written (D,
       fanctl-io.c cmd_duty: cur_duty feeds state_save_once and the printed
       previous_duty_raw and nothing else). The helper will write 30% onto a
       channel the chip is holding at 100% without a word. Nothing below this
       file enforces "upward". This file is where it is enforced, and that is
       why the freshness of the reading it enforces it against is not a detail.

       What remains unguarded is the gap between that last reading and the
       helper's own write - one process start, about 2 ms (V, 2026-09-22) -
       which is the same residual the helper accepts between its own two
       writes and is not reducible from here.

    2. IT REFUSES TO START WITHOUT SOMETHING ELSE ALREADY ABLE TO UNDO IT.
       Before the first write, a DEADMAN is forked: a detached process, in its
       own session, that ignores every catchable signal, sleeps to a deadline
       and then runs `fanctl-io release` on this channel whether or not this
       program still exists. The write does not happen unless that process has
       been confirmed alive. If this program is SIGKILLed - the one signal
       nothing can catch - the deadman still fires. If the deadman is killed
       too, the helper's own record of the channel's original is still on disk
       and `fanctl.py restore`, the rc script's stop path and package removal
       all end in a release.

       Running release on a channel that has already been released writes
       NOTHING: the helper forgets its record when it restores, and a release
       with no record and a non-manual mode prints "nothing-to-restore" and
       touches no register (D, fanctl-io.c cmd_release, state_forget). So the
       deadman firing after a clean test is free.

    3. IT WILL NOT TOUCH A CHANNEL SOMETHING ELSE HAS TAKEN OVER.
       Measured on the reference appliance, 2026-09-22 (V): channel 1 was held
       at 100% by hand while the helper's saved record of that channel said
       SmartFan IV at raw 50 - 20%. On such a channel every restore path in
       this plugin, including this file's, would hand the fan back to 20% on a
       CPU that needs the 100%. So it refuses, and quotes both numbers. That
       refusal is why this test could not be run on the reference machine at
       the time it was written: its one fan with a tachometer was exactly in
       that state.

    4. IT IS NEVER AUTOMATIC.
       Nothing in this plugin calls run(). The daemon does not. The probe does
       not. The settings page does not. It is a button somebody presses, with a
       confirmation, and it refuses outright while the daemon is running.

    WHAT IT COSTS: a burst of noise, for about twenty seconds. On a fan that
    responds, that is the point - the noise IS the measurement. On the
    reference appliance's fan, 100% is 5 844 rpm against 1 332 at the 20% the
    board's own curve was asking for (V, 2026-09-22), which is audible across a
    room.

    WHAT IT CAN AND CANNOT CONCLUDE

      responded     the tachometer followed the duty up, on a channel that was
                    NOT moving on its own beforehand. This channel drives the
                    fan this tachometer counts. A fact - and a fact only
                    because the 'moving' refusal has already ruled out the one
                    thing that could fake it.
      appeared      the tachometer was silent and started reading, by more than
                    the same threshold 'responded' has to clear. Same fact,
                    more of it: the channel has a fan AND a tach wire, and at
                    the old duty that fan was not turning. The threshold is
                    there because without one a tachometer going from 199 to
                    200 rpm was reported as this fact (V, 2026-09-22:
                    _verdict(199, 4, 200, 4, ...) returned 'appeared'), on a
                    machine whose measured tachometer noise is 39 rpm peak to
                    peak.
      no-change     the duty moved and the tachometer did not. This is NOT
                    "there is no fan". It is consistent with: a fan with no
                    tachometer wire, a header with no fan, a fan already at the
                    top of its range, and a channel whose output goes somewhere
                    else. The channel is not driven on this result, and the
                    reason given says all four.
      inverted      the tachometer went DOWN when the duty went up. Something
                    is not what it seems - a voltage-controlled channel, a
                    mis-mapped tachometer, another writer on the chip. The
                    remaining samples are abandoned on the first reading that
                    shows it and the channel is handed back then, rather than
                    at the end of the window. Nothing is concluded. (This
                    docstring used to promise the test "stops at once" and the
                    code did not: it sampled the whole window and only then
                    labelled the result - V, 2026-09-22, a 25 points/s ramp
                    produced `inverted` after the full 12.7 s run.)
      inconclusive  not enough readable samples, or the test was interrupted.

    And one outcome that is not a verdict, because nothing was written:

      moving        (a refusal, slug 'moving') the channel's output changed
                    under its own control law between the start and the end of
                    the before-samples. Neither the duty this test would write
                    nor the rise it would measure would mean what it said, so
                    it writes nothing and quotes both figures. Test the channel
                    when the machine is thermally quiet, or take it over first
                    and test it from a duty that is standing still.
"""

import errno
import fcntl
import json
import os
import signal
import statistics
import subprocess
import sys
import time

import probe

try:
    import chip
except Exception as _failure:                   # ImportError and anything else
    chip = None
    _CHIP_IMPORT_ERROR = str(_failure)
else:
    _CHIP_IMPORT_ERROR = ''

RUN_DIR = '/var/run/fanctl'
STATE_DIR = '/var/db/fanctl'
MEASURED = os.path.join(STATE_DIR, 'measured.json')
DEADMAN_LOG = os.path.join(STATE_DIR, 'deadman.json')

# The same lock fanctl.py takes, and deliberately NOT the helper's own
# (/var/run/fanctl-io.lock). The helper locks its window for the handful of
# microseconds it is in it; this one is held for the whole test, so that the
# daemon and the page queue behind it rather than interleaving writes with it.
# Holding the helper's file here would make every helper call in the test fail
# as busy - against itself.
LOCK = os.path.join(RUN_DIR, 'chip.lock')
PID = os.path.join(RUN_DIR, 'fanctl.pid')

# THE MEASUREMENT EVERY THRESHOLD BELOW IS SCALED AGAINST (V, 2026-09-22,
# reference appliance, channel 1, a Noctua NF-A4x20 held at a fixed 100%).
# Twenty tachometer reads half a second apart:
#     5133 5133 5133 5133 5133 5113 5113 5133 5133 5133
#     5133 5113 5113 5152 5113 5113 5152 5133 5133 5133
# min 5113, max 5152, spread 39 rpm, stdev 11.8 rpm, median 5133 - a relative
# spread of 0.008. That is the noise floor, and it is the whole of the evidence
# for the numbers below. The same fan spans 1332 rpm at 20% to 5844 rpm at 100%
# (V), about 56 rpm per point of duty, which is what makes a step size mean
# something in rpm.

# How far up the test moves, in points of duty, by whichever of the two rules
# gives the larger target: +30 points, or half as fast again. 30 points is
# about 1700 rpm on the reference fan (V, at 56 rpm/point), 44 times the
# measured 39 rpm spread. It is deliberately far more than is needed to see a
# response: overshooting costs noise, undershooting costs an unreadable answer
# on a fan nobody has characterised.
STEP_UP_POINTS = 30
STEP_UP_FACTOR = 1.5

# Below this, the move is too small to conclude anything from - which is how a
# test that "found no response" on a channel already at 95% would be a lie.
# 15 points is about 850 rpm on the reference fan (V), 22 times the spread.
MIN_MOVE_POINTS = 15

# What counts as the tachometer having followed. Both must be satisfied: a
# relative rule alone calls noise a response on a slow fan, and an absolute
# rule alone misses a real response on a fast one.
#
# 120 rpm is 3 times the measured 39 rpm peak-to-peak spread and 10 times its
# 11.8 rpm stdev (V). 0.15 is 19 times the measured relative spread of 0.008
# (V). Both are far above the noise and both err the same way: they make this
# test slower to say "responded" and never quicker. A threshold set too high
# costs a 'no-change' on a real fan, and 'no-change' is a result this file is
# careful to say means nothing. A threshold set too low costs a claim that a
# channel drives a fan when it does not, which is a claim somebody would then
# build a cooling policy on. That asymmetry is the reason for the margins.
RESPONSE_RPM = 120
RESPONSE_FRACTION = 0.15

# Four samples give a median that survives one unreadable read and one outlier,
# and three gaps - 3.015 s measured end to end on the reference appliance (V,
# 2026-09-22), the helper's status and tach calls costing about 2 ms each. That
# 3 s is also exactly the window the 'moving' check has to close.
BEFORE_SAMPLES = 4
AFTER_SAMPLES = 4
SAMPLE_GAP = 1.0

# The deadman's margin on top of everything the test can legitimately take. It
# is generous on purpose: the cost of a deadman that fires late is a few extra
# seconds of noise, and the cost of one that fires early is a release in the
# middle of a measurement. Measured slack on a default-length test: it ran in
# 12.5 s against a planned 14.0 s and a deadman of 44.0 s (V, 2026-09-22), so
# the deadman had 31.5 s in hand.
DEADMAN_MARGIN = 30.0

# How far a channel's own output may drift between the start and the end of the
# before-samples before this test refuses to write. One point, because
# raw-to-percent rounding can move a steady channel by one and nothing else
# can: anything larger is the channel's control law doing its job, and a
# channel whose control law is working is a channel this test cannot measure
# honestly. See rule 1.
DRIFT_POINTS = 1

_stopping = False


class Refused(Exception):
    """A reason not to run. Carries a slug so the page and the log agree."""

    def __init__(self, slug, message):
        self.slug = slug
        self.message = message
        super().__init__(message)


def _on_term(signum, _frame):
    global _stopping
    _stopping = True


def _read_json(path, default=None):
    try:
        with open(path, 'r') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write_json(path, document, mode=0o640):
    """Written whole, through a temporary file, or not at all.

    A half-written measured.json is a floor nobody measured, which is the one
    file in this plugin where a partial write has a fan behind it.
    """
    temporary = '%s.%d.tmp' % (path, os.getpid())
    with open(temporary, 'w') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.rename(temporary, path)


def daemon_running():
    try:
        with open(PID, 'r') as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    if pid <= 1:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


class OurLock(object):
    """Non-blocking, and a failure to take it is a refusal rather than a wait.

    Whoever holds it is talking to the chip. Waiting behind them would mean
    this test starting at a moment nobody chose, which for something that makes
    a fan loud is the wrong behaviour.
    """

    def __init__(self, path=None):
        # The module global is read HERE and not baked into a default argument.
        # Python evaluates a default once, at import, which made this class
        # unredirectable: a test harness that pointed response.LOCK at a
        # scratch directory still took the real one, and did it silently. Found
        # by noticing an empty /var/run/fanctl/chip.lock on a machine where the
        # test was supposed to have touched nothing outside its own scratch
        # directory (V, 2026-09-22). A path that cannot be redirected is a path
        # that cannot be tested.
        self.path = path or LOCK
        self.handle = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.handle = open(self.path, 'a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as failure:
            self.handle.close()
            self.handle = None
            if failure.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise Refused('busy', 'something else in this plugin is talking to the chip '
                                      'right now. Try again in a moment.')
            raise Refused('lock', '%s: %s' % (self.path, failure))
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


# ---------------------------------------------------------------------------
# The deadman
# ---------------------------------------------------------------------------

class Deadman(object):
    """A process whose only job is to undo this test, and which outlives it.

    It is forked BEFORE the first write and confirmed alive before the write is
    allowed to happen, which is the whole of the safety argument: there is no
    instant at which a duty has been written and nothing exists that will take
    it off.

    It is in its own session (setsid), so a signal sent to this program's
    process group does not reach it; it ignores every catchable signal, so a
    tidy-up loop that kills "everything fanctl" does not disarm the one thing
    keeping the fan honest; and it writes what it did into
    /var/db/fanctl/deadman.json so that a fan which changed speed at 03:00 has
    an explanation on disk.

    The one signal it cannot survive is SIGKILL, and behind that there is still
    the helper's own record of the channel's original, which `fanctl.py
    restore`, the rc script's stop and the package's pre-deinstall all act on.

    IT ALSO INHERITS THIS PROGRAM'S LOCK, which is deliberate and is written
    down here so that nobody tidies it away. The deadman is forked inside
    OurLock, so it holds a second reference to the same open file description.
    On a clean run this program's LOCK_UN releases it for both and nothing is
    left held. On a SIGKILL nobody unlocks, and the orphaned deadman keeps
    chip.lock held until it fires and exits - measured (V, 2026-09-22): a
    separate process was refused the lock with errno 35 immediately after the
    parent was killed, and took it once the deadman had fired. That is the
    behaviour wanted. While an unfired deadman is pending, the daemon and the
    page queue behind it instead of writing a channel whose original is about
    to be restored over the top of them. Closing the inherited descriptor in
    the child would "fix" a leak that is a safety property.
    """

    def __init__(self, helper, channel, seconds):
        self.helper = helper
        self.channel = int(channel)
        self.seconds = float(seconds)
        self.pid = None
        self.path = os.path.join(RUN_DIR, 'deadman.%d.pid' % self.channel)

    def arm(self):
        os.makedirs(RUN_DIR, exist_ok=True)
        # Flushed before the fork: anything sitting in this process's buffers
        # would otherwise be written twice, once by each half.
        sys.stdout.flush()
        sys.stderr.flush()

        pid = os.fork()
        if pid == 0:
            self._be_the_deadman()                      # never returns
        self.pid = pid
        try:
            with open(self.path, 'w') as handle:
                handle.write('%d' % pid)
        except OSError:
            # Not fatal: the file is for a person reading the machine later.
            # The process is the mechanism, and it is already running.
            pass
        return pid

    def _be_the_deadman(self):
        """The child. Ends in os._exit, never in a return or an exception."""
        try:
            os.setsid()
            for received in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM,
                             signal.SIGQUIT, signal.SIGALRM):
                signal.signal(received, signal.SIG_IGN)
            devnull = os.open(os.devnull, os.O_RDWR)
            for stream in (0, 1, 2):
                os.dup2(devnull, stream)

            deadline = time.monotonic() + self.seconds
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                time.sleep(min(1.0, left))

            outcome = []
            for attempt in range(3):
                try:
                    done = subprocess.run([self.helper, 'release', str(self.channel)],
                                          capture_output=True, text=True, timeout=15)
                    outcome.append({'attempt': attempt + 1, 'exit': done.returncode,
                                    'said': (done.stdout or done.stderr or '').strip()[:400]})
                    if done.returncode == 0:
                        break
                except Exception as failure:            # noqa: BLE001 - last line of defence
                    outcome.append({'attempt': attempt + 1, 'error': str(failure)})
                time.sleep(2.0)

            try:
                _write_json(DEADMAN_LOG, {
                    'at': time.time(), 'channel': self.channel, 'fired': True,
                    'after_seconds': self.seconds, 'attempts': outcome,
                    'detail': ('the response test on channel %d did not disarm its deadman, '
                               'so the deadman handed the channel back. Either the test was '
                               'killed or it took longer than it was allowed.' % self.channel),
                })
            except Exception:                           # noqa: BLE001
                pass
        finally:
            # The child removes its own pid file, because it is the only one
            # that knows it has finished. A test that was killed leaves nobody
            # else to do it, and a pid file outliving its process would say a
            # deadman is armed when none is (V: one was left behind by the
            # SIGKILL case before this line existed, 2026-09-22).
            try:
                os.unlink(self.path)
            except OSError:
                pass
            # Not sys.exit: this is a forked child of a program with open files
            # and finally blocks that belong to the parent, and running any of
            # them here would be running them twice.
            os._exit(0)

    def alive(self):
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def disarm(self):
        """Kill it, reap it, and say whether it had already fired.

        SIGKILL because it ignores everything else - on purpose. Reaped here so
        that a long-running caller does not accumulate zombies; a caller that
        dies instead leaves the child to init, which is the case this whole
        class exists for.
        """
        fired = False
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                fired = True
            except PermissionError:
                pass
            try:
                os.waitpid(self.pid, 0)
            except (ChildProcessError, OSError):
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return fired


# ---------------------------------------------------------------------------
# The plan: everything decided, and every refusal earned, before any write
# ---------------------------------------------------------------------------

def _device(helper=None):
    if chip is None:
        raise Refused('no-helper', 'the hardware layer (chip.py) could not be imported: %s'
                      % _CHIP_IMPORT_ERROR)
    return chip.Chip(helper) if helper else chip.Chip()


def _resolve(device, channel):
    """A channel named or numbered, as (number, name), or a refusal."""
    try:
        status = device.status()
    except chip.ChipError as failure:
        slug = {2: 'no-device', 3: 'no-permission', 4: 'unknown-chip',
                9: 'busy'}.get(failure.code, 'chip-unreadable')
        raise Refused(slug, failure.message)

    wanted = str(channel).strip().upper()
    for entry in status.get('channels') or []:
        name = probe.name_for(entry)
        if wanted == name.upper() or wanted == str(entry.get('channel')):
            return entry, name, status
    have = ', '.join(probe.name_for(entry) for entry in status.get('channels') or [])
    raise Refused('no-channel', '%s is not a channel on this chip. It has: %s.'
                  % (channel, have or 'none'))


def plan(channel, helper=None, seconds=None):
    """What the test would do, and whether it may. READS ONLY.

    Called on its own by the page, so that somebody can see the exact duty, the
    exact length and the exact noise before deciding - and called again by
    run(), which will not write anything unless the plan it just made says ok.
    """
    device = _device(helper)
    entry, name, status = _resolve(device, channel)

    view = probe.channel_view(entry)
    floor = entry.get('floor_pct')
    floor = int(floor) if floor is not None else 0
    from_pct = entry.get('output_pct')
    if from_pct is None:
        from_pct = entry.get('duty_pct')

    refusals = []

    running = daemon_running()
    if running:
        refusals.append(('daemon-running',
                         'the fan control service is running (pid %d) and owns the chip. '
                         'Stop it before measuring anything by hand.' % running))

    if view['takeover']['state'] == 'stale':
        refusals.append(('held-elsewhere', view['takeover']['detail']))
    elif view['takeover']['state'] == 'ours':
        refusals.append(('held-by-us',
                         'this plugin is already holding this channel. Release it first, so '
                         'that the test measures the fan and not this plugin.'))

    if from_pct is None:
        refusals.append(('no-duty-readable',
                         'this channel\'s present output cannot be read, so there is no '
                         'known place to move upward from.'))
        target = None
    else:
        from_pct = int(from_pct)
        target = _target_for(from_pct, floor)
        if target - from_pct < MIN_MOVE_POINTS:
            refusals.append((
                'no-headroom',
                'this channel is already putting out %d%%, and the most this test may ask '
                'for is 100%%. A %d point move is too small to tell a response from '
                'tachometer noise, and this test never moves a fan DOWNWARD to make room - '
                'downward is where a fan stalls, and nobody has measured where that is on '
                'this one.' % (from_pct, target - from_pct)))

    # A silent channel is ALLOWED to be tested, and that is deliberate: it is
    # the only way a channel reading zero can ever become something better than
    # "four possibilities". The move is upward, so the worst case on a header
    # with nothing on it is that nothing happens.
    note = ''
    if view['tach_state'] != 'turning':
        note = ('This channel reports no turning fan. The test is still allowed, because it '
                'only moves upward and can therefore only make a fan that is there turn '
                'faster - but a result of "no change" will still not mean "there is no fan '
                'here". It will mean nobody can tell.')

    if view['law'] == 'reserved':
        note = ((note + ' ') if note else '') + (
            'This channel is under control law %s, which is named in no document available '
            'here. Release writes back exactly that value, so it can be handed back - but '
            'what it does in the meantime is not known.' % view['mode'])

    length = _length(seconds)
    return {
        'ok': not refusals,
        'channel': name,
        'number': entry.get('channel'),
        'from_pct': from_pct,
        'to_pct': target,
        'helper_floor_pct': floor,
        'helper_floor_source': entry.get('floor_source'),
        'rpm_now': entry.get('rpm'),
        'law': view['law'],
        'law_detail': view['law_detail'],
        'backstop': view['backstop'],
        'takeover': view['takeover'],
        'seconds': length,
        'deadman_seconds': round(length + DEADMAN_MARGIN, 1),
        'restores_to': _restores_to(entry),
        'cost': ('This makes the fan on %s loud for about %d seconds. That noise is the '
                 'measurement: if the fan gets louder, this channel drives it. Nothing else '
                 'in this plugin makes a noise on purpose, and nothing runs this by itself.'
                 % (name, length)),
        'refusals': [{'slug': slug, 'detail': detail} for slug, detail in refusals],
        'note': note,
        # A forecast, and labelled a forecast. run() reads the channel again
        # immediately before it writes and computes the duty from THAT, so a
        # figure a page drew a minute ago is not a promise. On a channel under
        # an automatic law it is a forecast that will be wrong whenever the
        # machine warms up in between - which is the whole of rule 1.
        'to_pct_is_forecast': True,
        'writes': ([] if refusals else
                   ['switch %s to manual, which freezes it at the speed it is turning at '
                    'now' % name,
                    'write about %d%% to %s - the exact figure is recomputed from a fresh '
                    'reading taken immediately before the write, and is never below what '
                    'the channel is putting out at that moment' % (target, name),
                    'release %s, which puts back %s' % (name, _restores_to(entry))]),
    }


def _target_for(from_pct, floor):
    """The duty to write, given a channel's present output and its floor.

    One function, used by plan() for its forecast and by run() for the figure
    it actually writes, so that the two cannot drift apart and quietly stop
    meaning the same thing.
    """
    from_pct = int(from_pct)
    return min(100, max(from_pct + STEP_UP_POINTS,
                        int(round(from_pct * STEP_UP_FACTOR)), int(floor)))


def _output_now(device, number):
    """One channel's output, read fresh: (percent, floor, entry).

    Raises Refused rather than returning a guess. A target computed from a
    figure nobody could read is exactly the defect this re-read exists to
    close, and falling back to a stale figure here would reintroduce it.
    """
    try:
        entry = device.channel_status(number)
    except chip.ChipError as failure:
        raise Refused('chip-unreadable',
                      'this channel could not be read again immediately before the write, '
                      'so there is no fresh figure to compute a duty from. Nothing was '
                      'written: %s' % failure.message)
    present = entry.get('output_pct')
    if present is None:
        present = entry.get('duty_pct')
    if present is None:
        raise Refused('no-duty-readable',
                      'the present output of this channel cannot be read, so there is no '
                      'known place to move upward from. Nothing was written.')
    floor = entry.get('floor_pct')
    return int(present), (int(floor) if floor is not None else 0), entry


def _restores_to(entry):
    if entry.get('saved_original'):
        return ('mode %s at raw %s, the record the helper already holds'
                % (entry.get('saved_mode'), entry.get('saved_duty_raw')))
    return ('mode %s (%s) at %s%%, which is what this channel is under right now and what '
            'the helper will record the moment the test takes it over'
            % (entry.get('mode'), entry.get('mode_name'), entry.get('duty_pct')))


def _length(seconds=None):
    """How long the whole thing takes, and it cannot be made too short.

    The tachometer is not judged for the first SPIN_SECONDS after a write - a
    fan is a lump of metal - so a test shorter than that plus a sample or two
    cannot notice anything at all. fanctl.py learned this the expensive way on
    the floor test (V, 2026-09-22: a three second test recorded three samples
    of 0 rpm and wrote down a floor); the same floor is enforced here rather
    than rediscovered.
    """
    least = (BEFORE_SAMPLES * SAMPLE_GAP) + probe.SPIN_SECONDS + (AFTER_SAMPLES * SAMPLE_GAP)
    if seconds is None:
        return round(least, 1)
    return round(max(least, min(120.0, float(seconds))), 1)


def _after_samples(length):
    """How many samples after the write, for a test of this length.

    `seconds` used to reach nothing but the deadman's deadline and the sentence
    on the page: the sampling was fixed, so a test asked for 120 seconds
    finished in 12.5 while the page had said 120 (V, 2026-09-22). Either the
    knob does something or it should not be offered. It now buys what a longer
    test is asked for, which is more samples after the write.
    """
    spare = float(length) - (BEFORE_SAMPLES * SAMPLE_GAP) - probe.SPIN_SECONDS
    return max(AFTER_SAMPLES, int(spare // SAMPLE_GAP))


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def _threshold(before):
    """What a change has to clear to be a change. One rule, used everywhere."""
    return max(RESPONSE_RPM, int(RESPONSE_FRACTION * max(int(before or 0), 1)))


def _sample(device, number, count, gap, abandon_below=None):
    """count tachometer reads, gap apart, and whether we were interrupted.

    abandon_below stops on the first read beneath it. That is the 'inverted'
    case, and stopping is the point: this file's docstring promised the test
    "stops at once" on an inverted reading and it did not - it sampled the full
    window and only then labelled the result (V, 2026-09-22: a 25 points/s ramp
    produced `inverted` after the whole 12.7 s run). Every sample abandoned
    here is a second less spent at a duty that may be the wrong one.
    """
    speeds = []
    for index in range(count):
        if _stopping:
            break
        try:
            speeds.append({'at': time.time(), 'rpm': device.tach(number)})
        except chip.ChipError as failure:
            speeds.append({'at': time.time(), 'rpm': None, 'error': failure.message})
        latest = speeds[-1].get('rpm')
        if abandon_below is not None and latest is not None and latest < abandon_below:
            speeds[-1]['abandoned_here'] = True
            break
        if index + 1 < count:
            time.sleep(gap)
    return speeds


def _median(samples):
    readable = [item['rpm'] for item in samples if item.get('rpm') is not None]
    return (int(statistics.median(readable)) if readable else None), len(readable)


def run(channel, confirmed=False, helper=None, seconds=None):
    """Run the test. Never called by anything in this plugin - only by a person.

    confirmed has no default that means yes and never will. It is the one place
    where a human being is in the loop by construction rather than by policy.
    """
    global _stopping

    if not confirmed:
        raise Refused('not-confirmed',
                      'this test makes a fan loud on purpose and writes to the fan '
                      'controller. It runs only when it is confirmed, by somebody who is '
                      'at the machine.')

    ready = plan(channel, helper, seconds)
    if not ready['ok']:
        first = ready['refusals'][0]
        raise Refused(first['slug'], first['detail'])

    device = _device(helper)
    number = int(ready['number'])
    name = ready['channel']
    helper_path = device.helper

    _stopping = False
    for received in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(received, _on_term)

    with OurLock():
        # The channel's output at the START of the before-samples, so that the
        # reading taken at the end of them has something to be compared with.
        # Inside the lock, because a figure read outside it was read while
        # somebody else may have been writing.
        opening_pct, _opening_floor, _opening_entry = _output_now(device, number)

        before = _sample(device, number, BEFORE_SAMPLES, SAMPLE_GAP)
        before_median, before_read = _median(before)

        # THE ORDER HERE IS THE SAFETY PROPERTY. The deadman is armed and
        # confirmed alive before a single register is written. A failure to arm
        # is a refusal, not a warning: a test that cannot be undone by anything
        # except itself is exactly the test this plugin does not run.
        guard = Deadman(helper_path, number, ready['deadman_seconds'])
        try:
            guard.arm()
        except OSError as failure:
            raise Refused('no-deadman',
                          'the process that would undo this test could not be started (%s), '
                          'so nothing was written.' % failure)
        if not guard.alive():
            # Reaped here rather than left behind: nothing has been written, so
            # there is nothing to undo, and a refusal should not leave a pid
            # file claiming a guard that is not there.
            guard.disarm()
            raise Refused('no-deadman',
                          'the process that would undo this test did not stay alive, so '
                          'nothing was written.')

        # THE FRESH READING, and rule 1 lives here. Everything above this point
        # has been reading and waiting for about three seconds, so the figure
        # plan() computed its forecast from is three seconds old. Read the
        # channel again now - lock held, deadman armed, nothing between this
        # and the write but one helper call - and decide from this.
        try:
            live_pct, live_floor, live_entry = _output_now(device, number)

            # Did it move under its own law while we watched the tachometer? If
            # it did, neither number this test would produce means anything:
            # the duty could land below the chip's own, and a rise afterwards
            # could be the curve. Refuse, having written nothing.
            if abs(live_pct - int(opening_pct)) > DRIFT_POINTS:
                raise Refused(
                    'moving',
                    'this channel moved on its own while the test was watching it: it was '
                    'putting out %d%% when the test took the lock and %d%% %.1f seconds '
                    'later, under control law %s. Something - almost certainly the chip\'s '
                    'own curve reacting to temperature - is driving this channel. A duty '
                    'computed from the first figure could land BELOW where the chip has got '
                    'to, and a tachometer rising after the write could be the curve rather '
                    'than the write. Nothing was written. Test this channel when the machine '
                    'is thermally quiet, or take it over first and test it from a duty that '
                    'is standing still.'
                    % (opening_pct, live_pct, BEFORE_SAMPLES * SAMPLE_GAP, ready['law']))

            # Something else may have grabbed the channel in those seconds too.
            # plan() checked; three seconds ago is not now.
            fresh_takeover = probe.takeover_of(live_entry)
            if fresh_takeover['state'] == 'stale':
                raise Refused('held-elsewhere', fresh_takeover['detail'])

            target = _target_for(live_pct, live_floor)
            if target - live_pct < MIN_MOVE_POINTS:
                raise Refused(
                    'no-headroom',
                    'this channel was putting out %d%% as the write was about to happen, and '
                    'the most this test may ask for is 100%%. A %d point move is too small '
                    'to tell a response from tachometer noise, and this test never moves a '
                    'fan DOWNWARD to make room. Nothing was written.'
                    % (live_pct, target - live_pct))
        except Refused:
            # Armed and never used. Disarm it here rather than leave it to fire
            # on a channel nothing has touched. A release on an untouched
            # channel writes nothing (D, fanctl-io.c cmd_release, state_forget),
            # but a pid file claiming an armed guard that guards nothing is a
            # lie on disk, and this plugin's whole argument is that the files
            # on disk mean what they say.
            guard.disarm()
            raise

        written = False
        applied = {}
        after = []
        problem = ''
        try:
            applied = device.set_duty(number, int(target))
            written = True
            waited = 0.0
            while waited < probe.SPIN_SECONDS and not _stopping:
                time.sleep(0.5)
                waited += 0.5
            after = _sample(device, number, _after_samples(ready['seconds']), SAMPLE_GAP,
                            abandon_below=(None if before_median is None
                                           else before_median - _threshold(before_median)))
        except chip.ChipError as failure:
            problem = failure.message
            # Codes that mean nothing was written: the helper refuses those
            # before it opens the chip at all (D, chip.py CODES 2,3,4,5,6,7,9).
            if failure.code in (2, 3, 4, 5, 6, 7, 9):
                written = False
        finally:
            restored = _release(device, number)
            fired = guard.disarm()

    after_median, after_read = _median(after)
    # The verdict talks about the numbers the write actually used, not the
    # forecast. Quoting the forecast is how a result comes to say "20% -> 50%"
    # about a fan that was at 53%.
    measured = dict(ready, from_pct=live_pct, to_pct=target)
    verdict, detail = _verdict(before_median, before_read, after_median, after_read,
                              measured, problem)

    answer = {
        'status': 'ok' if (written and restored.get('ok')) else 'failed',
        'at': time.time(),
        'channel': name,
        'number': number,
        'from_pct': live_pct,
        'to_pct': target,
        'opening_pct': opening_pct,
        'forecast_from_pct': ready['from_pct'],
        'forecast_to_pct': ready['to_pct'],
        'seconds': ready['seconds'],
        'wrote': written,
        'rpm_before': before_median,
        'rpm_after': after_median,
        'samples': {'before': before, 'after': after},
        'verdict': verdict,
        'responded': verdict in ('responded', 'appeared'),
        'detail': detail,
        'restored': restored,
        'deadman_fired': fired,
        'applied': applied,
        'interrupted': _stopping,
        'problem': problem,
    }
    if fired:
        answer['detail'] += (' The deadman fired before this test could disarm it, which '
                             'means the test took longer than it was allowed. The channel '
                             'was handed back by the deadman; the measurement above may be '
                             'of a fan that was already being released.')
    if written:
        answer.update(record(name, answer))
    return answer


def _release(device, number):
    """Hand the channel back, and never raise out of a finally block.

    A raise here would replace whatever went wrong in the test with whatever
    went wrong in the restore, and the first one is the one somebody needs.
    """
    try:
        return dict(device.release(number), ok=True)
    except Exception as failure:                        # noqa: BLE001 - deliberate
        return {'ok': False, 'error': str(failure),
                'detail': ('the channel could not be handed back from here. The deadman, '
                           'which is still armed at this point, will try again; if the fan '
                           'is wrong after that, power the appliance off and on - a warm '
                           'reboot does not necessarily clear a fan register.')}


def _verdict(before, before_read, after, after_read, ready, problem):
    """What this test is entitled to claim, and nothing beyond it."""
    if problem:
        return 'inconclusive', ('the duty was not accepted, so nothing was measured: %s'
                                % problem)
    if before_read < 2 or after_read < 2:
        return 'inconclusive', ('the tachometer could not be read enough times to compare '
                                '(%d readable before, %d after). Nothing is claimed.'
                                % (before_read, after_read))
    if _stopping:
        return 'inconclusive', ('the test was interrupted before it finished, so the samples '
                                'do not describe a settled fan. Nothing is claimed.')

    moved = after - before
    threshold = _threshold(before)

    # 'appeared' used to have no threshold of any kind: crossing STALL_RPM was
    # the whole test, so 199 rpm becoming 200 rpm was reported as the flat fact
    # that this channel drives a fan with a tachometer wire (V, 2026-09-22:
    # _verdict(199, 4, 200, 4, ...) returned 'appeared'). Measured tachometer
    # noise on this machine is 39 rpm peak to peak - 39 times that difference.
    # It clears the same bar as 'responded' before it is allowed to be a fact.
    if before < probe.STALL_RPM and after >= probe.STALL_RPM and moved >= threshold:
        return 'appeared', ('the tachometer was silent at %d%% and read %d rpm at %d%%, a '
                            'rise of %d rpm against a threshold of %d. This channel drives a '
                            'fan, that fan has a tachometer wire to this chip, and at the old '
                            'duty it was not turning.'
                            % (ready['from_pct'], after, ready['to_pct'], moved, threshold))
    if moved >= threshold:
        return 'responded', ('%d rpm at %d%% became %d rpm at %d%%, a rise of %d rpm against '
                             'a threshold of %d. This channel drives the fan this tachometer '
                             'counts.' % (before, ready['from_pct'], after, ready['to_pct'],
                                          moved, threshold))
    if moved <= -threshold:
        return 'inverted', ('%d rpm at %d%% became %d rpm at %d%%: the fan slowed down when '
                            'the duty went up. That is not a fan this plugin understands - a '
                            'voltage-controlled channel, a tachometer that belongs to a '
                            'different header, or something else writing to this chip. The '
                            'channel was handed back and nothing is concluded.'
                            % (before, ready['from_pct'], after, ready['to_pct']))
    return 'no-change', ('%d rpm at %d%% became %d rpm at %d%%, a change of %d rpm where %d '
                         'was needed. This does NOT mean there is no fan here. It is equally '
                         'consistent with a fan whose tachometer is not wired to this chip, '
                         'a header with no fan on it, a fan already at the top of its range, '
                         'and a channel whose output goes somewhere this tachometer does not '
                         'watch. The channel is not driven on this result.'
                         % (before, ready['from_pct'], after, ready['to_pct'], moved,
                            threshold))


def record(name, answer):
    """Write the result beside the floor measurements, in measured.json.

    One file for everything this machine has been measured to do, because the
    two questions are asked together and answered by the same person on the
    same afternoon: does this channel drive a fan, and how slowly may that fan
    be driven.
    """
    document = _read_json(MEASURED) or {'channels': {}}
    channels = document.setdefault('channels', {})
    entry = channels.setdefault(name, {})
    entry['response'] = {
        'at': answer['at'],
        'responded': answer['responded'],
        'verdict': answer['verdict'],
        'from_pct': answer['from_pct'],
        'to_pct': answer['to_pct'],
        'rpm_before': answer['rpm_before'],
        'rpm_after': answer['rpm_after'],
        'detail': answer['detail'],
    }
    history = entry.setdefault('response_history', [])
    history.append(dict(entry['response']))
    entry['response_history'] = history[-10:]
    _write_json(MEASURED, document)
    return {'recorded': MEASURED}


# ---------------------------------------------------------------------------

def _print(document):
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')


def main(argv):
    verb = argv[1] if len(argv) > 1 else ''
    try:
        if verb == 'plan' and len(argv) > 2:
            # A refused plan exits non-zero. It used to print its refusals and
            # exit 0 (V, 2026-09-22: `response.py plan 1` on the reference
            # appliance printed two refusals and exited 0), so a script that
            # asked "may I test this channel" and checked the exit status was
            # told yes every time.
            forecast = plan(argv[2], seconds=float(argv[3]) if len(argv) > 3 else None)
            _print(forecast)
            return 0 if forecast['ok'] else 1
        elif verb == 'run' and len(argv) > 2:
            # The word is typed in full, every time, by the person running it.
            # An option letter is something a script picks up by accident.
            confirmed = len(argv) > 3 and argv[3] == 'confirm'
            result = run(argv[2], confirmed=confirmed,
                         seconds=float(argv[4]) if len(argv) > 4 else None)
            _print(result)
            return 0 if result['status'] == 'ok' else 1
        else:
            _print({'status': 'failed',
                    'detail': 'usage: response.py plan <channel> [seconds] | '
                              'response.py run <channel> confirm [seconds]'})
            return 2
    except Refused as refusal:
        _print({'status': 'failed', 'slug': refusal.slug, 'detail': refusal.message})
        return 1
    except ValueError as bad:
        _print({'status': 'failed', 'detail': 'that is not a number: %s' % bad})
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
