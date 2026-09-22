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

    WHICH TEMPERATURE DECIDES, ON A MACHINE NOBODY HAS SEEN.

    curve.py turns a temperature into a duty: interpolation, rounding up to a
    step, hysteresis and a dwell on the way down, a limit above which the curve
    is not consulted. None of that needs to know what kind of machine it is on.
    THIS file is the part that does: it stands between the sensors that were
    discovered on this appliance and the curve, and it answers three questions
    that only have answers once you know what sensors exist.

      1. WHICH sensor decides? The hottest of the ones asked for, never an
         average - one core at 85 C and three at 40 C averages to something a
         curve is happy with, and the machine still has a core at 85 C. That
         rule is sensors.decide()'s and this file keeps it.

      2. Hot COMPARED TO WHAT? This is the question that does not survive
         contact with unknown hardware. 70 C is most of the way to a CPU core's
         limit and nowhere near a chipset's. On the reference appliance the
         numbers are measured and they are not close to each other (V, sysctl,
         2026-09-22):

             dev.cpu.0.coretemp.tjmax     100.0 C    the cores' own limit
             dev.pchtherm.0.ctt           115.0 C    the chipset's catastrophic trip
             hw.acpi.thermal.tz0._CRT     119.1 C    the board zone's critical point
             hw.acpi.thermal.tz0._ACx[0]   71.1 C    where the FIRMWARE wanted full fans

         So this file reads each sensor's own limit out of the same tree the
         reading came from, and asks a second question beside the curve's: not
         "how hot is it" but "how much is left". THE ANSWER CAN ONLY EVER MAKE
         A FAN FASTER OR HAND THE FANS BACK. It never replaces the temperature
         the curve reads and it never lowers a duty, and that constraint is the
         whole design - a rule worked out from numbers nobody here has checked,
         on a machine nobody here has seen, must not be able to slow a fan
         down.

         A first draft of this file did let it decide the number the curve
         reads, by converting a margin into an "equivalent temperature". Run
         against the real sensor tree it turned a CPU sitting comfortably at
         56 C with 44 C of headroom into 75.1 C for the curve, because the
         hottest LIMIT in the set belonged to a different sensor entirely (V,
         2026-09-22). The arithmetic was defensible and the number was
         nonsense, which is exactly the kind of cleverness a fan controller
         cannot afford. It was removed rather than corrected.

      3. When does this plugin STOP being the thing that cools the machine?
         Above the panic point the fans go back to the hardware and the daemon
         exits, because a machine still climbing at full duty has proved that
         the curve is not what is keeping it cool. Beside that absolute rule
         sits the discovered one: any sensor within PANIC_MARGIN of its OWN
         limit hands the fans back, whatever the absolute number says. Both are
         judged against every chosen sensor and not only against the one that
         won the curve - a chipset three degrees from its trip point is a
         reason to get out even on a cycle where a CPU core decided the duty.

    WHAT THIS FILE DOES NOT DECIDE

    The floor. It never has, and the arrangement is deliberate in three layers:
    fanctl-io has a compiled hard floor of 30% and a file that can only raise
    it (D, FANCTL_HARD_FLOOR_PCT, /usr/local/etc/fanctl/floor.conf); above that
    sits the floor MEASURED on this machine; above that sits the owner's own
    floor from the settings page, which can only raise it further. A duty this
    file computes below the helper's floor is refused BY THE HELPER, named, and
    passed up as it arrives. Nothing here clamps it quietly, and nothing here
    can lower it.

    WHAT IS MEASURED HERE AND WHAT IS NOT

    V: the sysctl names, the four limit values quoted above, that the reference
    machine's ACPI zone 0 carries five active-cooling trip points while zone 1
    carries none, and that on this machine the discovered rule reproduces the
    shipped settings exactly - tjmax 100.0 C gives full duty at 70 C and a
    hand-back at 85 C, which are the limit_c and panic_c a person chose by
    hand. That agreement is the only check available on the arithmetic, and it
    is a check on the arithmetic rather than on the cooling.
    R: that the same names appear on other Intel platforms - they come from
    coretemp(4), pchtherm(4) and acpi_thermal(4), not from this board - which
    is why they are looked up by pattern rather than hard-coded per machine.
    Nothing here has been run on a second appliance.
