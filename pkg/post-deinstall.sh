#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Run by pkg(8) after the files of os-fanctl have been removed, and only on a real removal.
# An upgrade does not come through here: pkg deletes the old version in
# pkg_add_cleanup_old(), which runs the old package's PRE-deinstall and nothing else -
# PKG_SCRIPT_POST_DEINSTALL appears nowhere in libpkg/pkg_add.c - and the new version's
# post-install runs instead. So everything here may assume the plugin is going for good.
#
# The fans were handed back in pre-deinstall, while there was still something on the disk
# that could do it. Nothing here touches hardware: by now there is nothing left to touch it
# with, which is precisely why that work is not in this file.
#
# pkg has already removed every file the package owns. It has not yet removed the
# directories: pkg_delete() runs this script between pkg_delete_files() and pkg_delete_dirs().
# What is left is what pkg cannot know about: the files this plugin wrote outside its own
# package, the entry it made in config.xml, and the GUI's caches.

# The pidfiles and the chip lock, in case the controller had to be killed and daemon(8) never
# got round to removing them. /etc/rc.d/cleanvar empties /var/run at every boot, which is
# exactly why these are easy to forget: a firewall that is not rebooted keeps them for months.
rm -rf /var/run/fanctl

# Take the plugin back out of config.xml's system/firmware/plugins. Left in, it would be a
# plugin OPNsense believes it is managing and cannot find, and that list is what a plugin
# restore after a firmware upgrade works from. register.php only unregisters a name whose
# marker under /usr/local/opnsense/version has gone, which is why this belongs here and not in
# a pre-deinstall: that marker is one of the files pkg has just removed. The GUI's own
# remove.sh runs the same line after pkg remove, and running it twice changes nothing.
if [ -x /usr/local/opnsense/scripts/firmware/register.php ]; then
	/usr/local/opnsense/scripts/firmware/register.php remove os-fanctl ||
	    echo "could not take os-fanctl out of the plugin list in config.xml;" \
	        "System > Firmware > Plugins will keep showing it until the next plugin sync"
fi

# actions_fanctl.conf is gone; configd is still holding it. Until configd is restarted,
# configctl still answers for actions whose scripts are no longer on disk - which here would
# mean a restore that reports success having run nothing at all, and that is the one wrong
# answer this plugin must never give.
if [ -f /usr/local/etc/rc.d/configd ]; then
	/usr/local/etc/rc.d/configd restart
fi

# The menu file place-menu.sh wrote is not one of the package's files, so pkg leaves it behind and
# the menu would keep the entry. Its directory goes with it, and the model's if that is empty now.
rm -f /usr/local/opnsense/mvc/app/models/OPNsense/FanCtl/Menu/Menu.xml
rmdir /usr/local/opnsense/mvc/app/models/OPNsense/FanCtl/Menu \
    /usr/local/opnsense/mvc/app/models/OPNsense/FanCtl 2> /dev/null || true
rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /tmp/opnsense_menu_cache.xml

# system_cache_flush(): without it the menu keeps a dead entry and the user manager keeps an
# ACL tag pointing at a page that is not there, for up to an hour each. This block is word for
# word the +POST_DEINSTALL that opnsense/plugins generates for a plugin with models, and is
# kept identical to it on purpose.
if [ -f /usr/local/etc/rc.configure_plugins ]; then
	echo "Reloading plugin configuration"
	/usr/local/etc/rc.configure_plugins POST_DEINSTALL
fi

# Three things are kept on purpose, and each is named here rather than left for somebody to
# find later:
#
#   /var/db/fanctl - and above all measured.json inside it, the floors measured on this
#   machine's own fans. They are the only numbers in this plugin that came from the hardware
#   rather than from a settings page, every one of them cost somebody standing in front of the
#   appliance watching a fan stop, and a reinstall that still has them can drive a fan on the
#   first day instead of asking for that afternoon back. Remove it with:
#       rm -rf /var/db/fanctl
#
#   incident.json, if it is in there, is the record that a restore once failed. Somebody may
#   need to read it after this plugin is gone, which is the case for keeping it rather than
#   tidying it away with everything else.
#
#   the per-channel state files fanctl-io wrote in the same directory: the mode and duty each
#   channel was under before this plugin first set a duty on it. Keeping them is what lets a
#   reinstall hand a fan back correctly even if it arrives while a duty is still held.
#
#   /usr/local/etc/fanctl/floor.conf, if anything is written in it: somebody measured a fan
#   to get that number. The shipped shadow copy beside it belongs to the package and goes
#   with it.
#
#   /var/log/fanctl - what the controller did to the fans, and when. That is what a bug report
#   is cut from, and nothing writes to it once the plugin is gone, so it cannot grow.
#
#   the settings in config.xml, which is the framework's convention and is why a reinstall
#   comes back configured. The boot switch in /usr/local/etc/rc.conf.d/fanctl is removed with
#   the package, as rc.conf.d files of an rc script are.
echo "Fan Control removed; the fans were handed back before its files went. Its settings in"
echo "config.xml and the floors measured in /var/db/fanctl are kept."

if [ -f /var/db/fanctl/incident.json ]; then
	echo ""
	echo "WARNING: /var/db/fanctl/incident.json says a restore failed. If it was the last"
	echo "         one, the fans may still be held at a duty this plugin wrote and there is"
	echo "         now nothing installed that can undo it. Power the appliance off and on."
fi
