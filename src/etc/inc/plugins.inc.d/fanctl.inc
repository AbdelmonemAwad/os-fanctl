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
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS "AS IS" AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
 * THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * How fan control comes up at boot, and why this file exists.
 *
 * The daemon is started by an rc.d script, and the first version of this plugin left its switch
 * in /usr/local/etc/rc.conf.d/fanctl for /etc/rc to read. Measured on two appliances: nothing
 * reads it, and a fan controller that is not running after a reboot is the one failure this
 * plugin was built never to have. OPNsense starts a plugin's daemons from rc.bootup through
 * plugins_configure('bootup'), which calls the 'bootup' tasks a plugin returns from _configure();
 * so the start is here, guarded by the enabled setting. The daemon's own contract - never drive a
 * fan it has not been permitted to, hand every fan back on exit - is unchanged by who starts it.
 */

function fanctl_enabled()
{
    $model = new \OPNsense\FanCtl\FanCtl();
    return (string)$model->general->enabled == '1';
}

function fanctl_services()
{
    $services = [];

    if (!fanctl_enabled()) {
        return $services;
    }

    $services[] = [
        'description' => gettext('Fan control'),
        'configd' => [
            'restart' => ['fanctl restart'],
            'start' => ['fanctl start'],
            'stop' => ['fanctl stop'],
        ],
        'name' => 'fanctl',
        'pidfile' => '/var/run/fanctl/fanctl.pid',
    ];

    return $services;
}

function fanctl_configure()
{
    return [
        'bootup' => ['fanctl_configure_do'],
    ];
}

function fanctl_configure_do($verbose = false)
{
    if (!fanctl_enabled()) {
        return;
    }

    if ($verbose) {
        echo 'Starting fan control...';
        flush();
    }

    configd_run('fanctl start');

    if ($verbose) {
        echo "done.\n";
    }
}