"""

import re
import subprocess

import curve

# How close to a sensor's OWN limit is close enough to stop pretending this
# plugin is in charge. Ten degrees is not a measurement; it is the same spirit
# as the shipped panic point of 85 C against a tjmax of 100 C, expressed in a
# form that transfers to a sensor whose limit is 115.
PANIC_MARGIN_C = 15.0

# And where full duty is demanded, in the same form.
LIMIT_MARGIN_C = 30.0

# The critical point for a sensor is looked up by rewriting its own sysctl
# name. Each rule is (pattern for the reading, replacement, what the value is).
# Several are tried in order and the FIRST that answers wins, because a chip
# that publishes both a catastrophic trip and a lower throttle point should be
# judged against the lower one.
_LIMIT_RULES = (
    # coretemp(4): the cores' junction maximum, per CPU (V: 100.0C).
    (re.compile(r'^dev\.cpu\.(\d+)\.temperature$'),
     ['dev.cpu.{0}.coretemp.tjmax'], 'the junction maximum this CPU reports'),
    # pchtherm(4): t0temp is the first throttle trip, ctt the catastrophic one
    # (V: 108.0C and 115.0C). The throttle point is preferred - it is where the
    # chipset starts defending itself, which is where a fan should already be
    # working.
    (re.compile(r'^dev\.pchtherm\.(\d+)\.temperature$'),
     ['dev.pchtherm.{0}.t0temp', 'dev.pchtherm.{0}.ctt'],
     'the trip point this chipset reports'),
    # acpi_thermal(4): _CRT is the zone's critical point, at which the FIRMWARE
    # would shut the machine down (V: 119.1C).
    (re.compile(r'^hw\.acpi\.thermal\.(tz\d+)\.temperature$'),
     ['hw.acpi.thermal.{0}._HOT', 'hw.acpi.thermal.{0}._CRT'],
     'the critical point the firmware set for this zone'),
)

_CELSIUS = re.compile(r'^(-?\d+(?:\.\d+)?)\s*C$')
_NUMBER = re.compile(r'^(-?\d+(?:\.\d+)?)$')

PLAUSIBLE_LOW = 20.0            # a "limit" below this is not a limit
PLAUSIBLE_HIGH = 200.0


def _sysctl(names):
    """{name: raw} for the names that exist. One call, whatever the exit status.

    sysctl(8) exits non-zero when ANY name is unknown while still printing
    every one that is not, and on a machine nobody has seen most of these names
    will not exist. Discarding the output on a non-zero exit would mean one
    absent sensor took every present one with it.
    """
    if not names:
        return {}
    try:
        done = subprocess.run(['/sbin/sysctl', '-e'] + list(names),
                              capture_output=True, text=True, timeout=10)
        text = done.stdout or ''
    except (OSError, subprocess.SubprocessError):
        return {}
    values = {}
    for line in text.splitlines():
        name, _, value = line.partition('=')
        if name.strip():
            values[name.strip()] = value.strip()
    return values


def _celsius(raw):
    """A limit in degrees, or None. Same two spellings sensors.py accepts.

    A value outside the plausible band is dropped rather than used: acpi(4)
    prints -1 for a trip point the firmware did not set (V: every _ACx in zone
    1 on the reference machine), and a fan curve that treated -1 C as a limit
    would run every fan at full speed for ever.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    match = _CELSIUS.match(text)
    if match is not None:
        value = float(match.group(1))
    else:
        match = _NUMBER.match(text)
        if match is None:
            return None
        number = float(match.group(1))
        value = (number / 10.0) - 273.15 if number > 200 else number
    if value < PLAUSIBLE_LOW or value > PLAUSIBLE_HIGH:
        return None
    return round(value, 1)


def limits_for(names):
    """{sensor name: {'celsius': limit, 'from': sysctl, 'what': sentence}}.

    Discovered, not assumed. A sensor with no limit anywhere in the tree simply
    has none here, and everything downstream treats that as "this one cannot be
    judged against a limit of its own" rather than inventing a number for it.
    """
    plan = {}
    for name in names:
        for pattern, templates, what in _LIMIT_RULES:
            match = pattern.match(name)
            if match is None:
                continue
            plan[name] = ([template.format(match.group(1)) for template in templates], what)
            break

    raw = _sysctl(sorted({candidate for templates, _ in plan.values()
                          for candidate in templates}))

    found = {}
    for name, (templates, what) in plan.items():
        # IN THE ORDER THE TEMPLATES WERE WRITTEN, which is the order of
        # preference, and not in whatever order the sysctl names happen to
        # sort. The first version of this loop walked the names alphabetically
        # and so preferred dev.pchtherm.0.ctt - the catastrophic trip at 115.0
        # C - over dev.pchtherm.0.t0temp, the throttle point at 108.0 C, which
        # is the one a fan should be judged against (V, both read on the
        # reference machine, 2026-09-22). Alphabetical order is not a
        # preference; it only looked like one.
        for candidate in templates:
            value = _celsius(raw.get(candidate))
            if value is not None:
                found[name] = {'celsius': value, 'from': candidate, 'what': what}
                break
    return found


