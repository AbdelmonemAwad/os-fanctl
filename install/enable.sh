#!/bin/sh
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
# Turn fan control on without the settings page - for an image that installs the plugin at first
# boot and is meant to leave it running - the way the page's Save and Apply does it: enabled in
# config.xml through the plugin's own model, the boot switch in rc.conf.d, the daemon restarted.
#
# It changes nothing else, and that is what makes it safe to run on any machine. The mode stays
# what the model holds, which on a fresh install is "monitor": every sensor read, no chip ever
# written. Turning the plugin on cannot take a fan away from the firmware on a board the plugin has
# not been taught; choosing a curve stays the owner's decision, on the page.
#
#   install/enable.sh        exit 0 when it is on, 1 when the model refused
set -u

/usr/local/bin/php -r '
require_once "/usr/local/opnsense/mvc/script/load_phalcon.php";
$m = new \OPNsense\FanCtl\FanCtl();
$m->general->enabled = "1";
$bad = [];
foreach ($m->performValidation() as $msg) { $bad[] = $msg->getField() . ": " . $msg->getMessage(); }
if ($bad) { echo "fan control left off: " . implode("; ", $bad) . "\n"; exit(1); }
$m->serializeToConfig();
\OPNsense\Core\Config::getInstance()->save();
echo "fan control turned on, mode " . $m->general->mode . "\n";
' || exit 1

configctl fanctl enable > /dev/null 2>&1
configctl fanctl stop > /dev/null 2>&1
configctl fanctl start > /dev/null 2>&1
exit 0
