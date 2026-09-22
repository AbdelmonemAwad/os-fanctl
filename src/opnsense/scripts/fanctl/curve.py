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

    Temperature in, duty out. Four rules and nothing else:

      1. Between the points of the curve the duty is interpolated; below the
         first point it is the first point's duty, above the last it is the
         last point's.

      2. The answer is rounded UP to a step, 5% by default. Most of what a
         temperature does in a firewall is wobble by a degree, and a duty that
         followed every degree would be a write to the chip every ten seconds
         for ever. Rounding up rather than to nearest is the whole of the
         safety argument for quantising at all: the fan is never slower than
         the curve asked for.

      3. Going up is immediate. Coming down waits: the temperature has to fall
         a whole hysteresis below the one that justified the current duty, AND
         a dwell time has to pass. This is what stops a fan hunting - the
         audible fault everybody who has ever written a fan curve has shipped
         once - and it is deliberately asymmetric, because the cost of being
         late to speed up is measured in degrees and the cost of being late to
         slow down is measured in noise.

      4. At or above the limit temperature the curve is not consulted at all:
         the duty is 100 and it is applied at once, whatever the dwell and the
         hysteresis would otherwise have said. There is a second temperature
         above it, the panic point, and this file does not handle that one - it
         reports it, and fanctl.py hands the fans back to the hardware, because
         a machine that is still climbing at full duty has proven that this
         plugin is not the thing keeping it cool.

    And one habit that is not a rule: every duty comes back with what decided
    it - the curve, the floor or the limit - because the sentence the page shows
    is read by somebody asking why a fan is loud, and "the curve asks for 55%"
    printed over a number the floor chose answers a different question.

    Every refusal in here is a CurveError, which carries a sentence. Nothing
    leaves as a bare ValueError out of int() or float(): fanctl.py catches
    CurveError around the page's copy of the curve, and a ValueError walked
    straight through that handler (measured, with floor='').

    Nothing in here touches hardware and nothing in here knows what a fan is.
    That is why it can be exercised on a workstation: python3 curve.py 45 will
    print what the shipped curve would do at 45 C.
"""

import math
import re

# Rounding up to this by default. The page offers 1 to 25 - FanCtl.xml's
# MinimumValue and MaximumValue, and settings() in fanctl.py clamps the same
# pair - and a step above about 10 makes a curve with four points into a
# staircase with two, which is a caution for whoever types one, not a bound.
DEFAULT_STEP = 5

# As many points as FanCtl.xml's mask allows: one pair plus (,pair){1,7}.
# DESIGN.md section 9 says the Python copy of a bound is to be the narrower of
# the two, and this file was the wider one in two ways - no count at all, and a
# leading minus the mask does not permit.
MAX_POINTS = 8

# A point as the settings page stores one: 55:80, degrees before the colon and
# percent after it. Written this way rather than as a grid in the model because
# a curve is read back by a Python daemon that must find it in config.xml
# without a model to help, and because four numbers are easier to read in one
# line than in four rows. No sign, because the mask has none either.
_POINT = re.compile(r'^\s*(\d{1,3})\s*:\s*(\d{1,3})\s*$')


class CurveError(ValueError):
    """A curve that cannot be used. Always carries a sentence for the page."""


def _real(value, what):
    """value as a finite float, or CurveError.

    Every conversion in this file goes through here or through _whole below.
    int('') and float('hot') raise ValueError, and ValueError is NOT caught by
    `except curves.CurveError` - measured: Curve(..., floor='') took a
    ValueError straight through curve_summary()'s handler, and what should have
    been a sentence on the page arrived as "the control loop failed".
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CurveError('%s must be a number: "%s" is not.' % (what, value)) from None
    if not math.isfinite(number):
        # A temperature that is not a number is not a cool machine. fanctl.py
        # already treats an unreadable sensor as a reason to hand the fans
        # back; this is the same fact arriving in a different shape.
        raise CurveError('%s must be a real number, and "%s" is not one.' % (what, value))
    return number


def _whole(value, what):
    """value as an int, or CurveError. A fraction is refused, not truncated.

    int(40.5) silently makes a different curve from the one that was written
    down, and the difference is a degree of temperature or a percent of duty.
    """
    number = _real(value, what)
    if number != int(number):
        raise CurveError('%s must be a whole number: "%s" is not.' % (what, value))
    return int(number)


def _bounded(value, low, high, what):
    """value as an int inside [low, high], clamped rather than refused.

    Clamped because every caller in this plugin passes a number settings() has
    already clamped to the same range, and a second refusal here would turn a
    value nobody can type on the page into a daemon that will not start.
    """
    return max(low, min(high, int(_real(value, what))))


