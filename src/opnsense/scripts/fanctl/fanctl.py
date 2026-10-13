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

    Fan Control - the command every other part of the plugin goes through.

        fanctl.py probe             what chip is here, what the table says, and
                                    whether the two agree. Reads only.
        fanctl.py status            the live picture, as JSON: every fan, the
                                    temperature each decision came from, and
                                    the safety contract line by line.
        fanctl.py set CH PCT [SEC]  hold one channel at one duty, supervised,
                                    and give it back. Never below the floor.
        fanctl.py test CH PCT [SEC] the same, with the floor rule suspended and
                                    the tachometer watched: this is the
                                    instrument the floor is measured with.
        fanctl.py restore           hand every channel back to the hardware,
                                    now, from the captured baseline.
        fanctl.py start             start the daemon (the rc script's path)
        fanctl.py stop              stop it, and make sure it let go
        fanctl.py restart           stop, then start
        fanctl.py daemon            the loop itself; started by `start`
        fanctl.py diagnostics       probe and status in one answer, for the page

    ---------------------------------------------------------------------------

    THE ONE THING THIS FILE IS ABOUT

    A fan set wrong stops, and a firewall with a stopped fan cooks itself while
    nobody is looking. Everything below is arranged so that the machine ends up
    back under its own hardware control whenever anything at all is unclear.
    There is a short list of ways out of this file; every one of them ends in
    restore(), and each has its own line in daemon() and its own comment naming
    the failure it is there for: an exception, a SIGTERM, a SIGINT, a SIGHUP, a
    SIGQUIT, a sensor that stopped answering, a read-back that disagreed, a fan
    that stopped turning, the panic point, the watchdog. Every one of them was
    run against a fake chip rather than reasoned about, and three of them were
    run because they were broken (see Watchdog._fired, _on_term, and the
    hand-back counter in daemon()).

    TWO THINGS ARE NOT COVERED, and they are written down here rather than left
    to be discovered:

      * SIGKILL. There is no handler for it and there cannot be one. A duty this
        plugin wrote stays in the chip's register until something writes that
        register again - nothing in an NCT67xx reverts on its own, and a warm
        reboot does not necessarily clear it. What closes that gap is outside
        this file: the rc script's stop and its prestart both end in a restore,
        pre-deinstall restores before the packaged files go, and `fanctl.py
        restore` is one line anybody can type. Nothing inside this file can
        shorten it.

      * a helper run wedged in an uninterruptible port access. The watchdog kills
        our children before it restores, and SIGKILL does not reach a process in
        that state; the release then comes back as busy, and the incident file,
        the LOG_CRIT line, the rc status line and the page's one red alert are
        the honest answer - power the appliance off and on.

    "Restore" means exactly one thing: write back the control mode and the duty
    each channel was found under before this plugin touched it, which were read
    and stored before the first write. It does not mean "set a safe duty",
    because the safe duty of a chassis nobody has measured is not a thing this
    plugin knows.

    ---------------------------------------------------------------------------

    HOW THIS FILE REACHES THE HARDWARE

    chip.py beside this file is the only thing that touches a port, and below it
    fanctl-io does the port accesses themselves. Nothing in this file names a
    register. What it uses, and nothing else:

        chip.read_path()               one pass over the window: the chip, its
                                       channels, and for each one the speed, the
                                       duty, the control mode and the floor the
                                       helper will not go below. It picks the
                                       helper where the helper works and the
                                       read-only sysctl path where it does not,
                                       and says which in "source".
        chip.Chip().channel_status(n)  one channel, the same shape
        chip.Chip().tach(n)            one tachometer
        chip.Chip().set_duty(n, pct)   hold a channel at a duty, 1..100
        chip.Chip().release(n)         give a channel back
        chip.Chip().release_all()      the panic path
        chip.ChipError and its kinds   every refusal, each with a sentence

    Three things about that surface shape this file, and every one of them
    replaced something written against a layer that was only assumed:

      * CHANNELS ARE NUMBERED, from 1, as the chip numbers them. The names
        everything above this line uses - SYSFAN, CPUFAN - are ncthwm(4) labels
        for the same five registers, and chip.py carries them as aliases and
        never as facts. This file keeps the name as the key a person, a settings
        page and the hardware table use, and translates to a number at the last
        moment. A chip with no alias table gets FAN1..FANn.

      * THE FLOOR IS NOT THIS FILE ALONE. fanctl-io has a compiled-in hard
        floor, reads /usr/local/etc/fanctl/floor.conf, refuses anything below
        what those two say, and deliberately does not clamp: it names the floor
        and where the floor came from. So a duty this file thinks acceptable can
        still be refused, and the refusal is shown in the helper own words. It
        also means THE FLOOR TEST CANNOT MEASURE BELOW THE HELPER FLOOR -
        finding a stall point under it takes an edit to floor.conf, on the
        machine, by the owner, which is exactly the right gate for that.

      * RESTORE IS THE HELPER. release(n) writes back the mode and duty the
        channel had before this plugin first touched it, from a record the
        helper wrote at that moment, and where it has no record it falls back to
        the chip own PWM-off - full output, no thermal control. The baseline this
        file keeps is therefore a record for the page and for the contract, not
        the mechanism. That is the right way round: the thing that can undo a
        write is the thing that made it.

    Where chip.py is absent altogether, this file does not fall over and does
    not improvise: it says the hardware layer cannot do this, and reads
    tachometers through dev.ncthwm.* and temperatures through the ordinary
    sysctl tree - which is a useful plugin on any appliance, and the half that
    works on hardware nobody here has ever seen. It does NOT read those sysctls
    while the helper is usable; see sysctl_tach() for why that is a rule rather
    than a preference.
"""

import errno
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import syslog
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import curve as curves    # noqa: E402
import history            # noqa: E402
import sensors            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON_BIN = '/usr/sbin/daemon'
SELF = os.path.join(HERE, 'fanctl.py')

CONFIG_XML = '/conf/config.xml'
GENERAL = './OPNsense/FanCtl/general'

RUN_DIR = '/var/run/fanctl'
LOG_DIR = '/var/log/fanctl'
STATE_DIR = '/var/db/fanctl'

PID = os.path.join(RUN_DIR, 'fanctl.pid')
SUPERVISOR = os.path.join(RUN_DIR, 'fanctl.supervisor.pid')
LOG = os.path.join(LOG_DIR, 'fanctl.log')

# One flock for every sequence of port operations this plugin makes, held by
# whichever of our processes is talking to the chip. It cannot serialise us
# against the kernel - ncthwm_query_fan_speed() takes no lock at all, there is
# no mutex in struct ncthwm_softc (D) - and this file never pretends otherwise.
# What it does stop is the one race we can stop: a `test` from the page and the
# daemon writing an index byte and a data byte into the same two ports at the
# same moment, which would land our value in whichever register the other one
# had just selected. fanctl-io takes a non-blocking lock of its own over the same
# window and answers "busy" - this one is above that, so that our own two
# processes queue rather than one of them failing.
#
# It is held around the WRITES and never around a plain read, and that is not an
# oversight: flock is per open file description, so a second acquisition inside
# the first - which is what would happen when set_duty() has to look up a channel
# number and reads the chip to do it - would be this process waiting for itself,
# for ever, with a duty half written.
LOCK = os.path.join(RUN_DIR, 'chip.lock')

BASELINE = os.path.join(STATE_DIR, 'baseline.json')
MEASURED = os.path.join(STATE_DIR, 'measured.json')
STATUS = os.path.join(STATE_DIR, 'status.json')
HELD = os.path.join(STATE_DIR, 'held.json')
INCIDENT = os.path.join(STATE_DIR, 'incident.json')

# Not a setting, on purpose. The watchdog is what fires when the loop stops
# going round - deadlocked on the chip lock, stuck in a syscall, wedged in a
# way nobody predicted - and a watchdog whose length can be typed into a
# settings page is a watchdog somebody will eventually set to an hour. Six
# cycles of the shipped interval, with a floor under it so that a very short
# interval cannot make it trigger on an ordinary slow cycle.
WATCHDOG_MIN = 45.0
WATCHDOG_CYCLES = 6

# A tachometer below this is not a fan turning slowly, it is a fan that has
# stopped or one whose tach has stopped being believable. Deliberately low: the
# quietest fan measured on the reference appliance idles near 940 rpm, and the
# point of this number is to catch zero, not to judge a slow fan.
STALL_RPM = 200

# How long after a write the tachometer is given to catch up before it is
# judged. A fan is a lump of metal with inertia and the chip averages its own
# tach reading; asking it to have responded within a second is how a plugin
# convinces itself it has stalled a fan that was merely slowing down.
SPIN_SECONDS = 6.0

# How stale the daemon's own status document may be before a reader stops
# trusting it and goes and looks for itself.
STATUS_MAX_AGE = 30.0

# How many times one run may hand the fans back and take them again.
#
# Measured, 2026-09-22, against a fake chip: with a contract line that fails
# only while driving - the helper reporting no saved original for a channel this
# plugin is driving - the loop took control, wrote a duty, failed the contract on
# the next cycle, handed back, and took control again 2 s later, for ever. The
# hand-back CLEARS the state the failing line was reading, so the condition looks
# cured the moment it has been acted on. That is a fan alternating between two
# control laws all night, which the design forbids in as many words, and no
# amount of care inside one cycle prevents it.
#
# So a hand-back is counted, and the cycle after one is not allowed to take
# control again until the contract has held for a settling period. Three is
# enough for a genuinely transient fault - a sysctl that did not answer once -
# and few enough that a fault which repeats ends as a stopped service with one
# clear line in the log rather than as a noise nobody can find the source of.
HANDBACKS_ALLOWED = 3
SETTLE_CYCLES = 5
SETTLE_MIN = 60.0

# The hardware layer's exit codes that mean NOTHING WAS WRITTEN and no original
# was recorded, so the channel they name must not afterwards be "handed back".
#
# D, chip.py CODES and the order of the checks in fanctl-io.c cmd_duty(): the
# duty range (6) and the floor (7) are refused before identify() is called, a
# missing device (2) or a lack of privilege (3) before any port is opened, and a
# busy window (9) before state_save_once() runs. A release on such a channel
# would find no saved original and would therefore write the chip's own PWM-off
# - full output, no thermal control - to a channel this plugin never touched,
# destroying the only record of what law it was under (D, fanctl-io.c
# cmd_release: "If the saved original is missing or unreadable, the target is
# mode 0 with duty 255").
#
# 8 - readback - is deliberately NOT here. A readback failure means the write did
# go out, so that channel is ours and must be handed back.
# And this file's own: the chip lock could not be taken, which happens before the
# helper is even started, so nothing was written either.
LOCK_BUSY = -1

NOTHING_WRITTEN = frozenset((LOCK_BUSY, 2, 3, 4, 5, 6, 7, 9))

_stopping = False

# Whether this process is inside a ChipLock. Not a substitute for the flock - it
# says nothing about the other processes - but it is what turns the one mistake
# this lock cannot survive into a sentence instead of a stall. See LOCK above.
_holding_chip = 0


def log(message, level=syslog.LOG_NOTICE):
    syslog.openlog('fanctl', syslog.LOG_PID, syslog.LOG_DAEMON)
    syslog.syslog(level, message)
    syslog.closelog()
    # Also to the plugin's own log, because syslog is where this lands for an
    # administrator and the log file is what the page tails when somebody asks
    # "what did it do at 03:00". One line, one place to look.
    try:
        with open(LOG, 'a') as handle:
            handle.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), message))
    except OSError:
        pass


class Refused(Exception):
    """Something this plugin declines to do, with the reason for the page.

    Where the refusal came from the hardware layer it carries that layer's exit
    code as well, because a few of those codes say something this file has to
    act on rather than merely report: they are the refusals fanctl-io makes
    before it opens a port or records a channel's original state. See
    NOTHING_WRITTEN.
    """

    def __init__(self, message, code=None):
        self.code = code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Settings, the table, and the machine
# ---------------------------------------------------------------------------

def load_table():
    with open(os.path.join(HERE, 'fans.json'), 'r') as handle:
        return json.load(handle)


def smbios():
    """What the machine says it is, the same way both sibling plugins ask."""
    values = {}
    try:
        done = subprocess.run(['/bin/kenv'], capture_output=True, text=True, timeout=10)
        lines = done.stdout.splitlines() if done.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        lines = []
    for line in lines:
        if line.startswith('smbios.'):
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip().strip('"')
    return {
        'maker': values.get('smbios.planar.maker') or values.get('smbios.system.maker', ''),
        'product': values.get('smbios.planar.product') or values.get('smbios.system.product', ''),
        'version': values.get('smbios.planar.version') or values.get('smbios.system.version', ''),
    }


def match_model(table=None, fields=None):
    """(key, entry) for this machine; ('generic', entry) when nothing matches."""
    table = table if table is not None else load_table()
    models = table.get('models') or {}
    fields = fields if fields is not None else smbios()

    for key, entry in models.items():
        if key == 'generic':
            continue
        criteria = entry.get('match') or {}
        if not criteria:
            continue
        if all(re.search(pattern, fields.get(field, '') or '')
               for field, pattern in criteria.items()):
            return key, entry
    return 'generic', models.get('generic', {})


def settings(root=None):
    """The plugin's settings, with the table's defaults behind every one.

    Read straight out of config.xml, with no model and no validation, because
    the daemon has neither. Everything that comes out of here is therefore
    treated as untrusted: the curve is parsed by curve.py, which refuses what
    it cannot read, and every number is clamped below. A config.xml restored
    from a backup or edited by hand must not be able to hand a fan controller a
    duty nobody checked.
    """
    table = load_table()
    defaults = table.get('defaults') or {}
    values = {
        'enabled': False,
        'mode': defaults.get('mode', 'monitor'),
        'interval': int(defaults.get('interval', 10)),
        'floor_duty': int(defaults.get('floor_duty', 40)),
        'manual_duty': int(defaults.get('manual_duty', 70)),
        'limit_c': int(defaults.get('limit_c', 70)),
        'panic_c': int(defaults.get('panic_c', 85)),
        'hysteresis_c': int(defaults.get('hysteresis_c', 4)),
        'dwell_seconds': int(defaults.get('dwell_seconds', 30)),
        'step': int(defaults.get('step', 5)),
        'points': defaults.get('points', '40:60,50:75,60:90,70:100'),
        'sensors': defaults.get('sensors', 'cpu,pch,acpi'),
        'channels': '',
        'accept_unmeasured': False,
    }

    if root is None:
        try:
            root = ET.parse(CONFIG_XML).getroot()
        except Exception:
            return values

    general = root.find(GENERAL)
    if general is None:
        return values

    def text(name, default=None):
        found = general.findtext(name)
        return found if found not in (None, '') else default

    values['enabled'] = text('enabled', '0') == '1'
    values['accept_unmeasured'] = text('accept_unmeasured', '0') == '1'

    mode = (text('mode', values['mode']) or '').strip()
    values['mode'] = mode if mode in ('monitor', 'curve', 'manual') else 'monitor'

    for name in ('points', 'sensors', 'channels'):
        found = text(name)
        if found is not None:
            values[name] = found.strip()

    # Every bound here is the bound the model also enforces. Two copies of a
    # rule can drift, so the comment beside each one in FanCtl.xml names this
    # file - and where they ever do drift, the narrower of the two wins, which
    # is this one, because this is the copy with a fan behind it.
    limits = {
        'interval': (2, 120), 'floor_duty': (20, 100), 'manual_duty': (20, 100),
        'limit_c': (40, 100), 'panic_c': (45, 110), 'hysteresis_c': (0, 20),
        'dwell_seconds': (0, 600), 'step': (1, 25),
    }
    for name, (low, high) in limits.items():
        try:
            values[name] = int(float(text(name, values[name])))
        except (TypeError, ValueError, OverflowError):
            # OverflowError is in that list for one reason: float('1e400') is
            # infinity and int(infinity) raises it, not ValueError. A config.xml
            # carrying that in <interval> used to end the daemon with a traceback
            # from its first statement - nothing was written at that point, so
            # nothing was left held, but a traceback is not an answer.
            # A value that will not parse is not a reason to leave the one behind
            # it unchecked. What was in values[name] came from fans.json, which
            # ships correct numbers today and is still a file somebody can edit:
            # the clamp runs either way, so there is one bound and not two.
            pass
        values[name] = max(low, min(high, values[name]))

    if values['panic_c'] <= values['limit_c']:
        # Not a refusal: a panic point below the limit would otherwise mean the
        # machine hands the fans back before it has tried running them at full
        # duty, which is the wrong way round. Pushed above the limit instead,
        # and said out loud in the status document.
        values['panic_c'] = values['limit_c'] + 10

    return values


def watchdog_seconds(options):
    return max(WATCHDOG_MIN, options['interval'] * WATCHDOG_CYCLES)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def ensure_dirs():
    for path, mode in ((RUN_DIR, 0o755), (LOG_DIR, 0o750), (STATE_DIR, 0o750)):
        try:
            os.makedirs(path, mode=mode, exist_ok=True)
            os.chmod(path, mode)
        except OSError:
            pass


def read_json(path, default=None):
    try:
        with open(path, 'r') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path, document, mode=0o640):
    """Write a state file whole or not at all.

    A half-written baseline is the one file in this plugin that could actually
    hurt: restore() reads it to find out what the chip was doing before we
    touched it, and a truncated one would describe a state the machine was
    never in. Written to a temporary name in the same directory and renamed,
    which on UFS and ZFS alike is what makes the replacement atomic.
    """
    ensure_dirs()
    temporary = '%s.%d.tmp' % (path, os.getpid())
    try:
        with open(temporary, 'w') as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as failure:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise failure


def read_pid(path):
    """The pid in a pidfile, or None.

    daemon(8) writes the pid with no trailing newline. read(1) in a shell
    reports failure on an unterminated last line even though it has already
    assigned the value, which is the bug the sibling plugin's rc script was
    written around; Python has no such trap here, but the pidfile still has to
    be treated as possibly empty, possibly stale and possibly somebody else's.
    """
    try:
        with open(path, 'r') as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    if pid <= 1:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid


def daemon_pid():
    return read_pid(PID)


class ChipLock(object):
    """An exclusive flock held for the length of one chip conversation."""

    def __init__(self, timeout=5.0):
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        global _holding_chip

        # The one failure this lock cannot survive, turned into a sentence.
        # Measured, 2026-09-22: a second ChipLock inside the first, in the same
        # process, is REFUSED after the full timeout - flock is per open file
        # description and each of these opens the file again, so the process
        # waits for itself and then gives up. Left as a timeout it cost 15.4 s
        # of spinning inside a SIGALRM handler and a fan that stayed held; said
        # out loud it is a bug report. Callers that must write while this
        # process already holds the window pass take_lock=False and say why.
        if _holding_chip:
            raise Refused('this process already holds the chip lock. Taking it twice is '
                          'waiting for ourselves; nothing was written.')

        ensure_dirs()
        self.handle = open(LOCK, 'a+')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                _holding_chip += 1
                return self
            except OSError as failure:
                if failure.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise Refused('another part of this plugin is talking to the chip; '
                                  'nothing was written.', LOCK_BUSY)
                time.sleep(0.05)

    def __exit__(self, *_):
        global _holding_chip

        if self.handle is not None:
            _holding_chip = max(0, _holding_chip - 1)
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


class NoLock(object):
    """Stands where a ChipLock stands, and takes nothing.

    Exactly one caller is allowed this: the watchdog, restoring from inside its
    own signal handler. It is not an optimisation and it is not a shortcut past
    the lock - it is the only correct thing to do there, because the code the
    handler interrupted may be holding that lock and the handler cannot wait for
    a lock its own process is holding. The serialisation that still applies is
    fanctl-io's own non-blocking lock over the window, which is a different
    process and answers "busy" rather than deadlocking.
    """

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


# ---------------------------------------------------------------------------
# The hardware layer, at arm's length
# ---------------------------------------------------------------------------

def _says_the_same(message, explanation, enough=0.6):
    """Would printing both of these read as a stutter?

    Word overlap rather than substring containment, because the two sentences
    that need catching here are paraphrases of each other and neither contains
    the other. A simple ratio is enough: nothing turns on it except whether a
    refusal is printed once or twice.
    """
    words = set(re.findall(r'[a-z0-9-]+', explanation.lower()))
    if not words:
        return True
    seen = set(re.findall(r'[a-z0-9-]+', message.lower()))
    return len(words & seen) / float(len(words)) >= enough


class Hardware(object):
    """chip.py, wrapped so that its absence is a sentence and not a traceback.

    Every call goes through here, and every one of them can answer "the hardware
    layer cannot do this, because X". That matters more than it looks: this
    plugin is meant to be installed on appliances nobody here has seen, where
    the honest outcome is often exactly that - and a fan controller whose
    failure mode is a Python traceback in a log is a fan controller nobody will
    trust with a chassis.

    It is also the one place that knows a channel has a number as well as a
    name. Everything above this class works in names, because that is what a
    person types and what the hardware table is written in; chip.py works in
    numbers, because that is what the chip has. The translation happens here and
    nowhere else.
    """

    def __init__(self):
        self.module = None
        self.chip = None
        self.problem = ''
        self.numbers = {}
        try:
            import chip
            self.module = chip
            self.chip = chip.Chip()
        except Exception as failure:      # ImportError, and anything import raises
            self.problem = 'the hardware layer (chip.py) could not be loaded: %s' % failure

    @property
    def present(self):
        return self.module is not None

    @staticmethod
    def name_for(number, alias):
        """The name this plugin calls a channel by.

        The alias where chip.py has one - so that the page, the sysctl and the
        hardware table all say SYSFAN about the same register - and FAN<n> where
        it does not, which is every chip whose channels ncthwm(4) has no table
        for. Either way it is stable for a given chip, which is what a settings
        page needs it to be.
        """
        return str(alias) if alias else 'FAN%d' % int(number)

    def _refused(self, failure, doing):
        """A ChipError turned into one sentence somebody can act on.

        chip.py classifies every one of the helper exit codes and attaches a
        plain explanation to it - the helper must run as root, another fanctl-io
        holds the window, the duty is below the floor. Both halves are kept: the
        message says what happened to this call, the explanation says what that
        kind of failure means.
        """
        message = getattr(failure, 'message', None) or str(failure)
        explanation = getattr(failure, 'explanation', '')
        if explanation and _says_the_same(message, explanation):
            # The two often are the same sentence in different words - "another
            # fanctl-io holds the window" against "another fanctl-io holds the
            # hardware-monitor window" - and printing both makes a careful layer
            # look like a stutter. Where they differ, both are kept: the message
            # is what happened to this call, the explanation is what that kind of
            # failure means, and for a refusal like "must run as root" the second
            # is the actionable half.
            explanation = ''
        if explanation:
            message = '%s - %s' % (message, explanation)
        return Refused('%s: %s' % (doing, message), getattr(failure, 'code', None))

    def _channel(self, entry):
        """One channel of a chip.py answer, in the shape the rest of this file reads."""
        number = int(entry.get('channel'))
        name = self.name_for(number, entry.get('alias'))
        self.numbers[name] = number
        return {
            'name': name,
            'number': number,
            'tach': entry.get('rpm'),
            # What is SET, and separately what is coming OUT. On this family only
            # some channels have a readback register, so output is None rather
            # than a copy of the duty where the chip cannot say (D, chip.py).
            'duty': entry.get('duty_pct'),
            'output': entry.get('output_pct'),
            'mode': entry.get('mode'),
            'mode_name': entry.get('mode_name'),
            # The helper own floor for this channel, and where it came from. It is
            # the authority underneath everything this plugin decides: nothing
            # above it can lower it, and a duty below it is refused rather than
            # quietly raised.
            'helper_floor': entry.get('floor_pct'),
            'helper_floor_source': entry.get('floor_source'),
            'saved': entry.get('saved_original'),
        }

    def view(self):
        """The whole chip in one pass over the window.

        One call, not one per channel, and that is a hardware decision rather
        than a tidiness one: every extra helper run is another walk over two I/O
        ports that the kernel own reader touches without taking a lock.
        """
        if self.module is None:
            raise Refused(self.problem)
        try:
            answer = self.module.read_path()
        except Exception as failure:
            raise self._refused(failure, 'the chip could not be read')

        described = answer.get('chip') or {}
        self.numbers = {}
        channels = [self._channel(entry) for entry in (answer.get('channels') or [])]

        return {
            'present': True,
            'chip': described.get('name') or '',
            'id': described.get('devid') or '',
            'index_port': described.get('index_port') or '',
            'data_port': described.get('data_port') or '',
            'hwm_base': described.get('hwm_base') or '',
            'writable': bool(answer.get('writable')),
            'access': answer.get('source') or '',
            'source': answer.get('source') or '',
            'hard_floor': answer.get('hard_floor_pct'),
            'channel1_output': answer.get('channel1_output') or '',
            # "why" is the sysctl path explaining that it cannot write; "note" is
            # the sentence about a tachometer reading zero. Both are the layer own
            # words and both are carried rather than paraphrased.
            'detail': ' '.join(part for part in (answer.get('why'), answer.get('note')) if part),
            'unavailable': answer.get('helper_unavailable') or None,
            'channels': channels,
            'tach': dict((item['name'], item['tach']) for item in channels
                         if item.get('tach') is not None),
            'problem': '',
        }

    def number(self, name):
        if name not in self.numbers:
            # The map is filled by view(); an empty one means nothing in this
            # process has looked at the chip yet.
            self.view()
        if name not in self.numbers:
            raise Refused('%s is not a channel on this chip. It has: %s.'
                          % (name, ', '.join(sorted(self.numbers)) or 'none'))
        return self.numbers[name]

    def read(self, name):
        """One channel, freshly read. One pass over the window."""
        if self.module is None:
            raise Refused(self.problem)
        number = self.number(name)
        try:
            return self._channel(self.chip.channel_status(number))
        except Exception as failure:
            raise self._refused(failure, '%s could not be read' % name)

    def set_duty(self, name, percent):
        """Hold one channel at a duty. The helper checks its own floor first.

        Nothing is clamped on the way through here. A duty below the helper floor
        is refused by the helper before it opens anything, and that refusal names
        the floor and where it came from - which is what belongs in front of
        somebody. Raising their 20% to 35% without saying so would teach them the
        wrong thing about what this plugin is doing to their fans.
        """
        if self.module is None:
            raise Refused(self.problem)
        number = self.number(name)
        try:
            return self.chip.set_duty(number, int(percent))
        except Exception as failure:
            raise self._refused(failure, 'the duty for %s was not accepted' % name)

    def release(self, name):
        """Give one channel back to the hardware.

        The helper writes back the mode and duty it recorded when this plugin
        first touched that channel, and where it has no such record it falls back
        to the chip own PWM-off, which is full output. Both of those are better
        than anything this file could decide, which is why this function holds no
        policy at all.
        """
        if self.module is None:
            raise Refused(self.problem)
        number = self.number(name)
        try:
            return self.chip.release(number)
        except Exception as failure:
            raise self._refused(failure, '%s could not be handed back' % name)

    def release_all(self):
        """Every channel, through failures, without needing the chip identified.

        Kept for the one case that justifies it: a restore where this plugin own
        record of what it was driving cannot be read. chip.py does not stop at
        the first refusal here and does not need identify() to have worked.
        """
        if self.module is None:
            raise Refused(self.problem)
        try:
            return self.chip.release_all()
        except Exception as failure:
            raise self._refused(failure, 'the fans could not all be handed back')


# ---------------------------------------------------------------------------
# Reading without the hardware layer
# ---------------------------------------------------------------------------

_NCTHWM = re.compile(r'^dev\.(ncthwm|aibs)\.\d+\.([A-Za-z0-9_]+):?\s*(-?\d+)')


def cpld_tach():
    """Tachometers from the appliance's CPLD, as {channel: rpm}, where os-xgs-npu's
    sensor tool is installed and the board has the bank (the XGS 3300). Read-only,
    like everything that tool does; an absent tool or a failed read is {}.
    """
    tool = '/usr/local/sbin/xgs3300-sensors'
    if not os.access(tool, os.X_OK):
        return {}
    try:
        done = subprocess.run([tool, 'json'], capture_output=True, text=True, timeout=10)
        values = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {}
    found = {}
    for key, name in (('fan0_rpm', 'FAN0'), ('fan1_rpm', 'FAN1')):
        if isinstance(values.get(key), int):
            found[name] = values[key]
    return found


def sysctl_tach():
    """Tachometers the kernel already publishes, as {channel: rpm}.

    This is the fallback reader, and it is also the honest one: it writes
    nothing, it needs no module loaded and no port opened, and on the reference
    appliance it is where the numbers on the page come from today. ncthwm reads
    five channels on this chip (V, 2026-09-22: SYSFAN 941, the other four 0).

    It is not a substitute for chip.py. It cannot say what duty a channel is
    at, nor which control law it is under, and it can never set anything -
    sysctl -aW | grep ncthwm returns nothing, because every one of those nodes
    is registered CTLFLAG_RD (V, and D from ncthwm.c).

    AND IT IS ONLY EVER CALLED WHEN chip.py IS NOT THERE AT ALL. That is a rule,
    not a preference. Reading these sysctls walks the same two I/O ports the
    helper walks, and ncthwm takes no lock: a read landing between the helper
    index write and its data write puts the helper byte in whichever register
    the kernel selected last. chip.py already decides between the two paths and
    refuses to read the sysctls while the helper is live; a second, cheerful
    fallback here would undo that decision from above. So where chip.py is
    present, whatever it returned is the answer, including when the answer is
    that it could not read the chip.
    """
    found = {}
    try:
        done = subprocess.run(['/sbin/sysctl', 'dev.ncthwm', 'dev.aibs'],
                              capture_output=True, text=True, timeout=10)
        lines = done.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return found
    for line in lines:
        match = _NCTHWM.match(line.replace('=', ': '))
        if match is None:
            continue
        name, value = match.group(2), int(match.group(3))
        if name.upper().endswith('FAN') or 'FAN' in name.upper():
            found[name.upper()] = value
    return found


# ---------------------------------------------------------------------------
# The fingerprint and the contract
# ---------------------------------------------------------------------------

def fingerprint(entry, found):
    """Does the chip on this machine match the entry that matched by SMBIOS?

    Returns (ok, [what disagreed]). An entry with an empty verify block is not
    a match that passed, it is a match nobody described, and the caller treats
    it as such: verify is the thing that stops a table entry written for one
    revision of a board from being applied to another wearing the same SMBIOS
    strings.
    """
    wanted = entry.get('verify') or {}
    if not wanted:
        return False, ['this entry carries no verify block, so nothing about the chip could '
                       'be checked against it.']

    complaints = []

    chip_name = str(found.get('chip') or '')
    if 'chip' in wanted and not re.search(wanted['chip'], chip_name):
        complaints.append('the table expects a chip matching %s and this machine reports "%s".'
                          % (wanted['chip'], chip_name or 'nothing'))

    if 'hwm_base' in wanted:
        base = str(found.get('hwm_base') or '').lower()
        if base != str(wanted['hwm_base']).lower():
            complaints.append('the table expects the monitoring window at %s and this machine '
                              'reports %s.' % (wanted['hwm_base'], base or 'nothing'))

    if 'channels' in wanted:
        here = [str(channel.get('name') or channel) for channel in found.get('channels') or []]
        missing = [name for name in wanted['channels'] if name not in here]
        if missing:
            complaints.append('the table expects the channels %s and this machine does not '
                              'report %s.' % (', '.join(wanted['channels']), ', '.join(missing)))

    return (not complaints), complaints


def full_output_only(options):
    """True when nothing this configuration can decide is below full output.

    Which is the one case where a channel with no measured floor may still be
    driven, because the floor exists to stop this plugin writing a duty that
    stops a fan and 100% cannot stop one: it is the chip's own full output,
    the value it writes when PWM is switched off, and it is above every floor
    that could ever be measured - including the hard one compiled into the
    helper.

    Manual at 100 and nothing else. A curve is not admitted here even when
    every one of its points is 100, because a curve is a thing people edit: a
    rule that stopped applying the moment somebody moved a point from 100 to
    95, without saying so, would be a rule nobody could see. The mode is on
    the settings page in one word, and so is the number.
    """
    return options['mode'] == 'manual' and int(options['manual_duty']) >= 100


# The floor that is in force when there is no measured one and full_output_only()
# has carried the contract. It is not a guess and not a default: it is the lowest
# duty this configuration can write, which is what a floor is.
FULL_OUTPUT = 100

FULL_OUTPUT_SOURCE = ('not measured; this configuration only ever writes full output, which '
                      'cannot stop a turning fan')


def floor_for(channel, entry, options, measured):
    """(floor, where it came from) for one channel, or (None, why not).

    Three sources, in this order, and the order is the whole point:

      1. a floor measured on THIS machine with the test verb. Nothing beats a
         measurement taken on the fan that is about to be driven.
      2. a floor the table carries with a confirmed line behind it. Somebody
         stood in front of that model and wrote down what they saw.
      3. nothing. The settings page's floor is NOT a third source - it is a
         limit the owner may raise above a measured floor, never a substitute
         for having measured one. A number typed into a web page is not a
         measurement, and this is the one place in the plugin where that
         distinction decides whether a fan is driven at all.
    """
    taken = (measured.get('channels') or {}).get(channel) or {}
    if taken.get('floor') is not None:
        # Two measurements, and the higher wins. 'floor' is the positive one -
        # a duty at which this fan was seen to keep turning, plus a step.
        # 'not_below' is the negative one - a duty at which it was seen to stop,
        # plus a step - and it exists because the two can disagree: a fan that
        # survived 40% in March and stalled at 45% in August has told us
        # something about August, and the March reading must not overrule it.
        floor = max(int(taken['floor']), int(taken.get('not_below') or 0))
        return floor, 'measured on this machine on %s' % (
            time.strftime('%Y-%m-%d', time.localtime(taken.get('at', 0))))
    if taken.get('not_below') is not None:
        # Stopped at a duty, never yet seen turning at one. That is half a
        # measurement: it rules a duty out without establishing one, and a
        # floor is a number a fan has been SEEN to survive.
        #
        # The duty that stalled it is quoted from the record and not recovered by
        # subtracting a step from not_below. That arithmetic was here, with 5
        # hard-coded in it, and it printed the wrong number for anybody whose
        # step is not 5 - a sentence about a measurement, off by the difference.
        stalled_at = taken.get('stalled_at')
        return None, ('this fan stopped at %s, and no duty has yet been seen to keep it '
                      'turning. Test upwards from %d%% until one does.'
                      % ('%d%%' % int(stalled_at) if stalled_at is not None
                         else 'a duty below %d%%' % int(taken['not_below']),
                         int(taken['not_below'])))

    for fan in entry.get('fans') or []:
        if fan.get('channel') == channel and fan.get('floor') is not None:
            if fan.get('confirmed'):
                return int(fan['floor']), 'from the table: %s' % fan['floor_source']
            return None, ('the table carries a floor for this channel but no confirmation '
                          'behind it, which is a guess with a fan behind it.')

    return None, ('no floor has been measured for this channel. Run the floor test with the '
                  'appliance in front of you; until then it is not driven.')


def baseline_covers(channels, max_age=3600.0):
    """Is there a usable baseline for exactly these channels?

    Age is checked, but generously, and the reason is worth writing down: what
    a baseline is FOR is the control mode, not the duty. If the chip was under
    SmartFan IV when we found it, its duty was a moving number that the chip
    itself recomputes the instant that law is back in charge - so a duty an
    hour old is not a stale setting, it is a number that will be overwritten by
    the hardware within a cycle of the restore. The mode is what has to be
    right, and the mode does not drift on its own.
    """
    document = read_json(BASELINE) or {}
    kept = document.get('channels') or {}
    if not channels or any(name not in kept for name in channels):
        return False
    return (time.time() - document.get('at', 0)) <= max_age


def contract(options, entry, found, measured, reading, channels, capture_problem=''):
    """The six conditions, each with a sentence, in the order they are checked.

    Evaluated every cycle rather than once at start, because four of the six
    can stop being true while the daemon runs: a sensor can vanish, a fan can
    stop, a baseline file can be removed, and a chip can start answering
    differently. The daemon drives only while every line says yes.
    """
    lines = []

    ok, complaints = fingerprint(entry, found)
    # Identity and reach are one line rather than two, because they fail
    # together in every case that matters: a machine whose chip is not the
    # chip in the table, and a machine whose chip cannot be written to, are
    # both machines this plugin has no business driving. Saying which of the
    # two it was is what the detail is for.
    if ok and not found.get('writable'):
        ok = False
        complaints = ['the chip matches the table, but the hardware layer reports no writable '
                      'path to it%s' % (': ' + found['problem'] if found.get('problem')
                                        else '. Reading works; writing does not.')]
    lines.append({
        'name': 'fingerprint',
        'ok': ok,
        'detail': ('the chip matches the entry for %s, and it can be written to.'
                   % entry.get('display', 'this machine')) if ok else ' '.join(complaints),
    })

    permitted = bool(entry.get('control'))
    if not permitted and options['accept_unmeasured']:
        permitted = True
        permission = ('the table does not permit control on this model; the owner has accepted '
                      'that in writing on the settings page.')
    elif permitted:
        permission = 'the table permits control on this model.'
    else:
        permission = str(entry.get('control_reason') or
                         'the table does not permit control on this model.')
    lines.append({'name': 'permission', 'ok': permitted, 'detail': permission})

    baseline = read_json(BASELINE) or {}
    kept = baseline.get('channels') or {}
    have = [name for name in channels if name in kept]
    captured = bool(channels) and len(have) == len(channels)

    # And the stronger half of the same question, which costs nothing because it
    # is already in the reading: for a channel this plugin is ALREADY driving, the
    # helper must say it has its own saved original. That is the record release()
    # actually writes back, so a driven channel without one is a channel whose
    # restore would fall through to the chip own PWM-off - full output. Safe, and
    # not what anybody asked for, so it is reported rather than discovered.
    driving = (read_json(HELD) or {}).get('channels') or []
    unsaved = [item['name'] for item in (found.get('channels') or [])
               if item['name'] in channels and item['name'] in driving
               and item.get('saved') is False]

    lines.append({
        'name': 'baseline',
        'ok': captured and not unsaved,
        'detail': ('what each channel was under before this plugin touched it was recorded on '
                   '%s, and the helper holds its own record to restore from.'
                   % time.strftime('%Y-%m-%d %H:%M', time.localtime(baseline.get('at', 0))))
                  if captured and not unsaved else
                  ('the helper has no saved original for %s, although this plugin is driving '
                   'them: handing those back would fall through to the chip own full-output '
                   'setting.' % ', '.join(unsaved)) if unsaved else
                  ('the original fan settings could not be read, so there would be no record of '
                   'what they were: %s' % capture_problem) if capture_problem else
                  'the original fan settings have not been recorded. Nothing is driven until '
                  'they have been.',
    })

    # The one channel a floor does not have to have been measured for is one that
    # will only ever be written to at full output - see full_output_only(). The
    # line is still reported as carried BY that, and not as "measured", because
    # the two are different states and the page has to be able to tell them apart.
    full = full_output_only(options)
    unmeasured = []
    for name in channels:
        floor, _ = floor_for(name, entry, options, measured)
        if floor is None:
            unmeasured.append(name)
    without = [] if full else unmeasured
    lines.append({
        'name': 'floor',
        'ok': bool(channels) and not without,
        'detail': ('every channel to be driven has a measured floor.'
                   if channels and not unmeasured else
                   'no floor has been measured for %s, and none is needed while the mode is a '
                   'fixed 100%%: full output is what the chip writes with PWM switched off and '
                   'it cannot stop a fan that is turning. Lower the fixed duty below 100%%, or '
                   'choose the curve, and this line asks for a measurement again.'
                   % ', '.join(unmeasured)
                   if channels and full else
                   'no floor has been measured for: %s.' % (', '.join(without) or 'nothing '
                        'is selected to drive')),
    })

    stalled = []
    for name in channels:
        rpm = (found.get('tach') or {}).get(name)
        if rpm is None or rpm < STALL_RPM:
            stalled.append(name)
    lines.append({
        'name': 'tach',
        'ok': bool(channels) and not stalled,
        'detail': 'every channel to be driven reports a fan that is turning.'
                  if channels and not stalled else
                  'no turning fan is reported on: %s. A channel with no tachometer signal may '
                  'have no fan on it, or a fan with no tach wire; nothing here can tell those '
                  'apart, so it is not driven.' % (', '.join(stalled) or 'nothing is selected'),
    })

    wanted = sensors.groups_from(options['sensors'])
    celsius, names, missing = sensors.decide(reading, wanted)
    lines.append({
        'name': 'sensors',
        'ok': celsius is not None and not missing,
        'detail': ('%.1f C, read from %s.' % (celsius, ', '.join(names)))
                  if celsius is not None and not missing else
                  'a temperature the decision needs is not being read: %s.'
                  % (', '.join(missing) or 'none of the chosen sensors answered'),
    })

    return lines


def contract_ok(lines):
    return all(line['ok'] for line in lines)


def curve_summary(options):
    """The curve as the page should draw it, sampled by the code that drives the fan.

    Sampled here rather than redrawn in JavaScript from the same four numbers, because
    the interesting parts of this curve are not in the numbers: the rounding up to a
    step, the floor, and the cliff at the limit temperature are all in curve.py, and a
    picture that agreed with the points on the page but not with the daemon would be
    worse than no picture at all.

    A curve that cannot be parsed comes back as an error rather than as an empty
    picture. That is the one case where the page has something urgent to say: the daemon
    would refuse to drive with that curve, and the reason is a sentence.
    """
    try:
        shape = curves.Curve(options['points'], floor=options['floor_duty'],
                             step=options['step'], limit_c=options['limit_c'],
                             panic_c=options['panic_c'])
    except curves.CurveError as failure:
        return {'points': options['points'], 'error': str(failure), 'sample': []}
    return {
        'points': options['points'],
        'floor': options['floor_duty'],
        'step': options['step'],
        'limit_c': options['limit_c'],
        'panic_c': options['panic_c'],
        'error': '',
        'sample': shape.sample(low=20, high=max(90, options['panic_c']), step=2),
    }


# ---------------------------------------------------------------------------
# Reading the machine
# ---------------------------------------------------------------------------

def observe(hardware, entry):
    """Everything readable about the fans right now. Writes nothing, ever.

    One pass over the window through chip.py, which itself picks the helper
    where the helper works and the read-only sysctl path where it does not. Both
    of those are read-only, which is why this is safe to call from a page on
    hardware nobody has ever confirmed - and it is the only function in this
    file that the page reaches on a machine where nothing may be written.

    Where chip.py is missing altogether this falls back to reading the kernel
    tachometer sysctls directly. Where chip.py is present and could not read the
    chip, it does NOT: see sysctl_tach().
    """
    found = {'chip': '', 'id': '', 'index_port': '', 'data_port': '', 'hwm_base': '',
             'writable': False, 'access': '', 'present': False, 'detail': '', 'channels': [],
             'tach': {}, 'source': 'none', 'problem': '', 'hard_floor': None,
             'unavailable': None, 'channel1_output': ''}

    if hardware.present:
        try:
            found.update(hardware.view())
        except Refused as refusal:
            found['problem'] = str(refusal)
    else:
        found['problem'] = hardware.problem
        tach = sysctl_tach()
        if tach:
            # The kernel own reading is still a reading, and showing it is better
            # than an empty page on a machine where a fan is plainly turning.
            found['source'] = 'sysctl'
            found['access'] = 'sysctl'
            found['tach'] = tach
            found['channels'] = [{'name': name, 'number': None, 'tach': rpm, 'duty': None,
                                  'output': None, 'mode': None, 'mode_name': None,
                                  'helper_floor': None, 'helper_floor_source': None,
                                  'saved': None}
                                 for name, rpm in sorted(tach.items())]
            found['detail'] = ('read through the kernel driver, which publishes tachometers and '
                               'nothing else: no duty, no control law, and no way to set '
                               'anything.')

    if not found.get('channels'):
        # No super-I/O channel on this machine, whichever path was asked. The appliance's own
        # fan bank, read through the board's CPLD by the tool os-xgs-npu installs, is the last
        # place to look: real tachometers, watched and never driven, on the XGS 3300.
        tach = cpld_tach()
        if tach:
            found['source'] = 'cpld'
            found['access'] = 'cpld'
            found['tach'] = tach
            found['channels'] = [{'name': name, 'number': None, 'tach': rpm, 'duty': None,
                                  'output': None, 'mode': None, 'mode_name': None,
                                  'helper_floor': None, 'helper_floor_source': None,
                                  'saved': None}
                                 for name, rpm in sorted(tach.items())]
            found['detail'] = ('the fans as the board reads them, through the CPLD over the '
                               'host SMBus: tachometers and nothing else, no duty and no way '
                               'to set anything - this board keeps its own fan curve.')
            found['problem'] = ''

    # What the table says about each channel, joined on here so that everything
    # downstream - the page, the contract, the daemon - reads one list.
    described = dict((fan.get('channel'), fan) for fan in (entry.get('fans') or []))
    for channel in found['channels']:
        fan = described.get(channel['name']) or {}
        channel['label'] = fan.get('label') or ''
        channel['expected'] = fan.get('tach') or 'unknown'
        channel['sensors'] = fan.get('sensors') or []
        channel['confirmed'] = fan.get('confirmed') or ''
        # Never "stopped". A tach of zero means this chip is not being told about
        # a fan there, and no software on this machine can tell a missing fan from
        # a fan with no tach wire from a header this chip does not sample. Saying
        # "4 fans stopped" on an appliance whose fans are all turning is exactly
        # the kind of wrong that gets a plugin switched off and never switched on
        # again. chip.py and fanctl-io both say the same thing in their own words.
        if channel.get('tach') is None:
            channel['state'] = 'unreadable'
        elif channel['tach'] >= STALL_RPM:
            channel['state'] = 'turning'
        elif channel['expected'] == 'turning':
            channel['state'] = 'stopped'
        else:
            channel['state'] = 'silent'

    return found


# ---------------------------------------------------------------------------
# Writing, and taking it back
# ---------------------------------------------------------------------------

def capture_baseline(hardware, channels, found=None):
    """Write down what each channel was doing before we touched it.

    A record, not the mechanism, and the difference matters. What actually undoes
    a write is fanctl-io: it saves the mode and duty of a channel at the moment
    this plugin first sets a duty on it, in its own per-channel state file, and
    release() writes that back. This file could not do that job better and does
    not try - the thing that can undo a write is the thing that made it.

    What this is for, then, is everything around that: the page shows what the
    chip was found under, the baseline line of the safety contract has something
    to check, and a machine that comes back from a crash has a record of what was
    normal here before the plugin arrived. It is taken from the reading the cycle
    already did, so it costs no extra pass over the window.
    """
    view = found if found is not None else hardware.view()
    kept = {}
    for channel in view.get('channels') or []:
        if channel['name'] not in channels:
            continue
        kept[channel['name']] = {
            'number': channel.get('number'),
            'mode': channel.get('mode'),
            'mode_name': channel.get('mode_name'),
            'duty': channel.get('duty'),
            'helper_floor': channel.get('helper_floor'),
            'helper_floor_source': channel.get('helper_floor_source'),
            'helper_saved': channel.get('saved'),
        }
    missing = [name for name in channels if name not in kept]
    if missing:
        raise Refused('%s could not be read, so there is no record of what they were set to '
                      'beforehand.' % ', '.join(missing))
    write_json(BASELINE, {'at': time.time(), 'channels': kept})
    return kept


def mark_held(channels):
    """Write down that this plugin is now driving these channels.

    Read at the next start. A start that finds this file has found the wreckage
    of a run that did not get to put things back, and it restores from the
    baseline before it does anything else.
    """
    write_json(HELD, {'at': time.time(), 'pid': os.getpid(), 'channels': list(channels)})


def clear_held():
    try:
        os.unlink(HELD)
    except OSError:
        pass


def unmark_held(name):
    """Take one channel out of the record of what this plugin is driving.

    Called on exactly one occasion: the hardware layer refused the FIRST write to
    that channel with one of the codes that means it refused before touching
    anything (see NOTHING_WRITTEN). Without this, the restore that follows hands
    back a channel nobody ever wrote to - and handing back a channel with no
    saved original writes the chip's own full-output setting to it, which is
    loud, safe, and a lie about what happened. Measured, 2026-09-22: a first
    write refused with the helper's busy code still produced a release on that
    channel and a log line saying hardware fan control had been restored on it.
    """
    document = read_json(HELD) or {}
    channels = [item for item in (document.get('channels') or []) if item != name]
    if channels:
        write_json(HELD, {'at': document.get('at', time.time()), 'pid': os.getpid(),
                          'channels': channels})
    else:
        clear_held()
    return channels


def restore(hardware=None, quiet=False, take_lock=True):
    """Hand every channel we ever touched back to the hardware. The last word.

    Deliberately forgiving about everything except giving up: it restores from
    the captured baseline, it restores channels one at a time so that one
    stubborn channel cannot keep the others held, it retries, and when it
    genuinely cannot it leaves an incident file behind and says so loudly. It
    is never an error to call this when there is nothing to restore - that is
    the common case, and answering "there was nothing driven" is a good answer.
    """
    hardware = hardware or Hardware()
    baseline = read_json(BASELINE) or {}
    kept = baseline.get('channels') or {}
    held = (read_json(HELD) or {}).get('channels') or []

    # Nothing was ever written, so there is nothing to write back - and writing
    # a "restore" to a chip this plugin never touched would itself be a write
    # to a fan register on hardware nobody confirmed. The rule is not "restore
    # is always safe", it is "restore only undoes what we did".
    if not held:
        return {'status': 'ok', 'restored': [], 'failed': [],
                'detail': 'nothing was being driven by this plugin, so nothing was written.'}

    # Held, and no record of our own to show for it: somebody has removed a state
    # file from under a running controller, or the disk was full when it was
    # written. It is NOT a reason to stop here, and an earlier version of this
    # function had that wrong. The record that actually matters is the helper own,
    # written when it first set a duty on that channel, and release() uses it - so
    # the release still goes ahead, and the answer says whose record decided.
    without_record = not kept

    restored, failed = [], []
    for name in held:
        for attempt in range(3):
            try:
                with (ChipLock() if take_lock else NoLock()):
                    hardware.release(name)
                restored.append(name)
                break
            except Exception as failure:
                if attempt == 2:
                    failed.append({'channel': name, 'detail': str(failure)})
                else:
                    time.sleep(0.2)

    if failed:
        # The one outcome this plugin cannot fix by itself, and therefore the
        # one it must never hide. The fans may still be holding the last duty
        # this plugin wrote, and the only certain way back to firmware control
        # from here is a power cycle - a warm reboot does not necessarily reset
        # a Super I/O fan register.
        write_json(INCIDENT, {'at': time.time(), 'failed': failed, 'restored': restored})
        message = ('fan control could NOT be handed back for %s. The fans may still be held at '
                   'the last duty this plugin wrote. Power the appliance off and on to return '
                   'them to firmware control.' % ', '.join(item['channel'] for item in failed))
        log(message, syslog.LOG_CRIT)
        return {'status': 'failed', 'restored': restored, 'failed': failed, 'detail': message}

    clear_held()
    try:
        os.unlink(INCIDENT)
    except OSError:
        pass
    if not quiet and restored:
        log('hardware fan control restored on %s' % ', '.join(restored))
    detail = 'hardware fan control was restored on %s.' % ', '.join(restored)
    if without_record:
        detail += (' This plugin own record of what those channels were set to beforehand was '
                   'missing, so what they were restored to is whatever the helper had recorded '
                   'when it first set a duty on them - or, failing that, the chip own PWM-off, '
                   'which is full output.')
    return {'status': 'ok', 'restored': restored, 'failed': [], 'detail': detail}


def apply_duty(hardware, name, duty, verify=True):
    """Write one duty, then read it back and believe the read.

    The read-back is not a formality, and it is the second of two. fanctl-io
    checks its own write before it lets go of the window and refuses with its own
    exit code when the chip did not take it; this looks again, afterwards, from
    up here. Both exist because ncthwm takes no lock at all (D): if the kernel
    bank select lands between the helper index write and its data write, the byte
    goes into whichever register the kernel had just selected. The helper closes
    that window as far as anything can; looking again is what catches the case
    where something walked it anyway.

    A read-back that disagrees is not retried quietly - it means a write went
    somewhere, and the caller takes that as a reason to give the chip back.
    """
    with ChipLock():
        hardware.set_duty(name, duty)
        if not verify:
            return True, ''
        # The chip is given a moment: several of these parts latch a written duty
        # into the output stage on their own schedule rather than at the instant
        # of the write.
        time.sleep(0.2)
        try:
            seen = hardware.read(name).get('duty')
        except Refused as refusal:
            # A refusal on the way BACK is not a refusal of the write. The duty
            # went out; this is only the second look at what landed. It must not
            # leave this function as an exception either, because the caller reads
            # an exception from here as "nothing was written" and would take the
            # record of this write back off the channel - and then the restore
            # would not hand it back. Reported as a failed read-back instead,
            # which is what it is, and which hands the fans back.
            return False, ('%s: %d%% was written and then could not be read back: %s'
                           % (name, duty, refusal))

    if seen is None:
        return False, ('%s: the duty could not be read back, so there is no way to know what '
                       'was written.' % name)
    # A tolerance rather than equality, because a chip that counts 0-255 cannot
    # represent every percentage exactly: 60% is 153, and 153 back is 60.0%,
    # but 61% is 155.55 and comes back as either 60 or 61 depending on which
    # way the hardware layer rounded.
    if abs(int(seen) - int(duty)) > 2:
        return False, ('%s: %d%% was written and %d%% read back. Something else is writing to '
                       'this chip, or the write did not land where it was aimed.'
                       % (name, duty, seen))
    return True, ''


# ---------------------------------------------------------------------------
# The daemon
# ---------------------------------------------------------------------------

class Watchdog(object):
    """The last line, and the only one that works when the loop is wedged.

    Every other failure path in this file runs because some Python statement
    ran. This one runs when none of them does: a cycle that is stuck in a
    syscall, a lock that is never released, a hardware layer that blocks
    forever on a port read. SIGALRM arrives anyway, and the handler restores
    and leaves.

    The handler restores and then calls os._exit, which skips every finally
    block and every atexit hook on the way out. That is deliberate: the process
    has just proven it cannot be relied on to finish a loop, so it must not be
    relied on to unwind an exception through code it is already stuck in. The
    fans have been handed back by the time _exit is called, which is the whole
    of what has to be true.
    """

    def __init__(self, hardware, seconds):
        self.hardware = hardware
        self.seconds = float(seconds)
        self.fired = False

    def __enter__(self):
        signal.signal(signal.SIGALRM, self._fired)
        self.pet()
        return self

    def __exit__(self, *_):
        signal.setitimer(signal.ITIMER_REAL, 0)
        return False

    def pet(self):
        signal.setitimer(signal.ITIMER_REAL, self.seconds)

    def _fired(self, *_):
        # Not twice, whatever happens below. The itimer is one-shot, but a second
        # SIGALRM from anywhere at all would re-enter this handler in the middle
        # of the restore it is doing.
        signal.setitimer(signal.ITIMER_REAL, 0)
        if self.fired:
            os._exit(70)
        self.fired = True

        log('the control loop did not complete a cycle within %.0f seconds; handing the fans '
            'back and stopping' % self.seconds, syslog.LOG_CRIT)
        try:
            # Two things here that the first version of this handler had wrong,
            # both measured on 2026-09-22 against a fake chip:
            #
            #   * take_lock=False. The code this handler interrupted is very
            #     likely holding the chip lock - every write does, and a write is
            #     where a wedged cycle is most likely to be wedged. flock is per
            #     open file description, so this process cannot take a lock it is
            #     already holding: with the lock, the restore was refused three
            #     times over 15.4 s, no release was ever attempted, an incident
            #     was written, and the process exited 70 WITH THE DUTY STILL
            #     APPLIED. Without the lock, on the same test, the fan was handed
            #     back in 2 ms. Nothing is lost by not taking it: fanctl-io holds
            #     its own lock over the window, in its own process, and answers
            #     "busy" rather than deadlocking.
            #
            #   * the children first. If what wedged the cycle is a helper run
            #     that is not coming back, that helper still holds the window and
            #     the release below would be refused as busy. This process is
            #     about to _exit, so no child of ours has anything left to do.
            _kill_our_children()
            restore(self.hardware, take_lock=False)
        finally:
            os._exit(70)


def _kill_our_children():
    """SIGKILL every direct child of this process. The watchdog path only.

    The children this process ever has are helper runs and the odd sysctl, and by
    the time this is called it is milliseconds away from _exit. What it is for is
    the one child that matters: a fanctl-io that is not coming back, holding its
    own lock over the hardware-monitor window, which would make the release that
    follows fail as busy. A child wedged in an uninterruptible port access will
    not die even for SIGKILL, and then the incident file is the honest answer -
    but that is a far smaller set of cases than before, and no longer the common
    one.
    """
    try:
        done = subprocess.run(['/bin/pgrep', '-P', str(os.getpid())],
                              capture_output=True, text=True, timeout=5)
        pids = [int(item) for item in done.stdout.split() if item.strip().isdigit()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    killed = []
    for pid in pids:
        if pid <= 1:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except OSError:
            pass
    return killed


def _on_term(signum, _frame):
    """SIGTERM, SIGINT, SIGHUP and SIGQUIT: stop at the top of the next cycle.

    SIGHUP is in that list because of a measurement and not a theory
    (2026-09-22): with SIGTERM handled and SIGHUP not, a SIGHUP to a process
    holding a duty killed it at exit 129 with the duty still written, no release
    attempted, and held.json left behind. Nothing in this plugin sends SIGHUP -
    newsyslog signals the daemon(8) supervisor, which handles it itself and does
    not forward it (D, daemon(8): "-H ... Close output_file and re-open it when
    signal SIGHUP is received"; only SIGTERM is described as forwarded to the
    child) - but sshd sends it to everything in the session when a connection
    drops, and `fanctl.py test` is a command somebody runs over ssh while a fan
    is below its floor for the length of it. SIGQUIT is here for the same reason
    and costs nothing: a core dump is not worth a held fan.

    The flag rather than an immediate exit, so that a signal arriving in the
    middle of a chip conversation does not leave half a write behind. The loop
    tests it every time round and the restore runs in the finally below, which
    is the same path a clean stop takes - one restore path, exercised by every
    stop, rather than a special one that only runs when it is least tested.
    """
    global _stopping
    _stopping = True


def daemon():
    """Read, decide, apply, and above all put it back.

    Every exit from this function goes through the finally, and the finally
    restores. The paths that reach it, each of which exists for a failure
    somebody has actually had:

      SIGTERM / SIGINT   the service being stopped, and the shutdown sequence.
                         An appliance going down must not be left with a fan
                         under the control of a process that is about to not
                         exist.
      an exception       anything unforeseen. A fan controller that dies with a
                         traceback and leaves the fan where it put it is worse
                         than one that never started.
      a missing sensor   the temperature a decision is made from stopped
                         answering. There is no safe duty to infer from a
                         sensor that is not there, so the hardware - which has
                         its own sensors and its own law - gets it back.
      a failed read-back what was written is not what came back. Something else
                         is writing to this chip.
      a stalled fan      a channel we are driving stopped turning. Whatever we
                         believe about the floor is wrong.
      the panic point    the machine is above the panic temperature at full
                         duty. At that point this plugin has proven it is not
                         what is keeping the machine cool, and the most useful
                         thing it can do is stop being in the way.
      the watchdog       the loop stopped going round at all. See Watchdog.
    """
    options = settings()
    table = load_table()
    key, entry = match_model(table)
    hardware = Hardware()

    if not options['enabled']:
        log('not starting: fan control is turned off in the settings')
        return 1

    # Whatever the last run left behind, put it right before adding to it. A
    # held.json on disk at this moment is the signature of a run that was
    # killed rather than stopped.
    if read_json(HELD):
        log('the previous run did not hand the fans back; restoring before starting')
        restore(hardware, quiet=True)

    wanted = [name.strip().upper() for name in (options['channels'] or '').split(',')
              if name.strip()]

    governors = {}
    driving = []
    # What this process last wrote to each channel. Kept because a hardware
    # layer that cannot read a duty back - which is a legitimate thing for one
    # to be unable to do on a chip whose readback register is not documented -
    # would otherwise make every cycle look like a change and write to the chip
    # every ten seconds for ever. Fewer writes is the whole mitigation this
    # plugin has against the unlocked kernel reader, so it is not a nicety.
    written = {}
    gave_back = None
    # What is recorded in held.json, which is what a restore acts on. Built up one
    # channel at a time, immediately before that channel's own first write, and
    # never in advance of one: a channel named here that was never written to is a
    # channel a restore would hand back by writing the chip's full-output setting
    # to it. The ordering within a channel is unchanged and deliberate - the record
    # goes down before the write, so a kill between the two errs towards a record
    # of a write that did not happen rather than a write with no record.
    held_now = []
    handbacks = 0
    settle_until = 0.0

    def shape_for(floor_pct):
        """The curve, around one floor. Built here and not once at start, because
        the floor is the only number in this loop that comes from a measurement
        and a measurement can be superseded while the loop runs."""
        return curves.Curve(options['points'], floor=floor_pct, step=options['step'],
                            limit_c=options['limit_c'], panic_c=options['panic_c'])

    log('started in %s mode, deciding every %d seconds' % (options['mode'], options['interval']))

    try:
        with Watchdog(hardware, watchdog_seconds(options)) as watchdog:
            while not _stopping:
                watchdog.pet()
                cycle_started = time.monotonic()

                # Re-read rather than loaded once at start: the floors are the only
                # numbers in this plugin that come from the hardware, and a status
                # document that disagreed with the contract about them would be a page
                # arguing with itself.
                measured = read_json(MEASURED) or {}
                reading = sensors.read()
                found = observe(hardware, entry)
                channels = [name for name in wanted
                            if name in [item['name'] for item in found['channels']]]

                # The baseline is taken here, before the contract is judged,
                # and the ordering is not arbitrary: one of the contract's own
                # conditions is that a baseline exists, so capturing it after
                # the contract passed would be a condition that could never
                # become true. Capturing is a read - it writes nothing to the
                # chip - which is what makes it safe to do before the plugin
                # has earned the right to write anything.
                capture_problem = ''
                if options['mode'] != 'monitor' and channels and not driving \
                        and not baseline_covers(channels):
                    try:
                        # From the reading this cycle already took, so no second
                        # pass over the window.
                        capture_baseline(hardware, channels, found)
                    except Exception as failure:        # noqa: BLE001 - reported, not raised
                        capture_problem = str(failure)

                lines = contract(options, entry, found, measured, reading, channels,
                                 capture_problem)

                # Monitor mode never writes at all, which is what makes this
                # plugin installable on an appliance nobody has measured: the
                # page fills with real readings and the chip is never touched.
                #
                # The same branch is taken when a writing mode has nothing to
                # write to, and the two are NOT reported in the same words. A
                # page that said "watching" while the mode was set to follow a
                # curve would be describing a setting rather than what is
                # happening, and the reason is usually one the reader can fix:
                # no channel chosen, or a chip that could not be read this cycle.
                if options['mode'] == 'monitor' or not channels:
                    if driving:
                        restore(hardware)
                        driving = []
                        held_now = []
                        for governor in governors.values():
                            governor.forget()
                    if options['mode'] == 'monitor':
                        state = 'watching only, as the mode says'
                    elif not wanted:
                        state = 'no fan is chosen to control, so nothing is driven'
                    else:
                        state = ('none of the chosen fans could be read this cycle: %s'
                                 % (found.get('problem') or 'the chip reported no channels'))
                    write_status(options, key, entry, found, reading, lines, {}, state)
                    _sleep(options['interval'], cycle_started)
                    continue

                if not contract_ok(lines):
                    # The contract is checked every cycle precisely so that
                    # this can happen mid-run. Handing back on the first cycle
                    # where a condition stops holding is the entire safety
                    # argument of this plugin.
                    if driving:
                        broken = [line['name'] for line in lines if not line['ok']]
                        log('handing the fans back: %s' % '; '.join(
                            line['detail'] for line in lines if not line['ok']),
                            syslog.LOG_WARNING)
                        restore(hardware)
                        driving = []
                        held_now = []
                        for governor in governors.values():
                            governor.forget()

                        # And now the part that is not about this cycle. A
                        # hand-back CLEARS some of what the contract reads -
                        # held.json above all - so a condition that fails only
                        # while driving looks cured the moment it has been acted
                        # on, and the next cycle takes control again and fails
                        # again. Measured, 2026-09-22: exactly that, on a
                        # two-second interval, indefinitely. So each hand-back is
                        # counted and the next one is made to wait.
                        handbacks += 1
                        settle_until = time.monotonic() + max(
                            SETTLE_MIN, options['interval'] * SETTLE_CYCLES)
                        if handbacks > HANDBACKS_ALLOWED:
                            log('the fans have been handed back %d times in this run; the '
                                'contract is not holding, so this is where it stops rather '
                                'than going round again: %s'
                                % (handbacks, '; '.join(line['detail'] for line in lines
                                                        if not line['ok'])), syslog.LOG_ERR)
                            write_status(options, key, entry, found, reading, lines, {},
                                         'stopped after %d hand-backs (%s)'
                                         % (handbacks, ', '.join(broken)))
                            return 1
                        write_status(options, key, entry, found, reading, lines, {},
                                     'handed back (%s)' % ', '.join(broken))
                    else:
                        write_status(options, key, entry, found, reading, lines, {},
                                     'not driving')
                    _sleep(options['interval'], cycle_started)
                    continue

                if not driving and time.monotonic() < settle_until:
                    # The contract holds again, and that is not yet enough. See
                    # the hand-back above: it is the waiting that stops a fan
                    # alternating between two control laws all night.
                    write_status(options, key, entry, found, reading, lines, {},
                                 'the fans were handed back %d time(s) in this run; waiting '
                                 '%.0f more seconds for the contract to stay true before '
                                 'taking them again'
                                 % (handbacks, settle_until - time.monotonic()))
                    _sleep(options['interval'], cycle_started)
                    continue

                if not driving:
                    # No second capture here. The one taken above is what will
                    # be written back, and it is not re-read now for the reason
                    # in baseline_covers(): the control mode is the part that
                    # has to be exact, and the duty beside it is a number the
                    # chip recomputes for itself within a cycle of getting its
                    # own law back.
                    #
                    # held.json is NOT written here. It used to be, for the whole
                    # set of channels, before a single one of them had been
                    # written to - and a first write that was refused then left a
                    # restore handing back channels this plugin had never touched,
                    # which for a channel with no saved original means writing the
                    # chip's own full-output setting to it. It is written one
                    # channel at a time below instead, each time just before that
                    # channel's own first write.
                    driving = list(channels)
                    log('taking control of %s' % ', '.join(channels))

                decisions = {}
                trouble = ''

                for name in channels:
                    fan = next((item for item in found['channels'] if item['name'] == name), {})
                    floor, floor_source = floor_for(name, entry, options, measured)
                    if floor is None:
                        # Only reachable when the contract's floor line was carried
                        # by full_output_only(), because nothing else lets a channel
                        # with no measured floor be driven at all. The floor in
                        # force is then the lowest duty this configuration can
                        # write, and that is full output - which is the honest
                        # number to show on the page and to name in a stall report,
                        # rather than the owner's typed floor_duty dressed up as a
                        # measurement.
                        floor, floor_source = FULL_OUTPUT, FULL_OUTPUT_SOURCE
                    # Two floors, and the higher wins. The measured one is what
                    # the hardware can survive; the settings one is what the
                    # owner will put up with. Neither may be undercut, and the
                    # page cannot set the second below the first.
                    effective = max(int(floor), int(options['floor_duty']))

                    groups = fan.get('sensors') or sensors.groups_from(options['sensors'])
                    celsius, from_names, missing = sensors.decide(reading, groups)
                    if celsius is None:
                        trouble = ('%s: the temperature it follows (%s) is not being read.'
                                   % (name, ', '.join(groups)))
                        break

                    if name not in governors:
                        governors[name] = curves.Governor(
                            shape_for(effective), hysteresis_c=options['hysteresis_c'],
                            dwell_seconds=options['dwell_seconds'])
                    governor = governors[name]

                    # The floor is re-read from measured.json every cycle, so it
                    # can change under a running loop - and the curve that
                    # enforces it was built once, at the first cycle. Measured,
                    # 2026-09-22: with the measured floor raised from 40 to 95
                    # under a running loop, the status document the page reads
                    # said "duty=60 floor=95 measured on this machine" and went on
                    # holding 60% for the life of the daemon. A page showing one
                    # floor while another is enforced is the one thing this plugin
                    # must never do, so the shape is rebuilt and the governor's
                    # memory of what it applied and why is kept.
                    if int(governor.curve.floor) != int(effective):
                        log('the floor for %s has changed from %d%% to %d%% (%s); the curve '
                            'has been rebuilt around it'
                            % (name, int(governor.curve.floor), int(effective), floor_source))
                        governor.curve = shape_for(effective)

                    if governor.curve.over_panic(celsius):
                        trouble = ('%s reached %.1f C, above the panic point of %d C, while this '
                                   'plugin was driving the fans. Whatever it is doing is not '
                                   'keeping this machine cool.' % (name, celsius,
                                                                   options['panic_c']))
                        break

                    if options['mode'] == 'manual':
                        duty = max(effective, min(100, int(options['manual_duty'])))
                        # Two sentences, because one of them is a claim about what
                        # happened and the other is not. A duty that was raised to
                        # meet a floor is worth saying; a duty that was simply
                        # applied should not be reported as if the floor had moved
                        # it, which is what a single sentence with both numbers in
                        # it reads like when they are equal.
                        if int(options['manual_duty']) < int(effective):
                            reason = ('manual: %d%% was chosen on the settings page and raised '
                                      'to the floor of %d%%.'
                                      % (options['manual_duty'], effective))
                        else:
                            reason = ('manual: %d%% was chosen on the settings page.'
                                      % options['manual_duty'])
                    else:
                        duty, reason = governor.decide(celsius, time.time())

                    applied = fan.get('duty')
                    if applied is None:
                        applied = written.get(name)
                    if applied is None or int(applied) != int(duty):
                        # The record of the write goes down before the write, and
                        # only for a channel that is about to be written to. See
                        # held_now above for why not all of them at once.
                        if name not in held_now:
                            held_now.append(name)
                            mark_held(held_now)
                        try:
                            ok, complaint = apply_duty(hardware, name, duty)
                        except Refused as refusal:
                            # A refusal the hardware layer made before it touched
                            # anything leaves that channel exactly as it was, so
                            # the record of a write must come back off it -
                            # otherwise the restore below hands back a channel
                            # nobody wrote to. See NOTHING_WRITTEN.
                            if refusal.code in NOTHING_WRITTEN and name not in written:
                                held_now = unmark_held(name)
                                if name in driving:
                                    driving.remove(name)
                            raise
                        if not ok:
                            trouble = complaint
                            break
                        written[name] = duty
                        # The exact second of the change, which the sampler cannot
                        # know: it sees only that the duty was one thing a minute ago
                        # and another now, and a change that happened and reversed
                        # between two samples it would not see at all. Every
                        # before-and-after in the statistics is anchored here.
                        #
                        # `applied` is what the channel was at, and it is None on the
                        # first write of a run. history.py takes None and records the
                        # step without a from-duty rather than inventing one.
                        try:
                            history.note_duty(name, applied, duty, reason=reason)
                        except Exception as failure:    # noqa: BLE001 - logged
                            log('the duty change could not be recorded: %s' % failure,
                                syslog.LOG_WARNING)
                        # The fan is given time to answer before it is judged,
                        # and only a fan the table says should be turning is
                        # judged at all.
                        if fan.get('expected') == 'turning':
                            time.sleep(SPIN_SECONDS)
                            rpm = hardware_rpm(hardware, name, found)
                            if rpm is not None and rpm < STALL_RPM:
                                trouble = ('%s stopped turning at %d%%, which is above its floor '
                                           'of %d%%. The floor is wrong.'
                                           % (name, duty, effective))
                                break

                    decisions[name] = {
                        'duty': duty, 'celsius': celsius, 'from': from_names,
                        'floor': effective, 'floor_source': floor_source, 'reason': reason,
                        'missing': missing,
                    }

                if trouble:
                    log('handing the fans back: %s' % trouble, syslog.LOG_ERR)
                    restore(hardware)
                    driving = []
                    held_now = []
                    for governor in governors.values():
                        governor.forget()
                    write_status(options, key, entry, found, reading, lines, decisions,
                                 'handed back: %s' % trouble)
                    # Not a restart loop. Something is wrong that this plugin
                    # has no way to correct, and a daemon that took the fans
                    # back every ten seconds to fail the same way would be a
                    # fan oscillating between two control laws all night.
                    return 1

                write_status(options, key, entry, found, reading, lines, decisions, 'driving')
                _sleep(options['interval'], cycle_started)

    except Exception as failure:                        # noqa: BLE001 - deliberate
        log('the control loop failed: %s' % failure, syslog.LOG_ERR)
        return 1
    finally:
        # The one line that matters most in this file. Reached from a clean
        # stop, from an exception, and from a SIGTERM alike. It does not return
        # a value of its own: a return inside a finally discards whatever
        # brought the function here, including an exception on its way up, and
        # the outcome of the restore is carried out in a variable instead.
        gave_back = restore(hardware)
        log('stopped; hardware fan control is back' if gave_back['status'] == 'ok'
            else 'stopped, and the fans could NOT be handed back',
            syslog.LOG_NOTICE if gave_back['status'] == 'ok' else syslog.LOG_CRIT)

    return 0 if gave_back is not None and gave_back['status'] == 'ok' else 1


def hardware_rpm(hardware, name, found):
    """The tachometer for one channel, freshly read.

    No cheerful fallback to the sysctls when the read fails. Where chip.py is
    there, chip.py has already chosen the path and refusing is its answer; going
    round it would mean reading the kernel nodes for the same two ports the
    helper may be writing to at that moment, which is the race everything here is
    arranged to avoid. Where chip.py is absent, the last reading already came
    from the sysctls and there is nothing else to ask.
    """
    if hardware.present:
        try:
            with ChipLock():
                rpm = hardware.read(name).get('tach')
            return None if rpm is None else int(rpm)
        except Refused:
            return None
    return sysctl_tach().get(name, (found.get('tach') or {}).get(name))


def _sleep(interval, started):
    """Sleep out the rest of the cycle, in short naps.

    Short naps so that a SIGTERM is acted on within a fraction of a second: the
    flag is tested between them. A daemon that took ten seconds to notice it
    had been asked to stop would hold the fans for ten seconds into a shutdown.
    """
    deadline = started + interval
    while not _stopping and time.monotonic() < deadline:
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


# How often the daemon refreshes the chip's own temperature inputs for the status
# document. Not the decision interval, which is the owner's and defaults to ten
# seconds: this is a display cadence, it matches what the history samples at, and it
# is not a setting because nobody should be able to turn it into a per-cycle cost by
# typing a number into a page.
SUPERIO_STATUS_SECONDS = 60

_superio_cache = {'at': 0.0, 'entries': [], 'reason': 'not-asked'}


def superio_for_status(now=None):
    """The chip's own temperature inputs, for the document and never for the decision.

    Taken by the process that already holds the chip, through the same helper and
    the same lock as every other thing it does, so this adds no second reader - see
    the module docstring of sensors.read() for why the default is off in the loop,
    and DESIGN.md section 3 for why the page may not take its own.

    A refusal is cached exactly like a reading. A chip that has just said busy, asked
    again six times a minute, is how a plugin meant to be quiet becomes the noisiest
    thing in the log.

    Never raises. The fans do not depend on this and neither does the contract; it is
    a list of numbers for a page, and a page missing a number is not a reason for a
    control loop to stop going round.
    """
    now = time.time() if now is None else now
    if now - _superio_cache['at'] >= SUPERIO_STATUS_SECONDS:
        try:
            entries, reason = sensors.read_superio()
        except Exception as failure:        # noqa: BLE001 - a display, never a stop
            entries, reason = [], 'error: %s' % failure
        _superio_cache.update({'at': now, 'entries': entries, 'reason': reason})

    return _superio_cache['entries'], _superio_cache['reason']


def with_superio(reading):
    """A copy of the reading with the chip's own inputs in it, for writing out.

    A COPY, and that is the whole safety of this function: the caller's reading is
    what the decision was made from and what the contract was judged against, and
    nothing here may reach it. The entries arrive from sensors.read_superio() in the
    same shape as every other sensor, which is what lets one page draw one table.

    The group summary is extended the way sensors.read() extends it - the hottest
    reading in each group, never an average - so a board input cannot quietly lower a
    group another sensor is keeping high.
    """
    entries, reason = superio_for_status()
    if not entries:
        merged = dict(reading)
        merged['superio'] = reason
        return merged

    merged = dict(reading)
    merged['superio'] = reason
    merged['sensors'] = list(reading.get('sensors') or []) + list(entries)

    # Two lists and they are not interchangeable, which sensors.read() says in as
    # many words at the point it builds them: `unreadable` holds NAMES because the
    # page joins it into a sentence, and `unreadable_why` holds the objects. Putting
    # an object in the first one renders as "[object Object]" on the one line of the
    # page that appears exactly when something has gone wrong - which is what this
    # function did until the real page was looked at.
    unreadable = list(reading.get('unreadable') or [])
    unreadable_why = list(reading.get('unreadable_why') or [])
    groups = dict(reading.get('groups') or {})
    present = list(reading.get('present') or [])

    for item in entries:
        celsius = item.get('celsius')

        # The same rule sensors.read() applies to everything else in this list,
        # applied here because these entries arrive AFTER it has run: a reading
        # nobody can take is unknown, and a reading whose source the chip named
        # itself is named. Without this they came out with no confidence at all,
        # which on a page that prints the confidence beside every sensor is a
        # blank cell where the honesty is supposed to be.
        #
        # There is no tracker here, so nothing is ever promoted to measured or
        # demoted to constant. That needs a tracker kept for the life of the
        # process, which this daemon does not yet have, and half of one would be
        # worse than none.
        item['confidence'] = ('unknown' if celsius is None
                              else 'named' if item.get('named_by_driver')
                              else 'unknown')

        if celsius is None:
            unreadable.append(item.get('name'))
            unreadable_why.append({'name': item.get('name'),
                                   'why': item.get('unreadable', 'absent')})
            continue
        group = item.get('group') or 'other'
        if group not in present:
            present.append(group)
        if group not in groups or celsius > groups[group]:
            groups[group] = celsius

    merged['unreadable'] = unreadable
    merged['unreadable_why'] = unreadable_why
    merged['groups'] = groups
    merged['present'] = present
    return merged


def write_status(options, key, entry, found, reading, lines, decisions, state):
    """The document the page reads, written by the process that decided it.

    Written by the daemon rather than recomputed by whatever the page asks, and
    that is a safety property rather than a convenience: recomputing it would
    mean a second process reading this chip through the same two ports while
    the first one is writing to them.
    """
    # Re-read rather than passed in, so that every one of the five places this
    # is called from writes the same document without carrying an argument it
    # does not otherwise need. It is one small file once per cycle, and what it
    # buys is that a fan nobody is driving still shows its floor - which is the
    # number somebody staring at a page wants, because it is the one standing
    # between them and a fan they can drive.
    measured = read_json(MEASURED) or {}

    fans = []
    for channel in found['channels']:
        decision = decisions.get(channel['name']) or {}
        floor, floor_source = floor_for(channel['name'], entry, options, measured)
        fans.append({
            'channel': channel['name'],
            'label': channel.get('label') or '',
            'state': channel.get('state'),
            'rpm': channel.get('tach'),
            'duty': decision.get('duty', channel.get('duty')),
            'mode': channel.get('mode_name') or channel.get('mode'),
            'driven': channel['name'] in decisions,
            'floor': decision.get('floor', floor),
            'floor_source': decision.get('floor_source', floor_source),
            # The floor underneath ours, which fanctl-io enforces and nothing here
            # can lower. Carried so the page can show both and nobody has to guess
            # which number refused a duty.
            'helper_floor': channel.get('helper_floor'),
            'helper_floor_source': channel.get('helper_floor_source'),
            'output': channel.get('output'),
            'celsius': decision.get('celsius'),
            'from': decision.get('from') or [],
            'reason': decision.get('reason') or '',
            'confirmed': channel.get('confirmed') or '',
        })

    # Everything the machine will give up, for the document - which is a superset of
    # what the decision above was made from, and deliberately a different variable so
    # that nobody can later pass the wrong one to the part that decides.
    shown = with_superio(reading)

    write_json(STATUS, {
        'status': 'ok',
        'at': time.time(),
        'source': 'daemon',
        'state': state,
        'mode': options['mode'],
        'enabled': options['enabled'],
        'interval': options['interval'],
        'machine': dict(smbios(), model=key, display=entry.get('display', '')),
        'chip': {key: found.get(key) for key in
                 ('chip', 'id', 'index_port', 'data_port', 'hwm_base', 'writable', 'access',
                  'source', 'detail', 'problem', 'hard_floor', 'channel1_output',
                  'unavailable')},
        'contract': lines,
        'curve': curve_summary(options),
        'fans': fans,
        'temperatures': shown,
        'incident': read_json(INCIDENT),
    })

    # The history, fed from the reading this cycle already took. It reads nothing:
    # no second sysctl call, no second pass over the chip window, and above all no
    # second process talking to this chip while this one is driving it.
    # history.sample() records at most once a minute and returns immediately on the
    # five cycles out of six where nothing is due.
    #
    # Wrapped, and the wrapping is the point rather than caution. A record of what
    # the fans did is worth less than the fans: a full disk, a corrupt ring or a bug
    # in an arithmetic path may not be the reason this loop stops going round, so the
    # failure is logged once and the cycle carries on.
    try:
        history.sample(
            reading=shown,
            fans={channel['name']: {
                'rpm': channel.get('tach'),
                # The duty this cycle DECIDED where there is one, and the duty the
                # chip reported where there is not. A channel this plugin only
                # watches still has its duty recorded, which is what lets the history
                # see a SmartFan curve moving underneath it.
                'duty': (decisions.get(channel['name']) or {}).get(
                    'duty', channel.get('duty')),
                'driven': channel['name'] in decisions,
            } for channel in found.get('channels') or []},
            source='daemon')
    except Exception as failure:            # noqa: BLE001 - logged, never raised
        log('the history could not be updated: %s' % failure, syslog.LOG_WARNING)


# ---------------------------------------------------------------------------
# Holding a duty by hand: set and test
# ---------------------------------------------------------------------------

def hold(channel, duty, seconds, enforce_floor, watch_tach):
    """Apply one duty for a bounded time, then put it back. Both verbs use this.

    The two verbs differ in exactly two ways, and both differences are here:

      set   enforces the floor, because it is somebody choosing a fan speed.
      test  suspends it, because measuring where a fan stops is the one job
            that requires going below the floor - and it watches the
            tachometer while it does, so the measurement stops at the moment
            the fan does rather than a fixed number of seconds later.

    Neither may run while the daemon is running: the daemon owns the chip, and
    two writers on two ports with no lock between them and the kernel is the
    race this whole plugin is arranged around. Neither is allowed to be
    unsupervised either - this function does not return until the duty has been
    taken back off, and its signal handlers take it back off early.
    """
    global _stopping

    if daemon_pid():
        raise Refused('the fan control service is running and owns the chip. Stop it before '
                      'setting a duty by hand.')

    options = settings()
    table = load_table()
    key, entry = match_model(table)
    hardware = Hardware()
    measured = read_json(MEASURED) or {}

    found = observe(hardware, entry)
    if not found.get('writable'):
        raise Refused('the hardware layer reports no writable path to this chip%s'
                      % (': ' + found['problem'] if found.get('problem') else '.'))

    ok, complaints = fingerprint(entry, found)
    if not ok and not options['accept_unmeasured']:
        raise Refused('this machine does not match the hardware table: %s' % ' '.join(complaints))
    # And when it does not match but the owner has accepted that, the answer says
    # which of the two let it through. accept_unmeasured is described everywhere
    # else in this plugin as satisfying the permission line of the contract; here
    # it also waives the fingerprint, which is the only way a machine with no
    # table entry can ever have a floor measured on it - that is deliberate, and
    # it is the sort of thing that must be in the answer rather than in a comment.
    waived = ''
    if not ok:
        waived = ('the chip on this machine does not match the hardware table (%s). It was '
                  'written to anyway because the settings page has "accept an unmeasured '
                  'entry" turned on.' % ' '.join(complaints))

    names = [item['name'] for item in found['channels']]
    if channel not in names:
        raise Refused('%s is not a channel on this chip. It has: %s.'
                      % (channel, ', '.join(names) or 'none'))

    duty = int(duty)
    # 1 and not 0. fanctl-io refuses zero outright - "zero is not a duty" - and it
    # is right: a duty of nothing is not a slow fan, it is a stopped one asked for
    # in a way that looks like a number. The helper floor is far above this
    # anyway; this bound only stops a nonsense value reaching it.
    if not 1 <= duty <= 100:
        raise Refused('a duty is a percentage between 1 and 100.')

    if enforce_floor:
        floor, source = floor_for(channel, entry, options, measured)
        if floor is None and duty >= FULL_OUTPUT:
            # The same rule the contract keeps, kept here too so the two cannot
            # disagree: full output needs no measurement, because it cannot stop a
            # turning fan. A hold at anything less still does, and says so.
            floor, source = FULL_OUTPUT, FULL_OUTPUT_SOURCE
        if floor is None:
            raise Refused(source)
        effective = max(int(floor), int(options['floor_duty']))
        if duty < effective:
            raise Refused('%d%% is below the floor of %d%% (%s). The floor is not a preference.'
                          % (duty, effective, source))

    # The floor test is the one caller allowed below that floor, and it still has
    # a floor under it that this plugin cannot move: the one compiled into
    # fanctl-io, raised by /usr/local/etc/fanctl/floor.conf where somebody has
    # written a measurement into it. The helper refuses below that and names it,
    # and the refusal is passed through as it arrives - so a test that stops at
    # the helper floor reads as "this is as low as I am allowed to go", which is
    # the truth, and lowering it is an edit to a file on the machine by the person
    # who owns the fan.
    here = next((item for item in found['channels'] if item['name'] == channel), {})
    if not enforce_floor and here.get('helper_floor') is not None \
            and duty < int(here['helper_floor']):
        raise Refused('%d%% is below the floor fanctl-io itself will allow on %s, which is %d%% '
                      '(%s). Nothing above the helper can lower that. To measure below it, write '
                      'the measurement into /usr/local/etc/fanctl/floor.conf on this machine.'
                      % (duty, channel, int(here['helper_floor']),
                         here.get('helper_floor_source') or 'the compiled-in hard floor'))

    # The lower bound is different for the two verbs, and the reason is a
    # measurement. The tachometer is not judged for the first SPIN_SECONDS after a
    # write - a fan is a lump of metal with inertia - so a watched test shorter
    # than that cannot notice a stall at all. Measured, 2026-09-22: `test` for 3
    # seconds on a fan that stopped dead at the instant of the write recorded
    # every one of its three samples as 0 rpm and then wrote down a floor of 35%
    # for a duty of 30%, "measured on this machine". A test that cannot fail is
    # not a measurement, so one is not offered: the length is raised to something
    # that can, and the answer reports the length actually used.
    seconds = max(int(SPIN_SECONDS) + 4 if watch_tach else 3, min(60, int(seconds)))

    # The flag first, then the handlers. The other way round - which is how this
    # was - leaves a window in which a signal sets the flag and the line below
    # clears it again, and the fan is then held for the full length of the test by
    # a process that was asked to stop.
    _stopping = False
    for received in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(received, _on_term)

    samples = []
    stopped = False
    complaint = ''
    ok = False

    with Watchdog(hardware, seconds + 30):
        try:
            capture_baseline(hardware, [channel], found)
            mark_held([channel])
            try:
                ok, complaint = apply_duty(hardware, channel, duty)
            except Refused as refusal:
                # Refused before anything was touched, so this channel is exactly
                # as it was found and the record of a write has to come back off
                # it: the restore in the finally below would otherwise hand back a
                # channel nobody wrote to, which for a channel with no saved
                # original means writing the chip's own full-output setting to it.
                if refusal.code in NOTHING_WRITTEN:
                    unmark_held(channel)
                raise
            if ok:
                applied_at = time.monotonic()
                deadline = applied_at + seconds
                while not _stopping and time.monotonic() < deadline:
                    time.sleep(1.0)
                    rpm = hardware_rpm(hardware, channel, found)
                    # Whether this sample is old enough to mean anything, decided
                    # from the moment of the write rather than reconstructed from
                    # the deadline. Carried on the sample itself because it is
                    # what the verdict below is allowed to look at.
                    settled = (time.monotonic() - applied_at) >= SPIN_SECONDS
                    samples.append({'at': time.time(), 'rpm': rpm, 'settled': settled})
                    if watch_tach and settled and rpm is not None and rpm < STALL_RPM:
                        # The measurement, taken and then ended. Leaving a
                        # stopped fan stopped for the rest of the test would be
                        # measuring the same fact twice at the machine's
                        # expense.
                        stopped = True
                        break
        finally:
            result = restore(hardware)

    # What this test is entitled to claim. Three answers and not two, because
    # "not seen to stop" is not the same as "seen to keep turning": a test cut
    # short by a signal, one whose tachometer could not be read, and one too
    # short to have judged the tach at all, all end with stopped == False, and
    # every one of them used to be written down as a duty this fan survives.
    judged = [item for item in samples if item['settled'] and item['rpm'] is not None]
    if stopped:
        verdict = 'stopped'
    elif len(judged) >= 2 and all(item['rpm'] >= STALL_RPM for item in judged):
        verdict = 'turning'
    else:
        verdict = 'inconclusive'

    answer = {
        'status': 'ok' if (ok and result['status'] == 'ok') else 'failed',
        'channel': channel,
        'duty': duty,
        'seconds': seconds,
        'samples': samples,
        'stopped': stopped,
        'verdict': verdict,
        'restored': result['status'] == 'ok',
        'detail': ' '.join(part for part in (complaint or result['detail'], waived) if part),
    }

    if watch_tach:
        answer.update(record_measurement(channel, duty, samples, verdict, options))
        if verdict == 'inconclusive':
            answer['detail'] = ((answer['detail'] + ' ') if answer['detail'] else '') + (
                'This test is not a measurement of anything: the fan was not seen to stop and '
                'was not seen to keep turning either - %d of %d samples could be judged. '
                'Nothing was written down about the floor. Run it again with the fan reading a '
                'speed before it starts.' % (len(judged), len(samples)))
    return answer


def record_measurement(channel, duty, samples, verdict, options):
    """Write down what the floor test saw, and what floor it now supports.

    The floor kept is one step above the lowest duty that did NOT stop the fan,
    never the lowest itself. A fan that keeps turning at 30% in a cool room
    with a clean bearing is not promising to keep turning at 30% in August with
    three years of dust in it, and one step is the cheapest insurance in this
    plugin.

    A FLOOR IS ONLY EVER RAISED BY A TEST THAT SAW THE FAN TURNING. It takes a
    verdict and not a boolean for that reason, and the reason is a measurement:
    this took `stopped` before, so "was not seen to stop" became "was seen to
    keep turning", and a three-second test of a fan that stopped dead - three
    samples, every one of them 0 rpm - wrote down a floor of 35% for a duty of
    30% and dated it (V, 2026-09-22). The evidence of the stall was sitting in
    the same record, in lowest_rpm, next to the sentence denying it.

    It is worth being exact about what this measures, because the name invites
    the wrong reading: this is the duty at which a fan ALREADY TURNING keeps
    turning. It is not the duty at which a stopped fan starts, which is higher
    on every fan ever made and cannot be measured without stopping the fan
    first - which this plugin will not do on a firewall that is in service.
    """
    measured = read_json(MEASURED) or {'channels': {}}
    channels = measured.setdefault('channels', {})
    record = channels.setdefault(channel, {'tests': []})

    seen = [sample['rpm'] for sample in samples if sample.get('rpm') is not None]
    record['tests'].append({'at': time.time(), 'duty': duty, 'verdict': verdict,
                            'stopped': verdict == 'stopped',
                            'lowest_rpm': min(seen) if seen else None,
                            'samples': len(samples)})
    record['tests'] = record['tests'][-20:]

    def kind(test):
        # A record written before this function took verdicts says only whether
        # the fan was seen to stop, and nothing about how long it was watched
        # for. It keeps the meaning it had when it was written, because this
        # version cannot go back and watch that fan again; only tests taken by
        # this version are held to the rule above.
        return test.get('verdict') or ('stopped' if test.get('stopped') else 'turning')

    survived = [test['duty'] for test in record['tests'] if kind(test) == 'turning']
    failed = [test['duty'] for test in record['tests'] if kind(test) == 'stopped']

    # Recorded whether or not anything survived. A test that stopped the fan is
    # a measurement too - the most valuable one in this file, because it is the
    # only one taken at the boundary - and the first version of this function
    # threw it away unless some other test had succeeded, which left a fan with
    # a floor below a duty that had demonstrably stopped it.
    #
    # stalled_at is the duty that actually stopped it, kept so that the sentence
    # the page shows can quote a measurement instead of reconstructing one by
    # subtracting a step that may not be the step it was taken with.
    record['stalled_at'] = max(failed) if failed else None
    record['not_below'] = (max(0, min(100, max(failed) + int(options['step'])))
                           if failed else None)
    if survived:
        record['floor'] = max(0, min(100, min(survived) + int(options['step'])))
        record['at'] = time.time()

    write_json(MEASURED, measured)
    return {'floor': record.get('floor'), 'not_below': record.get('not_below'),
            'stalled_at': record.get('stalled_at'), 'verdict': verdict,
            'measured_at': record.get('at'), 'tests': record['tests']}


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def probe():
    """What is here, what the table says, and whether the two agree. Reads only."""
    options = settings()
    table = load_table()
    key, entry = match_model(table)
    hardware = Hardware()
    measured = read_json(MEASURED) or {}
    # Everything this machine will give up, and not the subset the control loop
    # runs on. sensors.read() leaves both of these off by default because the
    # loop runs six times a minute and neither is free - a drive reading is an
    # ATA command, and a super-I/O reading is another pass over a window shared
    # with a kernel driver that holds no lock. This is the other side of that
    # decision: somebody has opened the page, once, and what they came for is
    # every sensor the machine has rather than the three the curve is watching.
    #
    # On this appliance it is also the only place the root cause is visible. The
    # two PECI agents the board's own fan curve follows are enabled in the chip
    # and have never reported anything, which is why that curve asked for 20%
    # duty with the cores at 80 C - and a reading nobody can see is a finding
    # nobody has.
    reading = sensors.read(include_drives=True, include_superio=True)
    found = observe(hardware, entry)

    ok, complaints = fingerprint(entry, found)
    channels = [name.strip().upper() for name in (options['channels'] or '').split(',')
                if name.strip()]

    floors = {}
    for channel in found['channels']:
        floor, source = floor_for(channel['name'], entry, options, measured)
        floors[channel['name']] = {'floor': floor, 'source': source}

    return {
        'status': 'ok',
        'at': time.time(),
        'machine': dict(smbios(), model=key, display=entry.get('display', '')),
        'chip': found,
        'fingerprint': {'ok': ok, 'complaints': complaints},
        'table': {
            'control': bool(entry.get('control')),
            'reason': entry.get('control_reason') or '',
            'hint': entry.get('hint') or '',
            'notes': entry.get('notes') or [],
            'curve': entry.get('curve') or {},
        },
        'floors': floors,
        'contract': contract(options, entry, found, measured, reading, channels),
        'temperatures': reading,
        'incident': read_json(INCIDENT),
        'hardware_layer': {'present': hardware.present, 'detail': hardware.problem},
    }


def status():
    """The live picture, for the page.

    Where the daemon is running its own document is the answer, because it is
    the process that made the decisions and the only one that should be talking
    to the chip. Where it is not, this reads the machine itself - read-only -
    so that the page is just as informative with the service stopped, which is
    the state every new installation is in.
    """
    running = daemon_pid()
    document = read_json(STATUS)
    if running and document and (time.time() - document.get('at', 0)) < STATUS_MAX_AGE:
        document['service'] = {'running': True, 'pid': running}
        return document

    options = settings()
    table = load_table()
    key, entry = match_model(table)
    hardware = Hardware()
    measured = read_json(MEASURED) or {}
    # Everything this machine will give up, and not the subset the control loop
    # runs on. sensors.read() leaves both of these off by default because the
    # loop runs six times a minute and neither is free - a drive reading is an
    # ATA command, and a super-I/O reading is another pass over a window shared
    # with a kernel driver that holds no lock. This is the other side of that
    # decision: somebody has opened the page, once, and what they came for is
    # every sensor the machine has rather than the three the curve is watching.
    #
    # On this appliance it is also the only place the root cause is visible. The
    # two PECI agents the board's own fan curve follows are enabled in the chip
    # and have never reported anything, which is why that curve asked for 20%
    # duty with the cores at 80 C - and a reading nobody can see is a finding
    # nobody has.
    reading = sensors.read(include_drives=True, include_superio=True)
    found = observe(hardware, entry)
    channels = [name.strip().upper() for name in (options['channels'] or '').split(',')
                if name.strip()]
    lines = contract(options, entry, found, measured, reading, channels)

    fans = []
    for channel in found['channels']:
        floor, source = floor_for(channel['name'], entry, options, measured)
        fans.append({
            'channel': channel['name'],
            'label': channel.get('label') or '',
            'state': channel.get('state'),
            'rpm': channel.get('tach'),
            'duty': channel.get('duty'),
            'mode': channel.get('mode_name') or channel.get('mode'),
            'driven': False,
            'floor': floor,
            'floor_source': source,
            'helper_floor': channel.get('helper_floor'),
            'helper_floor_source': channel.get('helper_floor_source'),
            'output': channel.get('output'),
            'celsius': None,
            'from': [],
            'reason': '',
            'confirmed': channel.get('confirmed') or '',
        })

    # The real age, not the threshold. This said "has not written a reading for
    # 30 seconds" whatever the age was, because it printed STATUS_MAX_AGE - a
    # sentence that states a measurement and carries a constant instead.
    age = time.time() - (document or {}).get('at', 0) if document else None
    stale = (('the service is running but its last reading is %.0f seconds old, so these were '
              'taken by this page instead.' % age) if age is not None else
             ('the service is running but has not written a reading at all yet, so these were '
              'taken by this page instead.')) if running else ''

    return {
        'status': 'ok',
        'at': time.time(),
        'source': 'direct',
        'state': stale if running else ('the service is not running, so nothing is being '
                                        'driven and these readings were taken by this page.'),
        'mode': options['mode'],
        'enabled': options['enabled'],
        'interval': options['interval'],
        'machine': dict(smbios(), model=key, display=entry.get('display', '')),
        'chip': {name: found.get(name) for name in
                 ('chip', 'id', 'index_port', 'data_port', 'hwm_base', 'writable', 'access',
                  'source', 'detail', 'problem', 'hard_floor', 'channel1_output',
                  'unavailable')},
        'contract': lines,
        'curve': curve_summary(options),
        'fans': fans,
        'temperatures': reading,
        'incident': read_json(INCIDENT),
        'service': {'running': bool(running), 'pid': running},
    }


def start():
    """Start the daemon under daemon(8), and wait until it is really up."""
    ensure_dirs()
    if daemon_pid():
        return {'status': 'ok', 'detail': 'the fan control service is already running.'}

    options = settings()
    if not options['enabled']:
        return {'status': 'failed',
                'detail': 'fan control is turned off in the settings, so nothing was started.'}

    # Whatever the last run left, before a new one adds to it.
    if read_json(HELD):
        restore()

    command = [DAEMON_BIN, '-f', '-t', 'fanctl',
               '-p', PID, '-P', SUPERVISOR, '-o', LOG, '-H',
               SELF, 'daemon']
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as failure:
        return {'status': 'failed', 'detail': 'could not start: %s' % failure}
    if done.returncode != 0:
        return {'status': 'failed',
                'detail': 'could not start: %s' % ((done.stderr or done.stdout).strip()
                                                   or 'exit %d' % done.returncode)}

    # daemon(8) writes the pidfile after it has forked, so the file is not
    # there the instant the command returns. Polled rather than slept through:
    # a flat one second is both too long to wait when it worked and too short
    # when the machine is busy, which is how a start that succeeded gets
    # reported as a failure.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        pid = daemon_pid()
        if pid:
            return {'status': 'ok', 'pid': pid, 'detail': 'the fan control service is running.'}
        time.sleep(0.1)

    return {'status': 'failed',
            'detail': 'the service did not stay up. Its last words: %s' % tail(LOG, 6)}


def stop():
    """Stop the daemon, then make sure the fans really were handed back.

    The second half is not belt and braces for its own sake. A daemon that had
    to be killed never ran its finally, and the state files are then the only
    record that anything was ever driven - so a stop always ends with a restore
    whether or not the process appeared to go quietly.
    """
    pid = daemon_pid()
    supervisor = read_pid(SUPERVISOR)

    for target in (supervisor, pid):
        if target:
            try:
                os.kill(target, signal.SIGTERM)
            except OSError:
                pass

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if not daemon_pid() and not read_pid(SUPERVISOR):
            break
        time.sleep(0.2)

    killed = []
    for path in (SUPERVISOR, PID):
        target = read_pid(path)
        if target:
            try:
                os.kill(target, signal.SIGKILL)
                killed.append(target)
            except OSError:
                pass
    for path in (PID, SUPERVISOR):
        try:
            os.unlink(path)
        except OSError:
            pass

    result = restore()
    if killed:
        log('the daemon did not answer SIGTERM and was killed (%s)'
            % ', '.join(str(item) for item in killed), syslog.LOG_WARNING)
        result['detail'] = ('the service had to be killed; %s' % result['detail'])
    return result


def restart():
    stop()
    return start()


def tail(path, lines=10):
    try:
        with open(path, 'r') as handle:
            return ' / '.join(handle.read().splitlines()[-lines:])
    except OSError:
        return ''


def diagnostics():
    """probe and status in one answer, so the page makes one round trip."""
    return {'status': 'ok', 'probe': probe(), 'state': status()}


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _print(document):
    json.dump(document, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write('\n')


def main(argv):
    ensure_dirs()
    verb = argv[1] if len(argv) > 1 else 'status'

    if verb == 'daemon':
        # SIGHUP and SIGQUIT alongside the two obvious ones. Neither is sent by
        # anything in this plugin - newsyslog signals the daemon(8) supervisor,
        # which handles SIGHUP itself and forwards only SIGTERM (D) - and both
        # kill an unprepared process outright, with a duty applied and nothing
        # left running that knows it is there. See _on_term.
        for received in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(received, _on_term)
        return daemon()

    try:
        if verb == 'probe':
            _print(probe())
        elif verb == 'status':
            _print(status())
        elif verb == 'diagnostics':
            _print(diagnostics())
        elif verb == 'restore':
            result = restore()
            _print(result)
            return 0 if result['status'] == 'ok' else 1
        elif verb == 'start':
            result = start()
            _print(result)
            return 0 if result['status'] == 'ok' else 1
        elif verb == 'stop':
            result = stop()
            _print(result)
            return 0 if result['status'] == 'ok' else 1
        elif verb == 'restart':
            result = restart()
            _print(result)
            return 0 if result['status'] == 'ok' else 1
        elif verb in ('set', 'test'):
            # configd fills a %s the caller left out with an empty string and quotes
            # whatever arrives, so this is reached with empty arguments rather than with
            # missing ones. Each is converted here, where the reason can be a sentence:
            # an int() that raises inside hold() would be reported to a page as "fanctl
            # failed: invalid literal for int()", which tells the reader nothing.
            if len(argv) < 4:
                _print({'status': 'failed',
                        'detail': 'usage: fanctl.py %s <channel> <percent> [seconds]' % verb})
                return 2
            channel = str(argv[2]).strip().upper()
            if not channel:
                _print({'status': 'failed', 'detail': 'no channel was named.'})
                return 2
            try:
                duty = int(str(argv[3]).strip())
            except ValueError:
                _print({'status': 'failed',
                        'detail': '"%s" is not a percentage.' % argv[3]})
                return 2
            try:
                seconds = int(str(argv[4]).strip()) if len(argv) > 4 and str(argv[4]).strip() \
                    else 15
            except ValueError:
                _print({'status': 'failed',
                        'detail': '"%s" is not a number of seconds.' % argv[4]})
                return 2
            result = hold(channel, duty, seconds,
                          enforce_floor=(verb == 'set'), watch_tach=(verb == 'test'))
            _print(result)
            return 0 if result['status'] == 'ok' else 1
        else:
            _print({'status': 'failed', 'detail': 'unknown command "%s".' % verb})
            return 2
    except Refused as refusal:
        _print({'status': 'failed', 'detail': str(refusal)})
        return 1
    except Exception as failure:                        # noqa: BLE001 - deliberate
        # Nothing reaches here having written a duty: every write in this file
        # is inside a function that restores on its way out. Reported as a
        # refusal rather than as a traceback, because configd hands this
        # straight to a page.
        _print({'status': 'failed', 'detail': 'fanctl failed: %s' % failure})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
