#!/bin/sh
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
# Every file install/install.sh lays down from src/ is removed by install/uninstall.sh - by its own
# path, or by an `rm -rf` of a directory above it.
#
# The installer copies src/ onto /usr/local whole, so a file added to src/ is installed without
# anyone deciding to; the uninstaller names what it removes, so the same file is left behind unless
# somebody remembers. Left behind, a plugins.inc.d hook outlives the classes it calls and stops
# OPNsense's boot before the network (os-frontpanel issue 11, on an XGS 136), and a cron entry goes
# on calling a script that is gone. Nothing complains until a removal and a reboot - so this does.
#
#   tools/check-uninstall.sh [repository root]
set -eu
ROOT=${1:-.}
U="${ROOT}/install/uninstall.sh"
bad=0
for f in $(cd "${ROOT}" && find src -type f | sort); do
    target="/usr/local/${f#src/}"
    grep -qF "${target}" "${U}" && continue
    covered=0
    d=$(dirname "${target}")
    while [ "${d}" != /usr/local ] && [ "${d}" != / ]; do
        if grep -qE "rm -r[f]? [^#]*${d}([[:space:]]|\$)" "${U}"; then
            covered=1
            break
        fi
        d=$(dirname "${d}")
    done
    if [ "${covered}" = 0 ]; then
        echo "not removed by install/uninstall.sh: ${target}"
        bad=1
    fi
done
[ "${bad}" = 0 ] && echo "every file the installer lays down is removed by the uninstaller"
exit "${bad}"