def firmware_trips():
    """What the vendor firmware thought, where it left an opinion behind.

    Returns {zone: [trip points, hottest first]} from the ACPI active-cooling
    trips - the highest is where the firmware wanted the fans at full. On the
    reference appliance zone 0 carries 71.1, 55.1, 50.1, 45.1 and 40.1 C (V),
    which is a fan curve written by the people who built the box, sitting in a
    sysctl nobody reads. Read for the page and for propose(), and never applied
    to anything: those trips were written for the fan the appliance shipped
    with, and on this machine that fan has been replaced.
    """
    zones = {}
    raw = _sysctl(['hw.acpi.thermal'])
    for name, value in raw.items():
        match = re.match(r'^hw\.acpi\.thermal\.(tz\d+)\._ACx$', name)
        if match is None:
            continue
        points = [point for point in
                  (_celsius(part) for part in str(value).split()) if point is not None]
        if points:
            zones[match.group(1)] = sorted(points, reverse=True)
    return zones

# ---------------------------------------------------------------------------
# Choosing the temperature that decides
# ---------------------------------------------------------------------------

def _group_of(entry):
    """The group a reading belongs to, from the reading or from its name.

    sensors.py puts a group on every entry. A future sensors.py that discovers
    a family nobody here anticipated may not, and this file is not the place to
    lose a temperature over a missing key.
    """
    if entry.get('group'):
        return str(entry['group'])
    name = str(entry.get('name') or '')
    for prefix, group in (('dev.cpu.', 'cpu'), ('dev.pchtherm.', 'pch'),
                          ('hw.acpi.thermal.', 'acpi'), ('dev.nvme.', 'nvme')):
        if name.startswith(prefix):
            return group
    return 'other'


def candidates(reading, wanted=None):
    """The readings a decision may be made from, as (entries, missing groups).

    `wanted` is a list of group names; None means every group that was
    discovered. A group that was asked for and is not present is NOT silently
    dropped - a decision made from three of the four sensors somebody chose is
    a different decision, and the caller is told so it can fail safe.
    """
    entries = [entry for entry in (reading.get('sensors') or [])
               if entry.get('celsius') is not None]
    if wanted is None:
        return entries, []
    wanted = [str(group).strip().lower() for group in wanted if str(group).strip()]
    chosen = [entry for entry in entries if _group_of(entry) in wanted]
    missing = [group for group in wanted
               if not any(_group_of(entry) == group for entry in entries)]
    return chosen, missing


def choose(reading, wanted=None):
    """Which temperature the curve reads, and why that one.

    The hottest of the chosen sensors, in degrees, never an average. That is
    sensors.decide()'s rule and this file keeps it unchanged, because it is the
    rule that has actually been run on hardware.

    Returns a dict and never a bare number: the sentence a page shows six weeks
    later has to name the sysctl it came from. `celsius` is None when nothing
    could be chosen, and every caller treats that as a reason to fail safe
    rather than as a cool machine.
    """
    chosen, missing = candidates(reading, wanted)
    if not chosen:
        return {'celsius': None, 'from': [], 'missing': missing,
                'detail': ('no temperature the decision needs is being read right now%s.'
                           % (': %s' % ', '.join(missing) if missing else ''))}

    hottest = max(entry['celsius'] for entry in chosen)
    names = [entry['name'] for entry in chosen if entry['celsius'] == hottest]
    return {'celsius': hottest, 'from': names, 'missing': missing,
            'detail': '%.1f C, the hottest of %d readings, from %s.'
                      % (hottest, len(chosen), ', '.join(names))}


# ---------------------------------------------------------------------------
# What the machine says about its own limits
# ---------------------------------------------------------------------------

