#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Run by pkg(8) BEFORE the files of os-fanctl are removed, and that is the whole reason this
# file exists rather than being three more lines in post-deinstall: the rc script that stops
# the controller, and the Python that hands the fans back, are themselves packaged files. By
# post-deinstall time they are gone and a daemon may still be running with a duty written to
# a fan register - a duty nothing left on the machine knows how to undo.
#
# So this is the most important script in the package, and it does exactly two things: stop
# the controller, which hands the fans back on its way out, and then ask again directly, in
# case there was nothing running but something still held.
#
# onestop rather than stop, because the boot switch may be off and a stop has to stop.

# pkg runs the OLD package's pre-deinstall in the middle of an upgrade too, and this script
# deliberately does nothing then. Stopping would hand the fans back to the firmware and leave
# them there - the new package's post-install does not start anything, by design - so a
# machine whose owner had set up a fan curve would quietly lose it at every update until the
# next reboot. A controller that keeps running across an upgrade is running code python has
# already read into memory, with every one of its own failsafes intact, and post-install says
# plainly that it is doing so. PKG_UPGRADE is set in exactly that case and in no other.
if [ -n "${PKG_UPGRADE}" ]; then
	exit 0
fi

if [ -x /usr/local/etc/rc.d/fanctl ]; then
	/usr/local/etc/rc.d/fanctl onestop || true
fi

# Asked again, directly. The stop above may have found nothing running while the state files
# say a duty is still held, which is exactly what a machine looks like after a daemon was
# killed rather than stopped - and this is the last moment in the life of this package at
# which anything can be done about it.
if [ -x /usr/local/opnsense/scripts/fanctl/fanctl.py ]; then
	if ! /usr/local/opnsense/scripts/fanctl/fanctl.py restore > /dev/null 2>&1; then
		echo "WARNING: the fans could NOT be handed back to the hardware, and the tools"
		echo "         that could try again are about to be removed. Power the appliance"
		echo "         off and on to return them to firmware control; a warm reboot does"
		echo "         not necessarily clear a fan register."
	fi
fi
