#!/bin/sh
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
# Where this plugin's page goes in the menu. On an XGS appliance - os-xgs-npu installed - it is in the
# XGS section with the appliance's own pages; anywhere else it is in the section its subject belongs to.
# OPNsense's menu file takes no condition, so the file is written here: by this plugin's install, and
# again by os-xgs-npu's install and removal, which run every /usr/local/opnsense/scripts/*/place-menu.sh.
# The menu file is not one of the package's files for that reason; the plugin's removal deletes it.
#
#   place-menu.sh            decide from os-xgs-npu's presence
#   place-menu.sh xgs|own    put it there whatever is installed (a test)
#
# Prints where it put it: "xgs" or "own".
#
MODEL=FanCtl
HERE=$(cd "$(dirname "$0")" && pwd)
MENU=/usr/local/opnsense/mvc/app/models/OPNsense/${MODEL}/Menu/Menu.xml
XGS=/usr/local/opnsense/mvc/app/models/OPNsense/XGS/Menu/Menu.xml
case "${1:-}" in
xgs|own)
	WHERE=$1
	;;
*)
	if [ -f "${XGS}" ]; then WHERE=xgs; else WHERE=own; fi
	;;
esac
[ -f "${HERE}/menu/${WHERE}.xml" ] || { echo "no ${HERE}/menu/${WHERE}.xml" >&2; exit 1; }
mkdir -p "$(dirname "${MENU}")"
cp "${HERE}/menu/${WHERE}.xml" "${MENU}.new" && chmod 644 "${MENU}.new" && mv "${MENU}.new" "${MENU}"
# The menu is cached by PHP in its own temporary directory on 26.7; /tmp is where it was before.
rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /tmp/opnsense_menu_cache.xml
echo "${WHERE}"
