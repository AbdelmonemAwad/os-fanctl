#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
PLUGIN_NAME=		fanctl
PLUGIN_VERSION=		1.1.2
PLUGIN_COMMENT=		Fan control for appliances whose vendor fan curve is gone
PLUGIN_MAINTAINER=	eg2@live.com

# No PLUGIN_DEPENDS. Everything this plugin needs is in the base system: python3 for the
# controller, daemon(8) to supervise it, clang to build the port helper, and /dev/io for the
# helper to use. Nothing is installed on the owner's behalf, which is also why nothing here
# pulls in a sensor package: the temperatures this plugin reads are the ones the kernel
# already publishes.
#
# ONE SEAM IS DELIBERATELY LEFT VISIBLE HERE rather than papered over, because it is a
# packaging decision for whoever builds the package and not a fact about the plugin.
#
# src/fanctl-io/ is C, and plugins.mk copies everything under src/ into ${LOCALBASE}
# verbatim - which would ship fanctl-io.c and its Makefile into /usr/local/fanctl-io/ and
# install no binary at all. There are two honest ways to finish it, and both are written
# down because the right one depends on how the package is assembled:
#
#   * move that directory to contrib/fanctl-io and let `make -C contrib install` build it,
#     which is what plugins.mk provides a contrib hook for; or
#
#   * leave it where it is and let the source ship - pkg/post-install.sh builds it from
#     /usr/local/fanctl-io on first install, which is what it does today.
#
# install/install.sh, the path somebody uses from a copy of this repository, builds it either
# way. Until the seam is closed one way or the other, a package built from this tree installs
# a plugin that reads correctly and cannot write - which is the safe half to get wrong.

.include "../../Mk/plugins.mk"
