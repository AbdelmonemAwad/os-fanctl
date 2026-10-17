#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Run by pkg(8) after the files of os-fanctl are in place, on install and on upgrade alike.
# tools/make-package.sh inlines it into the package manifest.
#
# Three of the blocks below - the configd restart, run_migrations and rc.configure_plugins
# POST_INSTALL - are, line for line, what an OPNsense plugin runs: os-smart's post-install
# is those three and nothing else. The rest is this plugin's own.
#
# Nothing here may fail the install. By the time pkg runs this every file is already on
# disk, so refusing now would leave the package installed anyway; each step that can fail
# therefore says what the machine will be missing and the script carries on.
#
# Four things install/install.sh does are deliberately NOT here:
#
#   the chmod lines - pkg has already set every mode and owner from the plist, and it
#   recorded what it set. Setting them again from a script lets the disk and pkg's record of
#   the disk drift apart, and pkg check -s then reports this package as altered on a machine
#   where nothing is wrong.
#
#   the php -l / XML / JSON / py_compile / sh -n checks - they belong to the source tree,
#   before a package exists. By the time this script runs the files are installed; a check
#   that fails here cannot undo anything, it can only make pkg print an error after the fact.
#
#   rm -f of opnsense_menu_cache.xml and opnsense_acl_cache.json - rc.configure_plugins
#   POST_INSTALL below calls system_cache_flush(), which invalidates the ACL cache and the
#   menu cache and, unlike those two rm lines, also clears the model caches and the compiled
#   Volt templates. The official call does strictly more.
#
#   cp -R src/ /usr/local/ - that is what the package itself is.
#
# And one thing that is not here on purpose and is worth naming, because its absence is the
# whole posture of this plugin: nothing below starts the controller, and nothing below
# enables it. Not on a first install, and not on an upgrade either. A package that began
# writing to fan registers because it had been unpacked would be a package that can stop the
# only fan in somebody's chassis.

# configd reads actions_fanctl.conf once, at start.
if [ -f /usr/local/etc/rc.d/configd ]; then
	/usr/local/etc/rc.d/configd restart
fi

# The controller's log. 0750 because what it logs is what it did to the fans and what the
# chip answered. install -d also repairs the mode and the owner on an upgrade instead of
# leaving whatever it found.
if ! install -d -o root -g wheel -m 0750 /var/log/fanctl; then
	echo "/var/log/fanctl could not be created: the controller will still run and will"
	echo "still log to syslog, but the page will have no log file to show"
fi

# And the state directory, which is checked, because this one is not a convenience. The
# floors measured on this machine's own fans live here, and so does the record of what each
# channel was under before the plugin touched it - which is what a restore writes back. A
# controller that cannot write that file cannot satisfy the baseline line of its own safety
# contract, and will therefore refuse to drive anything at all rather than take a fan it
# cannot give back.
if ! install -d -o root -g wheel -m 0750 /var/db/fanctl; then
	echo "/var/db/fanctl could not be created: fan control will refuse to drive anything,"
	echo "because it has nowhere to record what it would have to put back"
fi

# The page's and the widget's strings, merged into every installed GUI language's catalogue -
# the same merge os-linkhealth runs, from this plugin's own copy. A failure is reported as one and
# not as "0 added", and the next boot (64-fanctl-i18n) runs the same merge again.
if [ -x /usr/local/opnsense/scripts/fanctl/merge_ui_translations.py ]; then
	ADDED=$(/usr/local/opnsense/scripts/fanctl/merge_ui_translations.py)
	STATUS=$?
	if [ "${STATUS}" -eq 0 ]; then
		echo "translated strings added: ${ADDED:-0}"
	else
		echo "the GUI strings were not merged (exit ${STATUS}); the next boot runs the same merge"
	fi
	if [ -n "${ADDED}" ] && [ "${ADDED}" != "0" ]; then
		echo "restarting the web GUI so that it reads the strings just added"
		[ -f /usr/local/sbin/configctl ] &&
		    /usr/local/sbin/configctl webgui restart > /dev/null 2>&1 || true
	fi
fi

if [ -f /usr/local/opnsense/mvc/script/run_migrations.php ]; then
	/usr/local/opnsense/mvc/script/run_migrations.php OPNsense/FanCtl
fi

# Put the plugin in config.xml's system/firmware/plugins list, which is where OPNsense keeps
# the plugins it considers managed; without it System > Firmware > Plugins shows this as an
# installed package that nothing configured. The GUI's own install path runs this line itself,
# after pkg - scripts/firmware/install.sh does - but a bare pkg add does not, so the package
# carries it. It is idempotent. Guarded because it is core's file, not ours, and it has moved
# before.
if [ -x /usr/local/opnsense/scripts/firmware/register.php ]; then
	/usr/local/opnsense/scripts/firmware/register.php install os-fanctl > /dev/null 2>&1 || true