def parse_points(text):
    """'40:60,50:75' as [(40, 60), (50, 75)], or CurveError with the reason.

    Every refusal here is a refusal the settings page has already made in the
    model, and this is the second of the two places that check. The daemon
    reads config.xml directly - no model, no validation - so a config.xml
    restored from a backup, or edited by hand, arrives here unchecked. A curve
    that cannot be parsed must stop the daemon from driving rather than be
    silently repaired into something nobody chose.

    A sequence of pairs is accepted too, and goes through exactly the same
    refusals. It used to be waved through untouched - `points if
    isinstance(points, list)` - which made a list the one way to hand this file
    a curve that falls as it heats, the single mistake in a fan curve that is
    obeyed exactly. Measured: [(30, 90), (60, 10)] was accepted and answered
    20% at 60 C. Nothing in the plugin passes a list; the hole was open anyway.
    """
    if text is None or isinstance(text, str):
        pairs = []
        for part in str(text or '').split(','):
            if not part.strip():
                continue
            match = _POINT.match(part)
            if match is None:
                if part.strip().startswith('-'):
                    raise CurveError('"%s" is not a point: a curve does not go below 0 C. Below '
                                     'the first point, the first point owns the duty.'
                                     % part.strip())
                raise CurveError('"%s" is not a point: write it as degrees:percent, such as 50:75.'
                                 % part.strip())
            pairs.append((match.group(1), match.group(2)))
    else:
        try:
            pairs = [(pair[0], pair[1]) for pair in text]
        except (TypeError, IndexError, KeyError):
            raise CurveError('A curve is either "40:60,50:75" or a sequence of '
                             '(degrees, percent) pairs.') from None

    points = []
    for celsius, duty in pairs:
        celsius = _whole(celsius, 'A temperature')
        duty = _whole(duty, 'A duty')
        if not 0 <= celsius <= 999:
            raise CurveError('A curve point is between 0 and 999 C: %d is not.' % celsius)
        if not 0 <= duty <= 100:
            # 0 is accepted here and is not a stopped fan: the floor below
            # decides what a 0 becomes, and under the floor fanctl-io refuses
            # anything beneath its own compiled one and does not clamp.
            raise CurveError('A duty is a percentage: %d is not between 0 and 100.' % duty)
        points.append((celsius, duty))

    if len(points) < 2:
        raise CurveError('A curve needs at least two points.')
    if len(points) > MAX_POINTS:
        raise CurveError('A curve is at most %d points; this one has %d.'
                         % (MAX_POINTS, len(points)))

    for before, after in zip(points, points[1:]):
        if after[0] <= before[0]:
            raise CurveError('The points must rise in temperature: %d C comes after %d C.'
                             % (after[0], before[0]))
        if after[1] < before[1]:
            # A curve that falls as it gets hotter is always a mistake in the
            # typing, and it is the one mistake that would be obeyed exactly.
            raise CurveError('The duty may not fall as the temperature rises: '
                             '%d%% at %d C is below %d%% at %d C.'
                             % (after[1], after[0], before[1], before[0]))

    return points


