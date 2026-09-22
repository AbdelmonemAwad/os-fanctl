# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
# SPDX-License-Identifier: BSD-2-Clause
#
# fanctl-io is built on the firewall, by the firewall's own clang, against the
# firewall's own headers. That is not a compromise - it is the reason this
# approach survives an OPNsense update at all. The program is ordinary
# userland: it links against nothing but libc, uses no kernel KPI, and knows
# nothing about the running kernel's version. A kernel module in its place
# would have to match __FreeBSD_version exactly and would stop loading the
# first time OPNsense moved the kernel, on a firewall whose fans it was
# supposed to be looking after.
#
# /usr/bin/cc and /usr/bin/clang are present on OPNsense 26.7.4_1 (V, checked
# on the reference machine), so there is nothing to install before this builds.
#
# PACKAGING NOTE, for whoever assembles the plugin: OPNsense's plugins.mk
# install target copies every file under src/ into ${LOCALBASE} verbatim, so
# leaving this directory here would ship fanctl-io.c and this Makefile into
# /usr/local/fanctl-io/. Either move this directory to contrib/fanctl-io -
# plugins.mk runs `make -C contrib install` precisely for things that are not
# plain file copies - or filter it out when the plist is generated. The binary
# belongs beside chip.py at ${SCRIPTSDIR}, which is what `make install` below
# does.

PREFIX?=	/usr/local
SCRIPTSDIR?=	${PREFIX}/opnsense/scripts/fanctl

PROG=		fanctl-io
SRCS=		fanctl-io.c
MAN=

BINDIR=		${SCRIPTSDIR}
BINMODE=	0755
BINOWN?=	root
BINGRP?=	wheel

# The whole program is about refusing to do the wrong thing to a register, so
# it is built with the warnings turned all the way up and every one of them
# fatal. A sign-conversion warning in this file is not pedantry: it is a duty
# byte that is not the duty byte somebody asked for.
WARNS?=		6

.include <bsd.prog.mk>