def urgency(reading, wanted=None, limits=None, limit_margin=LIMIT_MARGIN_C,
            panic_margin=PANIC_MARGIN_C):
    """How close any chosen sensor is to ITS OWN limit, and what that demands.

    THIS IS THE ONLY PART OF THIS FILE THAT ADAPTS TO UNKNOWN HARDWARE, and it
    is arranged so that it can only ever make a fan faster or hand the fans
    back. It never lowers a duty and it never replaces the temperature the
    curve reads. That constraint is the whole design: a rule worked out from
    numbers nobody here has checked, on a machine nobody here has seen, must
    not be able to slow a fan down.

      'normal'    nothing is near a limit it published for itself.
      'limit'     something is within `limit_margin` of its own limit: full
                  duty, at once, whatever the curve would have said.
      'handback'  something is within `panic_margin` of its own limit: this
                  plugin is not what is keeping the machine cool. The fans go
                  back to the hardware.

    A sensor that publishes no limit is not judged here at all - it is carried
    in `unjudged` and left to the ordinary curve, which is the cautious way
    round: the alternative is inventing a limit for it.

    On the reference appliance this rule changes nothing, and that is the best
    evidence available that its arithmetic is sane (V, measured 2026-09-22):
    the CPU cores publish tjmax 100.0 C, so 'limit' lands on 70 C and
    'handback' on 85 C - exactly the shipped limit_c and panic_c, which were
    chosen by a person. The chipset publishes 115.0 C and the ACPI zones 119.1
    C, both of which are looser than the absolute panic point of 85, so on this
    machine the absolute rule is the one that bites. On an appliance whose CPU
    is not an i5-6500, it is the other way round.
    """
    chosen, missing = candidates(reading, wanted)
    if not chosen:
        return {'level': 'handback', 'sensor': None, 'missing': missing, 'unjudged': [],
                'detail': ('no temperature is being read at all, so there is no honest duty '
                           'to hold.')}

    limits = limits if limits is not None else limits_for(
        [entry['name'] for entry in chosen])
    unjudged = [entry['name'] for entry in chosen if entry['name'] not in limits]

    worst = None
    for entry in chosen:
        limit = (limits.get(entry['name']) or {}).get('celsius')
        if limit is None:
            continue
        room = limit - entry['celsius']
        if worst is None or room < worst[0]:
            worst = (room, entry, limit)

    if worst is None:
        return {'level': 'normal', 'sensor': None, 'missing': missing, 'unjudged': unjudged,
                'detail': ('not one of these sensors publishes a limit of its own, so there '
                           'is nothing here to judge them against. The curve decides alone.')}

    room, entry, limit = worst
    common = ('%s reads %.1f C and publishes a limit of %.1f C for itself (%s), which leaves '
              '%.1f C' % (entry['name'], entry['celsius'], limit,
                          limits[entry['name']]['from'], room))

    if room <= panic_margin:
        return {'level': 'handback', 'sensor': entry['name'], 'room_c': round(room, 1),
                'limit_c': limit, 'missing': missing, 'unjudged': unjudged,
                'detail': ('%s. That is inside the %.0f C at which this plugin stops '
                           'pretending to be what cools this machine: the fans go back to '
                           'the hardware.' % (common, panic_margin))}
    if room <= limit_margin:
        return {'level': 'limit', 'sensor': entry['name'], 'room_c': round(room, 1),
                'limit_c': limit, 'missing': missing, 'unjudged': unjudged,
                'detail': ('%s. That is inside the %.0f C at which full duty is demanded '
                           'whatever the curve says.' % (common, limit_margin))}
    return {'level': 'normal', 'sensor': entry['name'], 'room_c': round(room, 1),
            'limit_c': limit, 'missing': missing, 'unjudged': unjudged,
            'detail': '%s, which is the least room any of these sensors has.' % common}


