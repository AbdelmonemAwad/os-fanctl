#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Build an installable OPNsense plugin package from this repository.
#
#   sh tools/make-package.sh [-o OUTDIR] [-c CATEGORY]
#
# It runs on the firewall, because the thing it builds is a FreeBSD package and pkg(8) is
# the only program that writes one. It does not need root and it installs nothing: the
# staging happens in a temporary directory that is removed on the way out, and the result
# is one .pkg file whose path and SHA256 you are told.
#
# WHAT THE MAKEFILE GIVES AND WHAT THIS HAS TO SUPPLY
#
# The Makefile carries PLUGIN_NAME, PLUGIN_VERSION, PLUGIN_COMMENT, PLUGIN_MAINTAINER and
# PLUGIN_DEPENDS, and that is all it carries. Everything else in a package manifest comes
# from ../../Mk/plugins.mk inside the OPNsense plugins tree, which this repository is not
# in yet. So this script supplies, by hand:
#
#   origin        opnsense/os-<name>, the path the plugin will have in that tree
#   prefix        /usr/local
#   licence       BSD2CLAUSE, licenselogic single
#   www           PLUGIN_WWW, or this repository on GitHub
#   category      -c, or PLUGIN_CATEGORY, or misc (metadata only; see below)
#   abi/arch      FreeBSD:*:*, because src/ holds no compiled object; see below
#   deps          origin and version looked up with pkg, never guessed
#   annotations   the product_* set OPNsense reads
#
# and it writes the one file the hand installation never wrote: /usr/local/opnsense/
# version/<name>. Every OPNsense plugin ships one - it is in the file list of os-smart,
# os-vnstat and all the rest - and that file is what the framework reads when it is asked
# whether the plugin is installed:
#
#   opnsense-version -c linkhealth     # exit 0 if it is there, 1 if it is not
#
# The argument is the name of the marker file, which is PLUGIN_NAME - not os-linkhealth.
# opnsense-version takes the marker to read as its operand and defaults to "core", so
# "opnsense-version -c os-linkhealth" looks for a file this plugin does not ship, and
# answers "not installed" for a plugin that is installed.

set -e

OUTDIR=
CATEGORY=

usage()
{
	echo "usage: sh tools/make-package.sh [-o OUTDIR] [-c CATEGORY]" >&2
	echo "  -o  where to leave the .pkg file (default: the current directory)" >&2
	echo "  -c  pkg category (default: PLUGIN_CATEGORY from the Makefile, or misc)" >&2
	exit 2
}

while getopts "o:c:h" opt; do
	case "${opt}" in
	o)	OUTDIR=${OPTARG} ;;
	c)	CATEGORY=${OPTARG} ;;
	*)	usage ;;
	esac
done

# Refuse before touching anything, and say what to do instead.
if [ "$(uname -s)" != "FreeBSD" ]; then
	echo "make-package.sh builds a FreeBSD package, so it has to run on FreeBSD." >&2
	echo "This is $(uname -s). Copy this repository to the firewall and run it there:" >&2
	echo "  scp -r . root@firewall:/root/plugin-src" >&2
	echo "  ssh root@firewall 'cd /root/plugin-src && sh tools/make-package.sh -o /root'" >&2
	exit 1
fi

if ! command -v pkg > /dev/null 2>&1; then
	echo "pkg(8) is not in PATH, and it is the program that writes the package." >&2
	exit 1
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)

if [ ! -f "${ROOT}/Makefile" ] || [ ! -d "${ROOT}/src" ]; then
	echo "${ROOT} does not look like a plugin repository: no Makefile, or no src/." >&2
	exit 1
fi

