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

namespace OPNsense\FanCtl;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;

class FanCtl extends BaseModel
{
    /**
     * Every message appended here stops the save. There is no channel in this framework for
     * a remark, so the rule applied throughout is: block a setting that would be accepted and
     * then silently ignored, or one that contradicts another setting on the same page - and
     * leave everything else to the page, which has a whole tab for saying what it cannot
     * promise.
     *
     * What is deliberately NOT checked here: whether the hardware will actually accept any of
     * this. A model has no business probing a chip, the answer would be taken at the moment of
     * a save rather than at the moment of a write, and the daemon evaluates the whole safety
     * contract every cycle anyway. A save that passes this function is a coherent set of
     * numbers, not a promise that a fan will turn.
     */
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        $general = $this->general;
        if (!$validateFullModel && !$general->isFieldChanged()) {
            return $messages;
        }

        $mode = (string)$general->mode;
        $channels = $general->channels->getValues();
        $floor = (int)(string)$general->floor_duty;
        $manual = (int)(string)$general->manual_duty;
        $limit = (int)(string)$general->limit_c;
        $panic = (int)(string)$general->panic_c;

        /* A mode that writes, with nothing named to write to. The daemon would start, satisfy
           nothing, drive nothing and report "not driving" for ever - which looks exactly like a
           fault and is really an empty list. */
        if ($mode !== 'monitor' && empty($channels)) {
            $messages->appendMessage(new Message(
                gettext('Name at least one fan channel for this mode, or set the mode back to ' .
                        'watch only. The Hardware tab lists the channels this chip reports.'),
                'general.channels'
            ));
        }

        /* The floor wins over everything, so a manual duty below it is a number that would be
           silently raised the moment it was applied. Refused with the reason instead: a setting
           that is accepted and then ignored is worse than one that is refused. */
        if ($manual < $floor) {
            $messages->appendMessage(new Message(
                sprintf(
                    gettext('This is below the floor of %d%%, which always wins, so it would ' .
                            'never be applied as written.'),
                    $floor
                ),
                'general.manual_duty'
            ));
        }

        /* The panic point is where the plugin gives up and hands the fans back. Below the limit
           it would give up before it had even tried running them at full duty, which is the
           wrong way round and would read on the page as the plugin failing rather than as a
           setting being backwards. */
        if ($panic <= $limit) {
            $messages->appendMessage(new Message(
                sprintf(
                    gettext('The panic point must be above the limit of %d C. Below it, the ' .
                            'fans would be handed back before full duty had ever been tried.'),
                    $limit
                ),
                'general.panic_c'
            ));
        }

        foreach ($this->curveComplaints((string)$general->points, $floor, $limit) as $complaint) {
            $messages->appendMessage(new Message($complaint, 'general.points'));
        }

        return $messages;
    }

    /**
     * Everything wrong with a curve, in the words the page should show.
     *
     * The field mask has already refused anything that is not a list of degrees:percent pairs,
     * so what is left to check is the sense of it rather than its shape - and the sense is
     * checked in the same order, and with the same rules, as curve.py checks it at the other
     * end. Two copies of a rule is one more than anybody wants; they are here and there because
     * the daemon reads config.xml with no model in front of it, and a curve that only the page
     * had ever checked would be a curve nobody had checked on a restored backup.
     */
    private function curveComplaints($text, $floor, $limit)
    {
        $complaints = [];
        $points = [];

        foreach (explode(',', trim($text)) as $part) {
            if (trim($part) === '') {
                continue;
            }
            $pair = explode(':', $part);
            if (count($pair) !== 2 || !is_numeric(trim($pair[0])) || !is_numeric(trim($pair[1]))) {
                /* the mask has already said this better; nothing further is useful */
                return $complaints;
            }
            $points[] = [(int)trim($pair[0]), (int)trim($pair[1])];
        }

        if (count($points) < 2) {
            $complaints[] = gettext('A curve needs at least two points.');
            return $complaints;
        }

        for ($index = 1; $index < count($points); $index++) {
            [$beforeC, $beforeDuty] = $points[$index - 1];
            [$afterC, $afterDuty] = $points[$index];
            if ($afterC <= $beforeC) {
                $complaints[] = sprintf(
                    gettext('The points must rise in temperature: %d C comes after %d C.'),
                    $afterC,
                    $beforeC
                );
                break;
            }
            if ($afterDuty < $beforeDuty) {
                /* A curve that slows the fan down as the machine gets hotter is always a
                   mistake in the typing, and it is the one mistake that would be obeyed
                   exactly. */
                $complaints[] = sprintf(
                    gettext('The duty may not fall as the temperature rises: %d%% at %d C is ' .
                            'below %d%% at %d C.'),
                    $afterDuty,
                    $afterC,
                    $beforeDuty,
                    $beforeC
                );
                break;
            }
        }

        foreach ($points as [$celsius, $duty]) {
            if ($duty < $floor) {
                $complaints[] = sprintf(
                    gettext('%d%% at %d C is below the floor of %d%%, so this point would ' .
                            'never be used as written - the floor would raise it.'),
                    $duty,
                    $celsius,
                    $floor
                );
                break;
            }
        }

        /* A curve whose points all sit above the limit is a curve that is never consulted: at
           the limit the duty becomes 100 whatever the curve says, so every point above it is
           decoration. Worth refusing, because somebody who writes one believes they have set a
           fan curve. */
        if ($points[0][0] >= $limit) {
            $complaints[] = sprintf(
                gettext('Every point is at or above the limit of %d C, where the duty becomes ' .
                        '100%% whatever the curve says, so the curve would never be consulted.'),
                $limit
            );
        }

        return $complaints;
    }
}