def handback(reading, panic_c, wanted=None, limits=None, use_limits=True,
             panic_margin=PANIC_MARGIN_C):
    """(True, sentence) when this plugin should stop driving and get out.

    Two rules, and whichever fires first wins. The absolute one is the shipped
    panic point, in degrees, judged against EVERY chosen sensor rather than
    against the one that won the curve: a chipset three degrees from its trip
    point is a reason to get out even on a cycle where a CPU core decided the
    duty. The discovered one is urgency() above.
    """
    chosen, _ = candidates(reading, wanted)
    if not chosen:
        return True, ('no temperature is being read at all, so there is no honest duty to '
                      'hold. The fans go back to the hardware.')

    if panic_c is not None:
        for entry in chosen:
            if entry['celsius'] >= float(panic_c):
                return True, ('%s reached %.1f C, at or above the panic point of %.0f C. The '
                              'fans go back to the hardware and this plugin stops: a machine '
                              'still climbing at full duty has proved it is not this plugin '
                              'keeping it cool.'
                              % (entry['name'], entry['celsius'], float(panic_c)))

    if use_limits:
        near = urgency(reading, wanted, limits, panic_margin=panic_margin)
        if near['level'] == 'handback':
            return True, near['detail']
    return False, ''


# ---------------------------------------------------------------------------
# One channel's policy
# ---------------------------------------------------------------------------

class ChannelPolicy(object):
    """A curve, a memory and a sensor group, for one fan.

    Per channel and not per machine, because the sensor group is per channel in
    the hardware table and the floor is per channel by measurement. The SHAPE
    is still shared - one curve for every driven fan - which DESIGN.md names as
    a thing this version does not cover, and which is not quietly covered here
    either.

    THE FLOOR IS NOT THIS CLASS'S. It is passed in, it comes from a
    measurement, and underneath it sits a floor no Python in this plugin can
    move: fanctl-io's compiled 30% raised by /usr/local/etc/fanctl/floor.conf
    (D, FANCTL_HARD_FLOOR_PCT). A duty computed here below that is refused by
    the helper, by name, and the refusal is passed up as it arrives rather than
    being quietly rounded away.
    """

    def __init__(self, name, points, floor, sensors=None, step=5, limit_c=None,
                 panic_c=None, hysteresis_c=4.0, dwell_seconds=30.0, use_limits=True):
        self.name = name
        self.sensors = list(sensors) if sensors else None
        self.use_limits = bool(use_limits)
        self.shape = curve.Curve(points, floor=floor, step=step, limit_c=limit_c,
                                 panic_c=panic_c)
        self.governor = curve.Governor(self.shape, hysteresis_c=hysteresis_c,
                                       dwell_seconds=dwell_seconds)
        self.dwell_seconds = max(0.0, float(dwell_seconds))
        # How long a demand for full duty outlives the reading that caused it.
        # Without this, a sensor hovering either side of its own limit margin
        # would swing the fan between 100% and the curve every cycle - the
        # hunting that rule 3 in curve.py exists to prevent, arriving by a
        # different door because this demand does not go through the Governor's
        # hysteresis at all.
        self._urgent_until = 0.0

    def set_floor(self, floor):
        """Follow a floor that moved under a running daemon.

        fanctl.py re-reads the measured floors every cycle - they are the only
        numbers in this plugin that come from hardware - and a Curve built on
        the first cycle would otherwise keep the floor it was born with.
        """
        self.shape.set_floor(floor)

    def decide(self, reading, now, limits=None):
        """(duty or None, record). None means: do not drive, and the record says why.

        The record is what fanctl.py writes into the status document the page
        reads, so it carries the sysctl names as well as the duty. A fan that
        got louder at half past three is a question somebody asks on Thursday,
        and "because dev.cpu.2.temperature read 68.4 C" is an answer.
        """
        picked = choose(reading, self.sensors)
        record = {'channel': self.name, 'sensors': picked['from'],
                  'missing': picked['missing'], 'celsius': picked['celsius'],
                  'why_sensor': picked['detail']}

        if picked['missing']:
            record['reason'] = ('a temperature this channel follows is not being read: %s. '
                                'Nothing is driven from a sensor group that is absent.'
                                % ', '.join(picked['missing']))
            return None, record
        if picked['celsius'] is None:
            record['reason'] = picked['detail']
            return None, record

        stop, why = handback(reading, self.shape.panic_c, self.sensors, limits,
                             self.use_limits)
        if stop:
            record['handback'] = True
            record['reason'] = why
            return None, record

        duty, reason = self.governor.decide(picked['celsius'], now)

        if self.use_limits:
            near = urgency(reading, self.sensors, limits)
            record['urgency'] = near['level']
            record['urgency_detail'] = near['detail']
            if near['level'] == 'limit':
                self._urgent_until = float(now) + self.dwell_seconds
            if float(now) < self._urgent_until and duty < 100:
                held = round(self._urgent_until - float(now), 1)
                record['duty'] = 100
                record['reason'] = (
                    '100%% is applied whatever the curve asked for (%d%%): %s%s'
                    % (duty, near['detail'],
                       '' if near['level'] == 'limit' else
                       ' It is held for another %.0f s so that a sensor sitting on the edge '
                       'of that margin cannot swing this fan between 100%% and the curve '
                       'every cycle.' % held))
                return 100, record

        record['duty'] = duty
        record['reason'] = reason
        return duty, record

    def forget(self):
        """Throw the memory away after hardware control has been handed back.

        Both memories: the Governor's, and the demand for full duty. A plugin
        that took the fans back and immediately applied 100% because of a
        reading from before the handback would be acting on the past.
        """
        self.governor.forget()
        self._urgent_until = 0.0


