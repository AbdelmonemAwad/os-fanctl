#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Install the Fan Control plugin from a copy of this repository on the firewall:
#   sh install/install.sh
#
# Safe to run again after an update: the settings in config.xml are kept, the switch in
# /usr/local/etc/rc.conf.d/fanctl is kept, and so are the floor measurements in
# /var/db/fanctl - which matter more than either, because they are the only numbers in
# this plugin that were taken from the hardware rather than typed.
#
# Three things this script deliberately does not do.
#
# It does not start anything. A plugin that begins writing to fan registers the moment it
# is installed is a plugin that can stop the only fan in a chassis it has never seen.
#
# It does not enable control, and installing this over an older copy does not either. The
# mode stays where the owner left it, and on a fresh installation that is "watch only".
#
# It does not touch a fan even to find out what is there. The probe on the Hardware tab
# reads and writes nothing, and it is the owner who decides when anything more happens.

set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
CONFIGPHP=/usr/local/opnsense/mvc/app/config/config.php
RCCONF=/usr/local/etc/rc.conf.d/fanctl

# plugin files: src/ maps to /usr/local
cp -R "${HERE}/src/" /usr/local/

# fanctl.py is run by the rc script and by configd, so it needs the bit; the modules beside
# it are imported rather than executed, and fans.json is only ever read. Giving the whole
# directory the same treatment as the sibling plugins do keeps a new module from arriving
# without the bit and failing an hour later for a reason nobody can see.
# fanctl-io is named out of the second sweep on purpose: it is a binary that lives in this
# directory and is not copied from src/, so on a second run the sweep would take the
# executable bit off a working helper - and if the rebuild below then failed, the machine
# would be left with a helper it could no longer run.
find /usr/local/opnsense/scripts/fanctl -type f -name '*.py' -exec chmod 755 {} +
find /usr/local/opnsense/scripts/fanctl -type f ! -name '*.py' ! -name 'fanctl-io'     -exec chmod 644 {} +
chmod 755 /usr/local/etc/rc.d/fanctl
chmod 644 /usr/local/etc/newsyslog.conf.d/fanctl
# No daily self-update since 2026-10-20: a release is installed from XGS: Updates
# (its Plugins tab, in os-xgs-npu) or by hand with self-update.py. An older
# install's cron entry goes.
rm -f /usr/local/etc/cron.d/fanctl-update
chmod 644 /usr/local/opnsense/service/conf/actions.d/actions_fanctl.conf

# The dashboard widget. Core globs the Metadata directory for *.xml and takes the element
# name inside as the key this widget has in every user's saved dashboard layout, so both
# files have to be readable by the web server or the widget is simply not offered - with no
# error anywhere, because a widget nobody can read is indistinguishable from one nobody
# installed.
chmod 644 /usr/local/opnsense/www/js/widgets/FanCtl.js
chmod 644 /usr/local/opnsense/www/js/widgets/Metadata/FanCtl.xml
# The page's Arabic is in opnsense-arabic's catalogue since 2026-10-22, so this plugin merges
# nothing into it any more. What an earlier install put here for that goes: the copy of the
# Arabic, its merge, the boot hook and the pkg trigger that ran it, and the merge's record.
rm -f /usr/local/opnsense/scripts/fanctl/i18n/ui/ar_SA.json     /usr/local/opnsense/scripts/fanctl/merge_ui_translations.py     /usr/local/etc/rc.syshook.d/start/64-fanctl-i18n     /usr/local/share/pkg/triggers/os-fanctl.ucl     /var/db/fanctl/i18n-owned.json

# The controller's log. 0750 because what it logs is what it did to the fans and what the
# chip answered back, which is diagnostic detail about the hardware.
install -d -o root -g wheel -m 0750 /var/log/fanctl

# And the state. Four files live here and one of them is the reason this directory is
# never removed on an uninstall: measured.json holds the floors measured on this machine's
# own fans, which cost somebody standing in front of the appliance watching a fan stop.
# baseline.json holds what each channel was under before the plugin touched it, which is
# what a restore writes back; held.json says whether anything is being driven right now,
# which is how a start after a crash knows to put things right first; and incident.json
# exists only when a restore has failed, which is the one state here a human must act on.
install -d -o root -g wheel -m 0750 /var/db/fanctl