# One value out of the Makefile. Accepts =, ?= and +=, trims the tabs the OPNsense plugin
# Makefiles line their values up with, and drops a trailing comment the way make does.
#
# The comment is not a nicety. make ends a line at the first unescaped #, and the plugins
# tree writes lines that rely on it - security/openvpn-legacy/Makefile:5 and
# security/strongswan-legacy/Makefile:5 are both "PLUGIN_DEPENDS=<tab><tab># openvpn",
# which is an empty PLUGIN_DEPENDS with a note beside it. Read literally, that value is
# "# openvpn": two dependencies named "#" and "openvpn", neither of which pkg has ever
# heard of, and the build stops with a message about a package called "#". The quieter
# half is worse - "PLUGIN_COMMENT=<tab>Link health # reword this" would sail through and
# ship a package whose one-line description carries the author's note to himself.
mk_get()
{
	sed -n "s/^$1[[:space:]]*[?+]*=[[:space:]]*//p" "${ROOT}/Makefile" |
	    sed -e '1!d' \
	        -e 's/\([^\\]\)#.*$/\1/' \
	        -e 's/^#.*$//' \
	        -e 's/\\#/#/g' \
	        -e 's/[[:space:]]*$//'
}

# A value on its way into a quoted UCL string. Without this, one double quote in
# PLUGIN_COMMENT or in -c ends the string early and pkg rejects the manifest with a
# column number and nothing else.
ucl_quote()
{
	printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

PLUGIN_NAME=$(mk_get PLUGIN_NAME)
PLUGIN_VERSION=$(mk_get PLUGIN_VERSION)
PLUGIN_COMMENT=$(mk_get PLUGIN_COMMENT)
PLUGIN_MAINTAINER=$(mk_get PLUGIN_MAINTAINER)
PLUGIN_DEPENDS=$(mk_get PLUGIN_DEPENDS)
PLUGIN_WWW=$(mk_get PLUGIN_WWW)

for var in PLUGIN_NAME PLUGIN_VERSION PLUGIN_COMMENT PLUGIN_MAINTAINER; do
	eval "value=\${${var}}"
	if [ -z "${value}" ]; then
		echo "${var} is missing from ${ROOT}/Makefile; cannot build a package." >&2
		exit 1
	fi
done

PKGNAME=os-${PLUGIN_NAME}
ORIGIN=opnsense/${PKGNAME}
PREFIX=/usr/local
: "${PLUGIN_WWW:=https://github.com/AbdelmonemAwad/${PKGNAME}}"

# The category. In the OPNsense plugins tree it is the directory the plugin sits in -
# net/vnstat becomes categories ["net"], sysutils/smart becomes ["sysutils"] - so until
# this repository is in that tree there is nothing to read it from. It is metadata and
# nothing more: System > Firmware > Plugins reads name, version, comment, size, locked,
# automatic, licence, repository and origin, and never the category. Pick the one the
# plugin will have upstream and it is already right when it gets there.
if [ -z "${CATEGORY}" ]; then
	CATEGORY=$(mk_get PLUGIN_CATEGORY)
fi
: "${CATEGORY:=misc}"

if [ -z "${OUTDIR}" ]; then
	OUTDIR=$(pwd)
fi
mkdir -p "${OUTDIR}"
OUTDIR=$(cd "${OUTDIR}" && pwd)

WORK=$(mktemp -d -t "${PKGNAME}") || {
	echo "could not make a temporary directory" >&2
	exit 1
}
trap 'rm -rf "${WORK}"' EXIT HUP INT TERM

STAGE=${WORK}/stage
META=${WORK}/meta
mkdir -p "${STAGE}${PREFIX}" "${META}"

# ------------------------------------------------------------- staging, modes, arch
#
# git in this repository records no executable bits - every file is 100644 - so the mode
# cannot be taken from the checkout, and install/install.sh does not take it from there
# either: it names the files that need the bit and chmods them. The rule below is that
# same decision made from the file itself instead of from a list that can fall behind:
# a file whose first two bytes are #! is run, and gets 0755; everything else is read, and
# gets 0644. On this repository that selects exactly the set install/install.sh names.
#
# Ownership goes into the plist rather than through chown, so that the build needs no root
# and pkg still installs every file owned by root:wheel.
#
# The same pass looks for an ELF header, because the answer decides the ABI below.

# Only regular files are walked, so a symbolic link would be left out of the package
# without appearing anywhere in the output. pkg can carry one, through a plist keyword
# this script does not write, so say so rather than ship a package that is quietly
# missing a file the repository has.
if find "${ROOT}/src" -type l | grep -q .; then
	echo "src/ contains a symbolic link, and this script packages regular files only:" >&2
	find "${ROOT}/src" -type l | sed -e "s|^${ROOT}/|  |" >&2
	echo "It would be dropped from the package silently. Replace it with a real file," >&2
	echo "or teach this script the plist keyword for links, before building." >&2
	exit 1
fi

find "${ROOT}/src" -type f | sed "s|^${ROOT}/src/||" | LC_ALL=C sort > "${WORK}/files"

if [ ! -s "${WORK}/files" ]; then
	echo "src/ is empty; there is nothing to package." >&2
	exit 1
fi

: > "${WORK}/data"
: > "${WORK}/exec"
COMPILED=

while read -r rel; do
	from=${ROOT}/src/${rel}
	to=${STAGE}${PREFIX}/${rel}
	mkdir -p "$(dirname "${to}")"
	cp -p "${from}" "${to}"
	if [ "$(head -c 4 "${from}" | od -A n -t x1 | tr -d ' ')" = "7f454c46" ]; then
		COMPILED="${COMPILED} ${rel}"
	fi
	if [ "$(head -c 2 "${from}")" = "#!" ]; then
		chmod 0755 "${to}"
		echo "${PREFIX}/${rel}" >> "${WORK}/exec"
	else
		chmod 0644 "${to}"
		echo "${PREFIX}/${rel}" >> "${WORK}/data"
	fi
done < "${WORK}/files"

# THE ABI, AND WHY IT IS NOT THIS MACHINE'S.
#
# OPNsense builds its own plugins once per release and stamps each with the concrete ABI
# of that release - os-smart on 26.7 is FreeBSD:15:amd64 - because the whole set is
# rebuilt when the release moves. A package built out of tree is not rebuilt by anybody,
# so a concrete ABI would write an expiry date into it for no reason: this plugin is PHP,
# Python, XML, Volt and JavaScript, and none of that is compiled against anything.
# FreeBSD:*:* says exactly that, and it is not a novelty - os-abuseipdb, a third-party
# OPNsense plugin, ships FreeBSD:*:* and installs and runs on this machine although it
# was built on FreeBSD 14.
#
# If src/ ever does get a compiled object, the claim stops being true, so it is checked
# rather than assumed, and the package is then stamped with this machine's real ABI.
if [ -n "${COMPILED}" ]; then
	PKGABI=$(pkg config ABI)
	PKGARCH=$(pkg config ALTABI)
	echo "note: src/ contains a compiled object:${COMPILED}"
	echo "note: the package is therefore stamped ${PKGABI} and will not survive"
	echo "note: an OPNsense release that moves to another FreeBSD major version."
else
	PKGABI="FreeBSD:*:*"
	PKGARCH="freebsd:*:*"
fi

# ------------------------------------------------- provenance and a repeatable build
#
# The commit is recorded in the package, and the commit date is used as the timestamp of
# every file in the archive. Without that second part two checkouts of the same commit
# produce two different files - pkg stores each file's mtime, and a fresh clone's mtimes
# are the time of the clone - and the SHA256 printed at the end means nothing to anybody
# but the person who ran it.
#
# It buys less than it looks like it does, and the end of this script says so rather than
# claiming otherwise: product_abi and product_arch below are read off the machine doing
# the building and go into the manifest, so the same commit built on OPNsense 26.1 and on
# 26.7 gives two different packages and two different digests. The digest is comparable
# between two people on the same release, which is the case that matters when somebody
# posts one, and it is not comparable across releases.

HASH=unknown
if command -v git > /dev/null 2>&1 && git -C "${ROOT}" rev-parse --git-dir > /dev/null 2>&1; then
	# nine characters, which is the length OPNsense's own product_hash uses
	HASH=$(git -C "${ROOT}" rev-parse --short=9 HEAD 2> /dev/null) || HASH=unknown
	if [ -z "${SOURCE_DATE_EPOCH}" ]; then
		SOURCE_DATE_EPOCH=$(git -C "${ROOT}" log -1 --format=%ct 2> /dev/null) || SOURCE_DATE_EPOCH=
		[ -n "${SOURCE_DATE_EPOCH}" ] && export SOURCE_DATE_EPOCH
	fi
fi

# A firewall does not necessarily have git on it - this one only does because
# os-git-backup pulled it in - and git also refuses a checkout it thinks somebody else
# owns, which is what a repository unpacked from a tarball as another user looks like.
# The commit is worth recording either way, so read it out of the checkout directly.
# Three layouts, because a checkout can be in any of them: a loose ref, a ref that
# git gc has moved into .git/packed-refs, and a detached HEAD holding the hash itself.
if [ "${HASH}" = "unknown" ] && [ -f "${ROOT}/.git/HEAD" ]; then
	gitref=$(sed -n 's/^ref: //p' "${ROOT}/.git/HEAD")
	if [ -n "${gitref}" ] && [ -f "${ROOT}/.git/${gitref}" ]; then
		HASH=$(cut -c 1-9 < "${ROOT}/.git/${gitref}")
	elif [ -n "${gitref}" ] && [ -f "${ROOT}/.git/packed-refs" ]; then
		HASH=$(grep " ${gitref}\$" "${ROOT}/.git/packed-refs" |
		    sed -e '1!d' | cut -c 1-9) || HASH=
		[ -n "${HASH}" ] || HASH=unknown
	elif [ -z "${gitref}" ]; then
		HASH=$(cut -c 1-9 < "${ROOT}/.git/HEAD")
	fi
fi

# On a firewall this is what opnsense-version answers. The release workflow builds in a plain
# FreeBSD virtual machine, which has no such program, and passes the answer in PRODUCT_ABI
# instead - so a value already in the environment is kept. Without this the first release run
# (v1.0) recorded product_abi "unknown" and the workflow refused to publish it.
if [ -z "${PRODUCT_ABI:-}" ]; then
	PRODUCT_ABI=$(opnsense-version -a 2> /dev/null) || PRODUCT_ABI=
fi
: "${PRODUCT_ABI:=unknown}"
PRODUCT_ARCH=$(uname -p)

# ------------------------------------------------------------------- version marker
#
# The same JSON every OPNsense plugin ships at /usr/local/opnsense/version/<name>. It is
# a packaged file, not something a script writes afterwards, so removing the package
# removes it and the framework stops claiming the plugin is there.
#
# product_tier is 4 on purpose. The GUI forces tier 4 for anything that did not come from
# a repository it trusts, and this did not; writing 3 here would only be a claim the GUI
# overrides anyway.

VERSIONFILE=${STAGE}${PREFIX}/opnsense/version/${PLUGIN_NAME}
mkdir -p "$(dirname "${VERSIONFILE}")"
cat > "${VERSIONFILE}" << MARKER
{
    "product_abi": "${PRODUCT_ABI}",
    "product_arch": "${PRODUCT_ARCH}",
    "product_conflicts": "${PKGNAME}-devel",
    "product_email": "${PLUGIN_MAINTAINER}",
    "product_hash": "${HASH}",
    "product_id": "${PKGNAME}",
    "product_name": "${PLUGIN_NAME}",
    "product_tier": "4",
    "product_version": "${PLUGIN_VERSION}",
    "product_website": "${PLUGIN_WWW}"
}
MARKER
chmod 0644 "${VERSIONFILE}"
echo "${PREFIX}/opnsense/version/${PLUGIN_NAME}" >> "${WORK}/data"

LC_ALL=C sort -o "${WORK}/data" "${WORK}/data"

# --------------------------------------------------------------------------- plist
{
	echo "@owner root"
	echo "@group wheel"
	echo "@mode 0644"
	cat "${WORK}/data"
	if [ -s "${WORK}/exec" ]; then
		echo "@mode 0755"
		cat "${WORK}/exec"
	fi
} > "${WORK}/plist"

# ------------------------------------------------------------------------ manifest
#
# UCL, with heredoc strings for everything that has newlines in it, so that nothing from
# pkg-descr or from pkg/*.sh has to be escaped on its way in.
#
# A heredoc string ends at a line that is exactly the terminator, which puts two ways to
# lose one in the path of an ordinary editing mistake, and both of them end in a package
# that builds, reports success and is wrong:
#
#   a file that contains a line equal to the terminator ends the string early, and its
#   own remaining lines are then read as manifest syntax;
#
#   a file whose last byte is not a newline carries the terminator onto its own last
#   line - "echo hiPKGSCRIPT" - so the string never ends. The next key and the next
#   script are swallowed into it. A package built that way installs with post-install
#   holding nothing that runs and post-deinstall absent altogether: configd is never
#   restarted, /var/db is never created, the plugin is never registered in config.xml,
#   and removing it later leaves that registration and the node_exporter file behind.
#   pkg does not object, because what it was handed is valid.
#
# So: refuse the first, repair the second, and count the terminators afterwards.

# $1 = file to inline. Emits the body and a terminator that is guaranteed to start a line.
manifest_body()
{
	cat "$1"
	# tail -c 1 through $() loses a trailing newline, so an empty result means the
	# file already ended in one.
	[ -z "$(tail -c 1 "$1")" ] || echo
}

# $1 = the terminator word, $2 = file. Refuses a file that contains the terminator alone
# on a line. The terminators are fixed all-capitals words because UCL will not take a
# hyphen in one - name the terminator after the key and "post-install" never starts a
# heredoc at all, and the shell script is then read as if it were manifest syntax, which
# fails a hundred lines later with a useless message.
check_terminator()
{
	if grep -q "^$1\$" "$2"; then
		echo "$2 has a line reading $1, which is this manifest's heredoc" >&2
		echo "terminator. Change that line." >&2
		exit 1
	fi
}

# $1 = key in the scripts block, $2 = file under pkg/ to inline; silent if absent.
# The key is quoted because post-install has a hyphen in it.
manifest_script()
{
	[ -f "${ROOT}/pkg/$2" ] || return 0
	check_terminator PKGSCRIPT "${ROOT}/pkg/$2"
	echo "    \"$1\" = <<PKGSCRIPT"
	manifest_body "${ROOT}/pkg/$2"
	echo "PKGSCRIPT"
}

if [ -f "${ROOT}/pkg-descr" ]; then
	check_terminator PKGDESCR "${ROOT}/pkg-descr"
fi

{
	echo "name = \"$(ucl_quote "${PKGNAME}")\";"
	echo "version = \"$(ucl_quote "${PLUGIN_VERSION}")\";"
	echo "origin = \"$(ucl_quote "${ORIGIN}")\";"
	echo "comment = \"$(ucl_quote "${PLUGIN_COMMENT}")\";"
	echo "maintainer = \"$(ucl_quote "${PLUGIN_MAINTAINER}")\";"
	echo "www = \"$(ucl_quote "${PLUGIN_WWW}")\";"
	echo "prefix = \"${PREFIX}\";"
	echo "abi = \"${PKGABI}\";"
	echo "arch = \"${PKGARCH}\";"
	echo "categories = [ \"$(ucl_quote "${CATEGORY}")\" ];"
	echo "licenselogic = \"single\";"
	echo "licenses = [ \"BSD2CLAUSE\" ];"

	echo "desc = <<PKGDESCR"
	if [ -f "${ROOT}/pkg-descr" ]; then
		manifest_body "${ROOT}/pkg-descr"
	else
		echo "${PLUGIN_COMMENT}"
	fi
	echo "PKGDESCR"

	# Dependencies. pkg wants an origin and a version beside the name, and the only
	# honest source for those on this machine is pkg itself: what is installed, then
	# the repository catalogue as it was last fetched (-U, so this neither tries to
	# update it nor needs root). If neither knows the package, stop - a manifest with
	# a guessed dependency version installs and then misbehaves.
	if [ -n "${PLUGIN_DEPENDS}" ]; then
		echo "deps {"
		for dep in ${PLUGIN_DEPENDS}; do
			dorigin=$(pkg query %o "${dep}" 2> /dev/null) || dorigin=
			dversion=$(pkg query %v "${dep}" 2> /dev/null) || dversion=
			if [ -z "${dorigin}" ]; then
				dorigin=$(pkg rquery -U %o "${dep}" 2> /dev/null | sed -e '1!d') || dorigin=
				dversion=$(pkg rquery -U %v "${dep}" 2> /dev/null | sed -e '1!d') || dversion=
			fi
			if [ -z "${dorigin}" ] || [ -z "${dversion}" ]; then
				echo "PLUGIN_DEPENDS names ${dep}, which pkg does not know." >&2
				echo "It is neither installed here nor in the repository" >&2
				echo "catalogue, so its origin and version cannot be filled" >&2
				echo "in. Fix that first: pkg update, or install ${dep}." >&2
				exit 1
			fi
			echo "    ${dep} { origin = \"${dorigin}\"; version = \"${dversion}\"; }"
		done
		echo "}"
	fi

	# The annotations OPNsense reads. Same set as the version marker; pkg annotate
	# shows them on the installed package.
	echo "annotations {"
	echo "    product_abi = \"${PRODUCT_ABI}\";"
	echo "    product_arch = \"${PRODUCT_ARCH}\";"
	echo "    product_conflicts = \"${PKGNAME}-devel\";"
	echo "    product_email = \"$(ucl_quote "${PLUGIN_MAINTAINER}")\";"
	echo "    product_hash = \"${HASH}\";"
	echo "    product_id = \"${PKGNAME}\";"
	echo "    product_name = \"${PLUGIN_NAME}\";"
	echo "    product_tier = \"4\";"
	echo "    product_version = \"$(ucl_quote "${PLUGIN_VERSION}")\";"
	echo "    product_website = \"$(ucl_quote "${PLUGIN_WWW}")\";"
	echo "}"

	echo "scripts {"
	manifest_script pre-install pre-install.sh
	manifest_script post-install post-install.sh
	manifest_script pre-deinstall pre-deinstall.sh
	manifest_script post-deinstall post-deinstall.sh
	echo "}"
} > "${META}/+MANIFEST"

# Every heredoc that was opened has to have been closed. manifest_body makes that true;
# this proves it on the file that is about to be handed to pkg, because the failure it
# guards against is the kind that produces a package rather than an error.
for word in PKGSCRIPT PKGDESCR; do
	opened=$(grep -c "= <<${word}\$" "${META}/+MANIFEST") || opened=0
	closed=$(grep -c "^${word}\$" "${META}/+MANIFEST") || closed=0
	if [ "${opened}" -ne "${closed}" ]; then
		echo "the manifest opened ${opened} ${word} strings and closed ${closed}." >&2
		echo "A file under pkg/ or pkg-descr contains the terminator, or the" >&2
		echo "manifest writer is wrong. Not building a package from it." >&2
		exit 1
	fi
done

# ----------------------------------------------------------------------- build it
pkg create -m "${META}" -p "${WORK}/plist" -r "${STAGE}" -o "${OUTDIR}"

RESULT=${OUTDIR}/${PKGNAME}-${PLUGIN_VERSION}.pkg
if [ ! -f "${RESULT}" ]; then
	# pkg chooses the extension from the compression format; find it rather than
	# insist on the name
	RESULT=$(ls -t "${OUTDIR}/${PKGNAME}-${PLUGIN_VERSION}".* 2> /dev/null | sed -e '1!d')
fi
if [ ! -f "${RESULT}" ]; then
	echo "pkg create reported success but no package appeared in ${OUTDIR}." >&2
	exit 1
fi

echo
echo "${RESULT}"
echo "SHA256 $(sha256 -q "${RESULT}")"
echo "commit ${HASH}"
echo "built on OPNsense ${PRODUCT_ABI} ${PRODUCT_ARCH}"
if [ -n "${SOURCE_DATE_EPOCH}" ]; then
	echo "this commit built again on OPNsense ${PRODUCT_ABI} gives the same bytes and"
	echo "the same SHA256. Another release will not: ${PRODUCT_ABI} is recorded in the"
	echo "package, so compare digests with somebody on the same release."
else
	echo "note: the commit date could not be read here, so every file in the archive"
	echo "note: carries its own mtime and two builds of this commit will not have the"
	echo "note: same SHA256. For a digest two people can compare, pass the date in:"
	echo "note:   SOURCE_DATE_EPOCH=\$(git log -1 --format=%ct) sh tools/make-package.sh"
fi
echo
echo "read it before installing it:"
echo "  pkg info -F '${RESULT}'"
echo "  pkg info -lF '${RESULT}'"
echo "install it with:"
echo "  pkg add '${RESULT}'"