# ---------------------------------------------------------------------------
# A proposal, marked as a proposal
# ---------------------------------------------------------------------------

def propose(reading, wanted=None):
    """Limit and panic points worked out from what this machine publishes.

    A PROPOSAL. Nothing applies it: it is what the Hardware tab shows beside
    the numbers in the settings, so that somebody on an appliance nobody has
    ever opened can see where the machine's own limits are before choosing. The
    arithmetic is shown rather than hidden, because a number somebody cannot
    check is a number they have to trust.
    """
    chosen, missing = candidates(reading, wanted)
    names = [entry['name'] for entry in chosen]
    limits = limits_for(names)
    trips = firmware_trips()

    known = sorted((item['celsius'], name) for name, item in limits.items())
    proposal = {'limits': [{'sensor': name, 'limit_c': item['celsius'],
                            'from': item['from'], 'what': item['what']}
                           for name, item in sorted(limits.items())],
                'firmware_active_trips': trips,
                'missing': missing,
                'sensors_without_a_limit': [name for name in names if name not in limits]}

    if not known:
        proposal.update({'limit_c': None, 'panic_c': None,
                         'detail': ('not one of the sensors on this machine publishes a limit '
                                    'of its own, so there is nothing to work a proposal out '
                                    'from. The shipped defaults are what is in use, and they '
                                    'were chosen on a machine whose CPU limit is 100 C.')})
        return proposal

    lowest, owner = known[0]
    proposal.update({
        'limit_c': int(round(lowest - LIMIT_MARGIN_C)),
        'panic_c': int(round(lowest - PANIC_MARGIN_C)),
        'detail': ('the lowest limit any sensor here publishes is %.1f C, on %s. %d C below '
                   'it is %d C, which is where full duty would be demanded; %d C below it is '
                   '%d C, which is where the fans would go back to the hardware. This is '
                   'arithmetic on what the machine says about itself, not a measurement of '
                   'what this chassis can cool - nobody has run this appliance hot with a '
                   'curve on it.'
                   % (lowest, owner, int(LIMIT_MARGIN_C), int(round(lowest - LIMIT_MARGIN_C)),
                      int(PANIC_MARGIN_C), int(round(lowest - PANIC_MARGIN_C)))),
    })
    if trips:
        hottest = max(points[0] for points in trips.values())
        proposal['firmware_detail'] = (
            'The firmware on this board left its own active-cooling trip points behind: the '
            'highest is %.1f C. That is where the VENDOR wanted the fans at full, with the '
            'fan the appliance shipped with. It is worth knowing and it is not a setting: a '
            'replaced fan moves a different amount of air at the same duty.' % hottest)
    return proposal


if __name__ == '__main__':
    import json
    import sys
    import time

    import sensors

    reading = sensors.read()
    wanted = sensors.groups_from(sys.argv[1]) if len(sys.argv) > 1 else None

    document = {
        'chosen': choose(reading, wanted),
        'urgency': urgency(reading, wanted),
        'proposal': propose(reading, wanted),
        'handback': dict(zip(('stop', 'why'), handback(reading, 85, wanted))),
    }
    try:
        policy = ChannelPolicy('FAN1', '40:60,50:75,60:90,70:100', floor=40,
                               sensors=wanted, limit_c=70, panic_c=85)
        duty, record = policy.decide(reading, time.monotonic())
        document['decision'] = {'duty': duty, 'record': record}
    except curve.CurveError as failure:
        document['decision'] = {'duty': None, 'error': str(failure)}

    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')