# Everything that has to parse is checked while only files have been touched: a syntax
# error must stop the install here rather than after configd has been told to reload it.
# php -l writes the parse error to its own stdout, so it cannot simply be discarded - the
# install would then stop without ever saying which file was wrong.
for f in $(find "${HERE}/src" -name '*.php'); do
    REPORT=$(php -l "$f" 2>&1) || { echo "${REPORT}"; exit 1; }
done
# the model, the form, the ACL and the menu are all XML, read by the GUI through the caches
# dropped below; one malformed file there takes the page with it
for f in $(find "${HERE}/src" -name '*.xml'); do
    python3 -c 'import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])' "$f" ||
        { echo "malformed XML: $f"; exit 1; }
done
for f in $(find /usr/local/opnsense/scripts/fanctl -name '*.py'); do
    python3 -m py_compile "$f"
done
# fans.json is the hardware table: which chip a model must have, which channel turns which
# fan, and what has actually been measured. A broken one would not stop the plugin - it
# would make it refuse to drive anything and say it could not read its own table - but it
# would do so an hour later, to somebody who had stopped thinking about this install.
for f in $(find /usr/local/opnsense/scripts/fanctl -name '*.json'); do
    python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$f" ||
        { echo "malformed JSON: $f"; exit 1; }
done
# The rc script is checked too, and for a harder reason than the rest: it is the one file
# that, broken, leaves a controller that cannot be stopped - and a controller that cannot
# be stopped is a fan that cannot be handed back. sh -n parses it without running a line.
sh -n /usr/local/etc/rc.d/fanctl || { echo "the rc script does not parse"; exit 1; }

# The helper that actually touches the ports. It is built here, on the firewall, by the
# firewall's own clang against the firewall's own headers - which is not a compromise but
# the reason the approach survives an OPNsense update: it is ordinary userland, it links
# against nothing but libc, and it knows nothing about the running kernel. A module in its
# place would have to match __FreeBSD_version exactly and would stop loading the first time
# OPNsense moved the kernel, on a firewall whose fans it was meant to be looking after.
#
# A failure here is NOT fatal, and that is deliberate. Without the helper this plugin reads
# and reports and writes nothing, which is a useful plugin: every temperature and every
# tachometer on the Fans tab still works. So the build says what will be missing and the
# install carries on.
if [ -f "${HERE}/src/fanctl-io/fanctl-io.c" ]; then
    if ( cd "${HERE}/src/fanctl-io" && make clean > /dev/null 2>&1; \
         cd "${HERE}/src/fanctl-io" && make ); then
        install -o root -g wheel -m 0755 "${HERE}/src/fanctl-io/fanctl-io" \
            /usr/local/opnsense/scripts/fanctl/fanctl-io
        echo "the port helper was built and installed"
    else
        echo "note: fanctl-io could not be built, so this plugin can read and cannot write."
        echo "      every reading on the Fans tab still works; the control modes will refuse"
        echo "      and say why."
    fi
fi

# And the file the helper reads its own floor from. Shipped as floor.conf.shadow with every
# line commented out, copied into place only when there is nothing there already - because
# once somebody has written a measured floor into it, that file is theirs and an update must
# not overwrite it. Nothing in the Python or on the page can lower what is in here.
install -d -o root -g wheel -m 0755 /usr/local/etc/fanctl
if [ ! -f /usr/local/etc/fanctl/floor.conf ] &&
   [ -f /usr/local/etc/fanctl/floor.conf.shadow ]; then
    install -o root -g wheel -m 0644 /usr/local/etc/fanctl/floor.conf.shadow \
        /usr/local/etc/fanctl/floor.conf
    echo "the helper floor file is at /usr/local/etc/fanctl/floor.conf (all defaults commented"
    echo "out, so the compiled-in hard floor applies until somebody measures a fan)"
fi

# chip.py is the layer between the two. Checked separately from the helper, because a tree
# missing this file is a different mistake from a tree whose helper would not build.
if [ ! -f /usr/local/opnsense/scripts/fanctl/chip.py ]; then
    echo "note: chip.py is not installed, so this plugin can read and cannot write."
    echo "      every reading on the Fans tab still works; the control modes will refuse"
    echo "      and say so."
fi

