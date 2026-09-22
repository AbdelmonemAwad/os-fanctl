<?php

/*
 * Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\FanCtl\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;
use OPNsense\Core\Backend;

/**
 * Start, stop and look at the fan controller, and the five things the page asks that a plain
 * service controller has no answer for: what chip is in this machine, what it is doing this
 * second, hold a fan at a duty, measure the duty at which a fan stops, and hand the fans back.
 *
 * Nothing in this class decides whether a write is allowed. Every such rule lives in fanctl.py,
 * where the whole safety contract is evaluated with the hardware in front of it, and what arrives
 * here is a refusal with a sentence attached. That is deliberate: a rule enforced in two places
 * is a rule that will one day be enforced in one of them, and the copy that matters is the one
 * next to the fan.
 */
class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceName = 'fanctl';
    protected static $internalServiceClass = '\OPNsense\FanCtl\FanCtl';
    protected static $internalServiceEnabled = 'general.enabled';

    /**
     * A channel name as a chip reports one. Narrow, because it ends up on a command line.
     */
    private const CHANNEL = '/^[A-Za-z][A-Za-z0-9_]{1,15}$/';

    /**
     * How long a floor test may be asked to run for. The script clamps to the same range; this
     * is here so that a request for an hour is refused before it reaches a command line.
     */
    private const SECONDS_MIN = 3;
    private const SECONDS_MAX = 60;

    /**
     * The daemon reads its settings once, at start: the curve, the floor, the interval and which
     * channels it may touch are all decided before the first write. There is no reloading one of
     * them into a running control loop, and a loop that changed its own rules while holding a fan
     * at a duty chosen under the old ones is not a thing to want. So applying a change means
     * stopping - which hands the fans back - and starting again.
     */
    protected function reconfigureForceRestart()
    {
        return 1;
    }

    /**
     * Applying the settings also decides whether the controller comes back after a reboot.
     *
     * The framework's own reconfigure starts and stops the service and no more; what a machine
     * does at boot is written in rc.conf.d, and rc.subr is the thing that edits it. Nothing else
     * on this page would ever set that switch, so a controller turned on here would run now and
     * be absent after the next reboot - and for this plugin that is not merely surprising, it is
     * a machine whose fans are on a firmware curve that its owner believes has been replaced. The
     * switch is set first, so that it is right even if the start that follows it fails.
     */
    public function reconfigureAction()
    {
        if ($this->request->isPost()) {
            (new Backend())->configdRun(
                'fanctl ' . ($this->serviceEnabled() ? 'enable' : 'disable')
            );
        }

        return parent::reconfigureAction();
    }

    private function run($action, $params = [], $timeout = 60)
    {
        return (new Backend())->configdpRun('fanctl ' . $action, $params, false, $timeout);
    }

    /**
     * Every one of these actions answers in JSON, and a script that could not run answers with
     * nothing at all. Saying so in the shape the page already understands is better than letting
     * it decide for itself what an empty string meant - and on this page an unexplained blank is
     * worse than elsewhere, because the reader is trying to find out what is happening to their
     * fans.
     */
    private function response($output)
    {
        $response = json_decode($output, true);
        return is_array($response)
            ? $response
            : ['status' => 'failed', 'detail' => gettext('No response from the fan control script.')];
    }

    /**
     * What chip is in this machine, what the appliance table says about it, and whether the two
     * agree. Reads only: no port is written, nothing is loaded, and no fan changes - which is
     * what makes it the first thing the page asks for, on any hardware at all.
     */
    public function probeAction()
    {
        return $this->response($this->run('probe'));
    }

    /**
     * The live picture: every fan, its speed, the temperature each decision came from, and the
     * safety contract line by line.
     *
     * Where the daemon is running, this is the daemon's own document - written by the process
     * that made the decisions. That is not a shortcut: having the page take its own reading would
     * mean two processes talking to one chip through the same two ports, with nothing to
     * serialise either of them against a kernel driver that takes no lock.
     */
    public function stateAction()
    {
        return $this->response($this->run('state'));
    }

    /**
     * What the fans and the temperatures have been doing, over the last hour, day, week and
     * month: the lowest, the highest and the time-weighted mean of every sensor, how long each
     * one spent above each rung of a fixed ladder, and the one number this plugin exists to be
     * able to state - how many degrees this chassis gives back for more fan.
     *
     * Read out of a document the sampler already wrote, not recomputed here, and for the same
     * reason stateAction() gives: recomputing it would put a second process on the same two
     * ports. This one does not touch the chip at all.
     */
    public function trendAction()
    {
        return $this->response($this->run('trend'));
    }

    /**
     * One ring of the record, raw, for drawing. The parameter says which - live, fine, hour or
     * day - and the page asks for one at a time: loading the whole record on a machine that has
     * been up a year is half a megabyte the reader did not ask for.
     *
     * The name is checked here rather than passed through. configd quotes its parameters, so
     * this is not about shell safety; it is that a ring name the script does not know produces a
     * refusal from the far end of a backend call, which is a slower and worse way to say the
     * same thing.
     */
    public function historyAction($ring = 'live')
    {
        if (!in_array($ring, ['live', 'fine', 'hour', 'day'], true)) {
            return ['status' => 'failed', 'detail' => gettext('There is no history of that name.')];
        }

        return $this->response($this->run('history', [$ring]));
    }

    /**
     * Hand every fan back to the hardware, now.
     *
     * Deliberately not guarded by anything. Not by the service being enabled, not by it running,
     * not by the hardware matching the table: it writes back what was captured before this plugin
     * touched the chip, it does nothing at all when this plugin was never driving anything, and
     * it is the one button on this page that must work when everything else has gone wrong.
     */
    public function restoreAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        return $this->response($this->run('restore'));
    }

    /**
     * Hold one channel at one duty for a few seconds, then give it back. The floor is enforced by
     * the script: this is somebody choosing a fan speed, not measuring one.
     */
    public function setAction()
    {
        return $this->hold('set');
    }

    /**
     * The floor test: the one operation in this plugin that is allowed below the floor, because
     * measuring where a fan stops is the only job that requires going there. It watches the
     * tachometer, ends the moment the fan stops, and hands the fan back whatever happens.
     *
     * The wait is generous because the test is meant to be: the script holds the duty for the
     * seconds asked, then restores, and only then answers.
     */
    public function testAction()
    {
        return $this->hold('test', self::SECONDS_MAX + 60);
    }

    /**
     * The shape of a set or a test request. Only the shape: whether it is allowed is the script's
     * to decide, with the hardware in front of it.
     */
    private function hold($action, $timeout = 90)
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }

        $channel = (string)$this->request->getPost('channel', 'striptags', '');
        $duty = $this->request->getPost('duty', null, '');
        $seconds = $this->request->getPost('seconds', null, '');

        if (preg_match(self::CHANNEL, $channel) !== 1) {
            return ['status' => 'failed',
                    'detail' => gettext('That is not the name of a fan channel.')];
        }
        /* Read without a sanitiser and checked as a number here, because the framework's numeric
           filters answer a non-numeric input with an empty string - which would arrive at the
           script as a missing argument and be reported as a usage error rather than as "that is
           not a percentage". */
        if (!is_numeric($duty) || (int)$duty < 0 || (int)$duty > 100) {
            return ['status' => 'failed',
                    'detail' => gettext('A duty is a percentage between 0 and 100.')];
        }
        if ($seconds === '' || $seconds === null) {
            $seconds = '';
        } elseif (!is_numeric($seconds) || (int)$seconds < self::SECONDS_MIN
                  || (int)$seconds > self::SECONDS_MAX) {
            return ['status' => 'failed',
                    'detail' => sprintf(
                        gettext('The hold must be between %d and %d seconds.'),
                        self::SECONDS_MIN,
                        self::SECONDS_MAX
                    )];
        }

        return $this->response($this->run(
            $action,
            [strtoupper($channel), (string)(int)$duty, (string)$seconds],
            $timeout
        ));
    }
}
