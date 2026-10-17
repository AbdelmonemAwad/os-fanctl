#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Remove the Fan Control plugin files. The settings stay in config.xml, the switch stays in
# /usr/local/etc/rc.conf.d/fanctl, and the state stays in /var/db/fanctl - so a machine
# that had a measured floor comes back with it after a reinstall instead of asking somebody
# to stand in front of the appliance and stop a fan again.

# Stopped first, while there is still an rc script to stop it with, and stopped properly.
# This is the line that matters in this file: the stop hands every fan back to the hardware
# before it returns, and a moment from now the script that could have done that will not be
# on the disk any more. onestop rather than stop, because the boot switch may already be
# off and a stop has to stop.
if [ -x /usr/local/etc/rc.d/fanctl ]; then
    /usr/local/etc/rc.d/fanctl onestop || true
fi

# And asked again directly, because the stop above may have found nothing running while the
# state files say a duty is still held - which is exactly what a machine looks like after a
# daemon was killed. It costs nothing when there is nothing to hand back.
if [ -x /usr/local/opnsense/scripts/fanctl/fanctl.py ]; then
    /usr/local/opnsense/scripts/fanctl/fanctl.py restore > /dev/null 2>&1 ||
        echo "the fans could NOT be handed back to the hardware: power the appliance off" \
             "and on to return them to firmware control"
fi

# The plugin hook first, before anything it calls: OPNsense loads every file in plugins.inc.d
# and calls its hooks at boot, and one left behind without the plugin's classes is a fatal error
# that stops rc.bootup before the network - which is what os-frontpanel's did on an XGS 136. The
# cron entry with it, which would otherwise go on calling a script that is no longer there.
# the daily self-update first, so that it cannot reinstall half way through this
rm -f /usr/local/etc/cron.d/fanctl-update
rm -rf /usr/local/share/os-fanctl
rm -f /usr/local/etc/inc/plugins.inc.d/fanctl.inc
rm -f /usr/local/etc/cron.d/fanctl
rm -f /usr/local/etc/rc.d/fanctl
rm -f /usr/local/etc/newsyslog.conf.d/fanctl
# The port helper and, if this tree was unpacked as-is, its source beside it. The binary goes
# with the plugin: it is the only thing on the machine that can write to a fan register, and
# leaving it behind would leave that ability behind with nothing above it.
rm -f /usr/local/opnsense/scripts/fanctl/fanctl-io
rm -rf /usr/local/fanctl-io
# The shipped shadow of the helper floor file, which belongs to the package. floor.conf beside
# it does NOT: anything written in that one is a measurement somebody took off a fan. The
# directory goes only if it is empty afterwards, which is the case where nothing was measured.
rm -f /usr/local/etc/fanctl/floor.conf.shadow
rmdir /usr/local/etc/fanctl 2> /dev/null || true
rm -f /usr/local/opnsense/service/conf/actions.d/actions_fanctl.conf
rm -rf /usr/local/opnsense/scripts/fanctl
rm -rf /usr/local/opnsense/mvc/app/models/OPNsense/FanCtl
rm -rf /usr/local/opnsense/mvc/app/controllers/OPNsense/FanCtl
rm -rf /usr/local/opnsense/mvc/app/views/OPNsense/FanCtl

# The two widget files, named one at a time because these two directories are shared with
# core and with every other plugin that has a dashboard panel. Removing a dashboard widget
# does not remove it from a saved layout - core simply finds nothing to render under that
# key, which is what it already does for a widget the user never had.
rm -f /usr/local/opnsense/www/js/widgets/FanCtl.js
rm -f /usr/local/opnsense/www/js/widgets/Metadata/FanCtl.xml
rm -f /usr/local/etc/rc.syshook.d/start/64-fanctl-i18n
rm -f /usr/local/share/pkg/triggers/os-fanctl.ucl

# The pidfiles of a service that has just been stopped, in case it had to be killed and
# daemon(8) never got round to removing them itself. The chip lock goes with them: it is an
# empty file that holds nothing once the process that flock()ed it is gone.
rm -rf /var/run/fanctl

# /var/db/fanctl and /var/log/fanctl are kept on purpose, and each for its own reason.
#
#   measured.json is the only file in this plugin whose numbers came from the hardware. It
#   cost somebody standing in front of the appliance watching a fan stop, and it is
#   worthless to anybody else and valuable to a reinstall. Remove it by hand if you mean to
#   start again from nothing:  rm -rf /var/db/fanctl
#
#   incident.json, if it is there, is a record that a restore failed - which is a thing
#   somebody may need to read after the plugin that wrote it is gone.
#
#   ch1.state and its siblings, written by fanctl-io: the mode and duty each channel was
#   under before this plugin first set a duty on it. They are what a release() writes back,
#   and keeping them means a reinstall can still hand a fan back correctly even if it is
#   installed while a duty is held.
#
#   the log says what the controller did to the fans and when, which is what a bug report
#   is cut from. Nothing writes to either once the plugin is gone, so neither can grow.
#
# /usr/local/etc/fanctl/floor.conf is kept for the same reason as the measurements: if
# anything is written in it, somebody measured a fan to get it. The shipped shadow copy
# beside it goes with the package.

service configd restart > /dev/null

# the menu and the ACL map are cached on disk for an hour each, so the menu would keep a
# dead entry until then. They live in the framework's own tempDir, which is /var/lib/php/tmp
# on 26.7 and not /tmp, so ask config.php where it is.
CONFIGPHP=/usr/local/opnsense/mvc/app/config/config.php
PHPTMP=$(php -r "echo (include '${CONFIGPHP}')->application->tempDir;" 2>/dev/null)
PHPTMP=${PHPTMP:-/var/lib/php/tmp}
rm -f "${PHPTMP}/opnsense_menu_cache.xml" "${PHPTMP}/opnsense_acl_cache.json"

configctl webgui restart > /dev/null 2>&1 || true

# the GUI strings merged into the gettext catalogues are left where they are: they are only
# ever read by msgid, and the next core update replaces those catalogues anyway
echo "Fan Control removed; the fans are back under hardware control, and the settings in"
echo "config.xml and the measurements in /var/db/fanctl are kept"