# The switch /etc/rc consults at boot. It is written once, off, and never written again by
# this script: whether this machine runs fan control by itself is a decision somebody made,
# and an update must not undo it. rc.subr's own enable and disable edit this same file in
# place afterwards, which is what configctl fanctl enable does.
if [ ! -f "${RCCONF}" ]; then
    install -d -o root -g wheel -m 0755 /usr/local/etc/rc.conf.d
    {
        echo '# Whether this machine runs fan control at boot.'
        echo '# Written once by the installer; changed with: configctl fanctl enable'
        echo 'fanctl_enable="NO"'
    } > "${RCCONF}"
    chmod 644 "${RCCONF}"
fi

# reload configd so it picks up the new actions
service configd restart > /dev/null

# The menu and the ACL map are cached on disk for an hour each, so until they are dropped
# the new page is missing from the menu and its ACL tag is unknown to the user manager.
# Those caches are not in /tmp: they sit in the framework's own tempDir, which is
# /var/lib/php/tmp on 26.7, so ask config.php where it is instead of writing down a path
# that has already moved once.
PHPTMP=$(php -r "echo (include '${CONFIGPHP}')->application->tempDir;" 2>/dev/null) || PHPTMP=''
PHPTMP=${PHPTMP:-/var/lib/php/tmp}
rm -f "${PHPTMP}/opnsense_menu_cache.xml" "${PHPTMP}/opnsense_acl_cache.json"

configctl webgui restart > /dev/null 2>&1 || true

# What this install is, for the self-update: the commit, from a git checkout, or from the
# .source-commit stamp that an xgs-image-builder image or the self-update leaves in the tree.
# Without either it is recorded as unknown, and the self-update takes the latest release once.
REV=; RDATE=; RSRC=unknown
if [ -e "${HERE}/.git" ] && REV=$(git -C "${HERE}" rev-parse HEAD 2>/dev/null); then
    RDATE=$(git -C "${HERE}" log -1 --format=%cI 2>/dev/null); RSRC=git
elif [ -s "${HERE}/.source-commit" ]; then
    read -r REV RDATE _ < "${HERE}/.source-commit"; RSRC=stamp
else
    REV=
fi
case ${REV} in *[!0-9a-f]*) REV=; RDATE=; RSRC=unknown ;; esac
install -d -m 0755 /usr/local/share/os-fanctl
printf '{"commit": "%s", "date": "%s", "source": "%s", "installed_at": %s}\n' \
    "${REV}" "${RDATE}" "${RSRC}" "$(date +%s)" > /usr/local/share/os-fanctl/installed.json

echo "Fan Control installed: the page is at System > Fan Control"
echo "the widget is at Lobby > Dashboard, under Add widget > Fan Control"
echo "nothing is controlling a fan yet, and nothing will until you say so on that page"

# An update leaves a running controller running the code it started with - python has
# already read the old files into memory - and this script will not restart it uninvited.
# A restart is not free here: it hands the fans back to the hardware, waits, and takes them
# again, and the right moment for that is the owner's to choose.
#
# Except where it holds nothing: in watch-only mode, with no fan driven, a restart hands back
# nothing and takes nothing, and leaving the old code running is all it saves. Three appliances
# updated by hand on 2026-10-19 went on naming sensors by their sysctl, the old code's way,
# until the controller was restarted.
if /usr/local/etc/rc.d/fanctl status > /dev/null 2>&1; then
    if /usr/local/opnsense/scripts/fanctl/fanctl.py status 2> /dev/null | /usr/local/bin/python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if d.get("mode") == "monitor" and not any(f.get("driven") for f in d.get("fans") or []) else 1)
'; then
        configctl fanctl restart > /dev/null 2>&1 || true
        echo "the controller only watches and holds no fan, so it was restarted onto this code"
    else
        echo "the controller is still running the code that was installed before this one;"
        echo "restart it from the page, or with:  configctl fanctl restart"
    fi
fi

# And the one thing worth shouting about, if it is true.
if [ -f /var/db/fanctl/incident.json ]; then
    echo
    echo "WARNING: /var/db/fanctl/incident.json exists, which means a previous restore"
    echo "         FAILED. The fans may still be held at a duty this plugin wrote. Power"
    echo "         the appliance off and on; a warm reboot does not necessarily clear a"
    echo "         fan register."
fi