fi

if [ -f /usr/local/etc/rc.configure_plugins ]; then
	echo "Reloading plugin configuration"
	/usr/local/etc/rc.configure_plugins POST_INSTALL
fi

# The boot switch, written once and never rewritten. Whether this machine runs fan control
# by itself is a decision somebody made on the page, and an upgrade must not undo it in
# either direction. rc.subr's own enable and disable edit this same file in place afterwards.
if [ ! -f /usr/local/etc/rc.conf.d/fanctl ]; then
	install -d -o root -g wheel -m 0755 /usr/local/etc/rc.conf.d
	{
		echo '# Whether this machine runs fan control at boot.'
		echo '# Written once on installation; changed with: configctl fanctl enable'
		echo 'fanctl_enable="NO"'
	} > /usr/local/etc/rc.conf.d/fanctl
	chmod 644 /usr/local/etc/rc.conf.d/fanctl
fi

# The file fanctl-io reads its own floor from. Copied from the shipped shadow only when
# there is nothing there: once a measured floor is written into it, that file belongs to
# whoever measured the fan and an upgrade must not overwrite it.
install -d -o root -g wheel -m 0755 /usr/local/etc/fanctl 2> /dev/null
if [ ! -f /usr/local/etc/fanctl/floor.conf ] &&
    [ -f /usr/local/etc/fanctl/floor.conf.shadow ]; then
	install -o root -g wheel -m 0644 /usr/local/etc/fanctl/floor.conf.shadow \
	    /usr/local/etc/fanctl/floor.conf
fi

# The port helper. It is compiled on the machine rather than shipped as a binary, because
# it is built against this firewall's own headers by this firewall's own clang - see
# src/fanctl-io/Makefile for why that is the point rather than a compromise. If the package
# carried the source through to /usr/local/fanctl-io, build it now; if the binary is already
# there, leave it alone.
#
# A failure here is not an error and must not read like one. Without the helper this plugin
# reads and reports and writes nothing, which is a useful plugin on any appliance.
if [ ! -x /usr/local/opnsense/scripts/fanctl/fanctl-io ] &&
    [ -f /usr/local/fanctl-io/fanctl-io.c ]; then
	if ( cd /usr/local/fanctl-io && make ) > /dev/null 2>&1; then
		install -o root -g wheel -m 0755 /usr/local/fanctl-io/fanctl-io \
		    /usr/local/opnsense/scripts/fanctl/fanctl-io
		echo "the port helper was built and installed"
	else
		echo "note: fanctl-io could not be built here, so this plugin can read and cannot"
		echo "      write. Every reading on the Fans tab still works, and the control modes"
		echo "      will refuse and say why."
	fi
fi

# The two layers underneath, each checked separately because a tree missing chip.py is a
# different mistake from one whose helper would not build.
if [ ! -f /usr/local/opnsense/scripts/fanctl/chip.py ]; then
	echo "note: the hardware layer (chip.py) is not installed, so this plugin can read and"
	echo "      cannot write. Every reading on the Fans tab still works."
elif [ ! -x /usr/local/opnsense/scripts/fanctl/fanctl-io ]; then
	echo "note: the port helper (fanctl-io) is not installed, so this plugin can read and"
	echo "      cannot write. Every reading on the Fans tab still works."
fi

echo "Fan Control installed: the page is at System > Fan Control"
echo "nothing is controlling a fan yet, and nothing will until it is turned on there"

# An upgrade leaves a running controller running the code it started with, which is
# deliberate - see pre-deinstall.sh - and means the machine is being controlled by code that
# is no longer on the disk. Harmless, and worth knowing.
if [ -x /usr/local/etc/rc.d/fanctl ] && /usr/local/etc/rc.d/fanctl status > /dev/null 2>&1; then
	echo "the controller is still running the code from before this upgrade;"
	echo "restart it when convenient with:  configctl fanctl restart"
fi

# The one thing in this plugin that a human has to act on, said at the one moment somebody
# is certainly reading the output.
if [ -f /var/db/fanctl/incident.json ]; then
	echo ""
	echo "WARNING: a previous restore FAILED (/var/db/fanctl/incident.json). The fans may"
	echo "         still be held at a duty this plugin wrote. Power the appliance off and"
	echo "         on; a warm reboot does not necessarily clear a fan register."
fi