class Curve(object):
    """The shape, with no memory. Governor below is what remembers."""

    def __init__(self, points, floor, step=DEFAULT_STEP, limit_c=None, panic_c=None):
        # floor has no default, deliberately. It used to default to 0, and
        # Curve('20:0,80:100').duty(20) was then 0 - measured - which is not a
        # slow fan but a stopped one. All three callers in this plugin already
        # had a floor to pass; the default existed only to be forgotten by the
        # next one. A floor of 0 is still legal where somebody writes it, and
        # means only that this file adds no floor of its own: fanctl-io still
        # refuses below its compiled 30% (D, FANCTL_HARD_FLOOR_PCT).
        self.points = parse_points(points)
        self.floor = _bounded(floor, 0, 100, 'The floor')
        self.step = _bounded(step, 1, 25, 'The step')
        self.limit_c = None if limit_c is None else _real(limit_c, 'The limit temperature')
        self.panic_c = None if panic_c is None else _real(panic_c, 'The panic temperature')

        if self.limit_c is not None and self.panic_c is not None and self.panic_c <= self.limit_c:
            raise CurveError('The panic temperature must be above the limit temperature.')

    def set_floor(self, floor):
        """Move the floor under a curve that is already running.

        fanctl.py re-reads measured.json every cycle - "the floors are the only
        numbers in this plugin that come from the hardware" - and recomputes the
        effective floor every cycle, but the Curve is built once, on the first
        cycle a channel is driven, and kept the floor it was born with. A floor
        that rose under a running daemon was therefore written into the status
        document the page reads and never applied to the duty. The Governor
        needs no telling: a higher floor raises what the curve asks for, and
        rule 3 applies a rise at once.
        """
        self.floor = _bounded(floor, 0, 100, 'The floor')

    def _up(self, duty):
        """duty rounded UP to a whole step, before the floor and before 100.

        Up, never to nearest. See rule 2: the fan is allowed to be louder than
        the curve asked for and is never allowed to be quieter.
        """
        return int(-(-_real(duty, 'A duty') // self.step) * self.step)

    def quantise(self, duty):
        """Up to the next step, then held to the floor and to 100."""
        return max(self.floor, min(100, self._up(duty)))

    def raw(self, celsius):
        """The curve's own answer, before quantising and before the floor."""
        celsius = _real(celsius, 'A temperature')
        first, last = self.points[0], self.points[-1]
        if celsius <= first[0]:
            return float(first[1])
        if celsius >= last[0]:
            return float(last[1])
        for (t0, d0), (t1, d1) in zip(self.points, self.points[1:]):
            if t0 <= celsius <= t1:
                if t1 == t0:
                    return float(d1)
                return d0 + (d1 - d0) * (celsius - t0) / float(t1 - t0)
        # Unreachable while the points rise and celsius is a real number, both
        # of which are enforced above. Left as the belt it is, and it answers
        # with the hottest duty on the curve rather than the coolest.
        return float(last[1])

    def answer(self, celsius):
        """(duty, what decided it, what the curve alone asked for).

        Three things rather than one, because the reason a person reads has to
        name which of the three it was. The Governor used to be handed the duty
        alone and wrote "the curve asks for 55%" over a number the floor had
        chosen - measured, on a curve asking 15% under a floor of 55% - which is
        a false sentence in exactly the case where somebody is most likely to be
        reading it: a fan running faster than the curve explains.
        """
        celsius = _real(celsius, 'A temperature')
        if self.limit_c is not None and celsius >= self.limit_c:
            return 100, 'limit', 100
        asked = min(100, self._up(self.raw(celsius)))
        if asked < self.floor:
            return self.floor, 'floor', asked
        return asked, 'curve', asked

    def duty(self, celsius):
        """What the curve asks for at this temperature, ready to apply."""
        return self.answer(celsius)[0]

    def over_panic(self, celsius):
        return self.panic_c is not None and _real(celsius, 'A temperature') >= self.panic_c

    def sample(self, low=20, high=90, step=5):
        """The curve as a small table, for drawing on the settings page.

        Drawn from the same code that drives the fan rather than redrawn in
        JavaScript from the points: a picture that agrees with the numbers on
        the page but not with the daemon would be worse than no picture. It
        agrees about the shape, about the rounding and about the cliff at the
        limit - and about THIS floor. fanctl.py draws the page's curve with the
        floor from the settings page, so a channel whose measured floor is
        higher runs above the picture. That is the one difference a reader has
        to be told rather than left to find.

        `step` here is the stride of the table and not the curve's rounding step
        - the caller passes 2 - and it is forced to at least 1 because a stride
        of 0 ran this loop for ever (measured: still going after 6 s) and a
        negative one did the same. Nothing in the plugin passes either; an
        unbounded loop inside a file a fan controller imports is worth one max().
        """
        stride = max(1, abs(_whole(step, 'The sample stride')))
        table = []
        celsius = _real(low, 'The lowest temperature to sample')
        ceiling = _real(high, 'The highest temperature to sample')
        while celsius <= ceiling and len(table) < 400:
            table.append({'celsius': celsius, 'duty': self.duty(celsius)})
            celsius += stride
        return table


def _asks(duty, decided_by, asked, celsius, still=False):
    """The clause that names who chose a duty, in that one's own words.

    See Curve.answer(): the floor is never called the curve.
    """
    if decided_by == 'floor':
        return ('the floor %sholds this fan at %d%% at %.1f C, where the curve asks for %d%%'
                % ('still ' if still else '', duty, celsius, asked))
    return 'the curve %sasks for %d%% at %.1f C' % ('still ' if still else '', duty, celsius)


class Governor(object):
    """One fan's memory: what was applied, why, and when.

    Rule 3 lives here. It needs three things remembered between cycles - the
    duty applied, the temperature that justified it, and the moment it was
    applied - and it needs them per fan, because two fans following different
    sensor groups come down at different times.
    """

    def __init__(self, curve, hysteresis_c=4.0, dwell_seconds=30.0):
        self.curve = curve
        self.hysteresis_c = max(0.0, _real(hysteresis_c, 'The hysteresis'))
        self.dwell_seconds = max(0.0, _real(dwell_seconds, 'The dwell'))
        self.duty = None
        self.basis_c = None
        self.applied_at = 0.0

    def forget(self):
        """Throw the memory away.

        Called whenever hardware control has been given back. The next decision
        after that must be made from the temperature at that moment and not
        held down by a duty the chip is no longer being told to hold.
        """
        self.duty = None
        self.basis_c = None
        self.applied_at = 0.0

    def decide(self, celsius, now):
        """(duty, reason) for this temperature at this moment.

        celsius must be a number. A caller with no temperature has no business
        asking this: there is no safe duty to infer from a sensor that is not
        answering, and fanctl.py hands the fans back to the hardware instead.

        now must come from a clock that cannot run backwards - time.monotonic(),
        which is what the loop already paces its own cycles on. With time.time()
        an ntpd step backwards makes the dwell unsatisfiable for the length of
        the step: measured, a clock moved back an hour held the fan at 90% where
        the curve allowed 75% and said "only -3600 s of the 30 s dwell have
        passed". Held high is the safe direction, which is why that is a wrong
        sentence and not a hot machine - but it was wrong for an hour, so a step
        backwards is handled below rather than trusted away.
        """
        if celsius is None:
            raise CurveError('There is no temperature to decide from.')

        now = _real(now, 'The moment of this decision')
        wanted, decided_by, asked = self.curve.answer(celsius)

        if decided_by == 'limit':
            # One test and not two: the limit used to be compared here and
            # again inside duty(), and two copies of one comparison are two
            # things to keep in step.
            reason = ('%.1f C is at or above the limit of %.0f C, so the curve is not consulted '
                      'and the duty is 100%%.' % (celsius, self.curve.limit_c))
            return self._apply(100, celsius, now, reason)

        if self.duty is None:
            return self._apply(wanted, celsius, now, 'first decision: %s.'
                               % _asks(wanted, decided_by, asked, celsius))

        if wanted > self.duty:
            # Never waits. A dwell on the way up would be a dwell spent hot.
            return self._apply(wanted, celsius, now, '%s, above the %d%% now applied.'
                               % (_asks(wanted, decided_by, asked, celsius), self.duty))

        if wanted == self.duty:
            return self.duty, '%s.' % _asks(wanted, decided_by, asked, celsius, still=True)

        fallen = (self.basis_c - celsius) if self.basis_c is not None else 0.0
        waited = now - self.applied_at

        if waited < 0.0:
            # The clock moved under us. The dwell restarts from this moment and
            # the higher duty is held, which costs noise rather than degrees.
            self.applied_at = now
            waited = 0.0

        allowed = ('the floor would allow %d%% (the curve asks for %d%%)' % (wanted, asked)
                   if decided_by == 'floor' else 'the curve would allow %d%%' % wanted)

        if fallen < self.hysteresis_c:
            return self.duty, ('%d%% is held: %s, but the temperature has only fallen %.1f C of '
                               'the %.1f C required.'
                               % (self.duty, allowed, max(0.0, fallen), self.hysteresis_c))
        if waited < self.dwell_seconds:
            return self.duty, ('%d%% is held: %s, but only %.0f s of the %.0f s dwell have '
                               'passed.' % (self.duty, allowed, waited, self.dwell_seconds))

        return self._apply(wanted, celsius, now,
                           'the temperature has fallen %.1f C and the dwell has passed, so %d%% '
                           'replaces %d%%.' % (fallen, wanted, self.duty))

    def _apply(self, duty, celsius, now, reason):
        # The basis is only moved when the duty is. Leaving it where it was is
        # what makes the hysteresis measure from the temperature that justified
        # the duty being held, rather than from whatever the last cycle read -
        # which would let a slow drift down defeat it one tenth of a degree at
        # a time.
        if duty != self.duty:
            self.duty = duty
            self.basis_c = celsius
            self.applied_at = now
        return duty, reason


if __name__ == '__main__':
    import json
    import sys

    # The shipped defaults, so that `curve.py 45` answers for the curve the
    # plugin actually ships: FanCtl.xml's Default for points, floor_duty,
    # limit_c and panic_c, and DEFAULT_STEP for the step.
    text = sys.argv[2] if len(sys.argv) > 2 else '40:60,50:75,60:90,70:100'
    try:
        shape = Curve(text, floor=40, limit_c=70, panic_c=85)
        if len(sys.argv) > 1:
            degrees = _real(sys.argv[1], 'A temperature')
            duty, decided_by, asked = shape.answer(degrees)
            print(json.dumps({'celsius': degrees, 'raw': round(shape.raw(degrees), 1),
                              'duty': duty, 'decided_by': decided_by, 'curve_asked': asked,
                              'panic': shape.over_panic(degrees)}))
        else:
            print(json.dumps(shape.sample(), indent=2))
    except CurveError as failure:
        # The sentence, not a traceback. This class promises one, and the
        # standalone runner is a caller like any other.
        sys.stderr.write('%s\n' % failure)
        sys.exit(2)
