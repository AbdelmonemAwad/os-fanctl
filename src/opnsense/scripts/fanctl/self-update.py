#!/usr/local/bin/python3
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
"""Follow os-fanctl's published releases on GitHub. Run from XGS: Updates
(os-xgs-npu's Plugins tab) or by hand; until 2026-10-20 it ran once a day from cron.

    self-update.py          take the latest release if it is ahead of what is installed
    self-update.py check    say what it would do, and change nothing

WHAT IT FOLLOWS. The repository's latest release - not main. A release is a tag its owner chose
to publish, so a change merged on main reaches an installed firewall when it is released, not
before. With no release published this does nothing.

SIGNED RELEASES (since 2026-11-07). A release is installed only as its owner signed it. Three files
are published with each release on GitHub: os-fanctl-<tag>.tar.gz, the tag's tree made by `git
archive`; release.json, the manifest, naming the tag, its commit and its date and the archive's
SHA-256 and size; and release.json.sig, the manifest's Ed25519 signature, made with the project's
release key by its owner's private signing repository - never by this repository's own CI. The
public keys are installed beside this script, in release-keys/. This reads the latest release's
manifest - GitHub's download of the latest release's file, not its API, so not the API's hourly
allowance either - and takes the release only when its signature verifies with one of those keys;
it downloads the archive the manifest names, and installs it only when its SHA-256 and size are the
ones signed. A release that is not signed is not taken. Until then GitHub's API, or npuforge.org's
copy of it (SELF_UPDATE_API), named the release and its commit, and that commit's tarball was
installed as it came: a copy that was down, wrong or in other hands could choose what was installed.
GitHub's git endpoint is still asked for the tag's commit before the download, so a tag moved since
it was signed stops the update too.

ONLY FORWARD. What is installed is read from /usr/local/share/os-fanctl/installed.json, which
install/install.sh writes from a git checkout, or from the .source-commit stamp that an
xgs-image-builder image or this script leaves in the tree. The release is taken when it is another
commit and is not dated before what is installed - by the date signed; a firewall running something
newer, from main or from a branch, is not taken back. An install from before the stamp existed takes
the latest release once.

WHAT AN UPDATE IS. The release's source, unpacked and installed by the install/install.sh it carries -
the same script a hand install runs, so an update is never a different procedure from an install.
The output of the last one is kept in /usr/local/share/os-fanctl/update.log, and what happened in
/usr/local/share/os-fanctl/update.json and in the system log, as self-update.

A test firewall may read a release's files from another place, named in the one-line file
/usr/local/share/os-fanctl/releases-from (a URL, file:// too): what is installed must still be
signed with a key in release-keys/, so the place decides nothing.
"""
import datetime
import fcntl
import glob
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import syslog
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

NAME = 'os-fanctl'
REPO = 'AbdelmonemAwad/os-fanctl'
INSTALLER = 'install/install.sh'
SHARE = '/usr/local/share/os-fanctl'
# GitHub: its releases' files; its git endpoint's list of references, the one `git ls-remote` reads;
# and its API, asked for a tag's commit only when that list cannot be read.
RELEASES = 'https://github.com/' + REPO + '/releases'
REFS = 'https://github.com/' + REPO + '.git/info/refs?service=git-upload-pack'
API = 'https://api.github.com/repos/' + REPO
# The public keys a release must be signed with, and OpenSSL, which checks the signature.
KEYS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'release-keys')
OPENSSL = '/usr/bin/openssl'
MANIFEST = 'release.json'
MANIFEST_MAX = 64 << 10
ARCHIVE_MAX = 256 << 20
INSTALLED = os.path.join(SHARE, 'installed.json')
RESULT = os.path.join(SHARE, 'update.json')
LOG = os.path.join(SHARE, 'update.log')
LOCK = os.path.join(SHARE, 'update.lock')
RELEASES_FROM = os.path.join(SHARE, 'releases-from')
# Where an update downloads and unpacks; os-xgs-npu's Plugins tab follows its progress there.
WORK = '/var/tmp'
# cron starts this with /usr/bin:/bin, and the installers call configctl, php and python3.
PATH = '/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin'
AGENT = {'User-Agent': NAME + '-self-update'}


class Unsigned(Exception):
    """A release not taken as signed: its manifest missing, its signature not verifying, or what the
    manifest says not a release of this plugin - the reason, said as it is."""


def read_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            found = json.load(handle)
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def say(result):
    """The outcome, in the system log and in update.json, and on stdout for a hand run."""
    result['at'] = int(time.time())
    syslog.syslog(syslog.LOG_NOTICE, NAME + ': ' + result['message'])
    try:
        os.makedirs(SHARE, exist_ok=True)
        with open(RESULT + '.tmp', 'w', encoding='utf-8') as handle:
            json.dump(result, handle)
        os.replace(RESULT + '.tmp', RESULT)
    except OSError:
        pass
    print(json.dumps(result))
    return 0 if result['status'] != 'failed' else 1


def when(stamp):
    """Seconds since the epoch of an ISO 8601 time as git (%cI) and GitHub write them; 0 when there
    is none or it cannot be read."""
    try:
        return int(datetime.datetime.fromisoformat(str(stamp or '').replace('Z', '+00:00')).timestamp())
    except ValueError:
        return 0


def newer_than(installed_date, release_date):
    """True when what is installed is dated after the release: it is not taken back to it. Unknown
    either way, it is not - the release is taken."""
    have, want = when(installed_date), when(release_date)
    return bool(have and want and have > want)


def releases_from():
    """Where a release's files are read: GitHub's releases, or the place a test firewall names."""
    try:
        with open(RELEASES_FROM, encoding='utf-8') as handle:
            place = handle.read().strip()
        if place:
            return place.rstrip('/')
    except OSError:
        pass
    return RELEASES


def release_url(tag, name):
    """A file published with release tag, or with the latest release when tag is ''."""
    if not tag:
        return '%s/latest/download/%s' % (releases_from(), name)
    return '%s/download/%s/%s' % (releases_from(), urllib.request.quote(tag, safe=''), name)


def release_bytes(tag, name, limit):
    """The file, read whole; one larger than limit is not a file of a release. A file missing from a
    place a test firewall names (file://) is GitHub's 404 for a file a release does not have."""
    url = release_url(tag, name)
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60)
    except urllib.error.URLError as error:
        if isinstance(getattr(error, 'reason', None), FileNotFoundError):
            raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None) from error
        raise
    with response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise Unsigned('%s of %s is larger than a release file of its kind can be'
                       % (name, tag or 'the latest release'))
    return data


def signature_key(data, signature):
    """The name of the public key in KEYS whose signature over data signature is - OpenSSL's own
    Ed25519 check - or '' when none is."""
    keys = sorted(glob.glob(os.path.join(KEYS, '*.pub')))
    if not keys or len(signature) != 64:
        return ''
    os.makedirs(SHARE, exist_ok=True)
    paths = []
    try:
        for content, prefix in ((data, '.manifest-'), (signature, '.signature-')):
            fd, path = tempfile.mkstemp(dir=SHARE, prefix=prefix)
            paths.append(path)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
        for key in keys:
            done = subprocess.run([OPENSSL, 'pkeyutl', '-verify', '-pubin', '-inkey', key, '-rawin',
                                   '-in', paths[0], '-sigfile', paths[1]], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=30, check=False)
            if done.returncode == 0:
                return os.path.basename(key)
    finally:
        for path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass
    return ''


FIELDS = {
    'repository': re.compile(re.escape(REPO) + r'\Z'),
    'tag': re.compile(r'v[0-9][0-9A-Za-z.+-]{0,62}\Z'),
    'commit': re.compile(r'[0-9a-f]{40}\Z'),
    'archive': re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,126}\.tar\.gz\Z'),
    'sha256': re.compile(r'[0-9a-f]{64}\Z'),
}


def named_tag(data):
    """The tag a manifest not yet verified names, as only where to read its signature; '' when it
    names none of the form a tag has, and the latest release's signature is read as before."""
    try:
        found = json.loads(data.decode('utf-8'))
    except (ValueError, RecursionError):
        return ''
    tag = found.get('tag') if isinstance(found, dict) else None
    return tag if isinstance(tag, str) and FIELDS['tag'].match(tag) else ''


def signed_manifest():
    """The manifest of the latest release: read, its signature verified with a key in KEYS, and every
    field it must have checked. Its fields, and 'key', the key's name; Unsigned when it is not to be
    taken. A network failure is the caller's to say."""
    what = 'the latest release'
    missing = MANIFEST
    try:
        data = release_bytes('', MANIFEST, MANIFEST_MAX)
        missing = MANIFEST + '.sig'
        # its signature by the tag the manifest names, not through 'latest' a second time: a release
        # published between the two reads paired one release's manifest with another's signature, and
        # the check cried tampering until the next one (os-xgs-npu's release check, 2026-11-07, K9).
        # The tag only says where to look; the signature still decides.
        signature = release_bytes(named_tag(data), missing, 64)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise Unsigned('%s is not signed yet: it has no %s' % (what, missing)) from error
        raise
    key = signature_key(data, signature)
    if not key:
        raise Unsigned('the signature of %s does not verify with %s' % (
            what, 'any release key of this firewall' if glob.glob(os.path.join(KEYS, '*.pub'))
            else "this firewall's release keys - it has none"))
    try:
        found = json.loads(data.decode('utf-8'))
    except ValueError as error:
        raise Unsigned('the manifest of %s, signed, is not JSON' % what) from error
    if not isinstance(found, dict) or found.get('format') != 1:
        raise Unsigned('the manifest of %s is of a format this self-update.py does not read' % what)
    for name, pattern in FIELDS.items():
        if not isinstance(found.get(name), str) or not pattern.match(found[name]):
            raise Unsigned('the manifest of %s has no %s, or not one of the form it must have' % (what, name))
    if not isinstance(found.get('date'), str) or not when(found['date']):
        raise Unsigned('the manifest of %s has no date' % what)
    size = found.get('size')
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= ARCHIVE_MAX:
        raise Unsigned('the manifest of %s has no size, or one no archive of it can have' % what)
    found['key'] = key
    return found


def tag_commit(tag):
    """The commit a release's tag names, as GitHub says it: from its git endpoint, the list of
    references `git ls-remote` reads - not the API, so not its hourly allowance. An annotated tag's
    own line is followed by the commit it points to, "^{}"; that is the one taken. When the list
    cannot be read, or has no such tag, GitHub's API is asked."""
    try:
        request = urllib.request.Request(REFS, headers={'User-Agent': 'git/2 ' + AGENT['User-Agent']})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(8 << 20)
        refs, at = {}, 0
        while at + 4 <= len(data):
            size = int(data[at:at + 4], 16)
            if size < 4:
                at += 4
                continue
            line = data[at + 4:at + size].split(b'\0')[0].strip().decode('ascii', 'replace')
            at += size
            if len(line) > 41 and line[40] == ' ':
                refs[line[41:]] = line[:40]
        found = refs.get('refs/tags/%s^{}' % tag) or refs.get('refs/tags/%s' % tag)
        if found:
            return found
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        pass
    request = urllib.request.Request('%s/commits/%s' % (API, urllib.request.quote(tag, safe='')),
                                     headers=dict(AGENT, Accept='application/vnd.github+json'))
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)['sha']


def plan():
    """The latest release, as its signed manifest says, and whether it is to be taken here (ONLY
    FORWARD): what to say of it, and the manifest."""
    have = read_json(INSTALLED)
    signed = signed_manifest()
    found = {'tag': signed['tag'], 'sha': signed['commit'], 'date': signed['date'],
             'installed': str(have.get('commit') or ''), 'signed_with': signed['key']}
    if found['installed'] == found['sha']:
        found['compare'] = 'identical'
    elif not found['installed']:
        found['compare'] = 'unknown'
    elif newer_than(have.get('date'), signed['date']):
        found['compare'] = 'behind'
    else:
        found['compare'] = 'ahead'
    # ahead: the release is another commit, not dated before what is installed. unknown: nothing is
    # recorded as installed. behind: what is installed is newer. identical: it is the release.
    found['take'] = found['compare'] in ('ahead', 'unknown')
    return found, signed


def unpack(archive, tree):
    """Everything under the one top directory the archive has (git archive's --prefix), nothing
    outside it."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        top = members[0].name.split('/')[0] if members else ''
        for member in members:
            if not member.name.startswith(top + '/') or '..' in member.name.split('/'):
                continue
            member.name = member.name[len(top) + 1:]
            if member.name:
                tar.extract(member, tree, filter='tar')


def update(found, signed):
    tag, sha = found['tag'], found['sha']
    # GitHub's own word for the tag as well as the signature (SIGNED RELEASES above): a tag moved
    # since it was signed stops the update before anything is downloaded.
    try:
        confirmed = tag_commit(tag)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError,
            http.client.HTTPException) as error:
        return say({'status': 'failed', 'tag': tag,
                    'message': '%s could not be confirmed with GitHub (%s); nothing was installed - try '
                               'again later' % (tag, error)})
    if confirmed != sha:
        return say({'status': 'failed', 'tag': tag,
                    'message': 'GitHub says %s is commit %s, not the %s it was signed as; nothing was '
                               'installed' % (tag, str(confirmed)[:12], sha[:12])})
    work = tempfile.mkdtemp(prefix=NAME + '-update.', dir=WORK)
    try:
        archive = os.path.join(work, 'release.tar.gz')
        tree = os.path.join(work, 'tree')
        # The archive the manifest names, read as far as its signed size and hashed on the way.
        request = urllib.request.Request(release_url(tag, signed['archive']), headers=AGENT)
        digest, size = hashlib.sha256(), 0
        with urllib.request.urlopen(request, timeout=120) as response, \
                open(archive, 'wb') as handle:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > signed['size']:
                    break
                digest.update(chunk)
                handle.write(chunk)
        if size != signed['size'] or digest.hexdigest() != signed['sha256']:
            over = size > signed['size']
            return say({'status': 'failed', 'tag': tag, 'message': (
                'the archive of %s is not the one signed: %s bytes, SHA-256 %s, where %d bytes, SHA-256 '
                '%s were signed; nothing was installed' % (
                    tag, 'more than %d' % signed['size'] if over else size,
                    '-' if over else digest.hexdigest()[:16], signed['size'], signed['sha256'][:16]))})
        os.makedirs(tree)
        unpack(archive, tree)
        if not os.path.isfile(os.path.join(tree, INSTALLER)):
            return say({'status': 'failed', 'tag': tag,
                        'message': '%s has no %s; nothing was installed' % (tag, INSTALLER)})
        # The installer records what it installed from this, as it does from a git checkout.
        with open(os.path.join(tree, '.source-commit'), 'w', encoding='utf-8') as handle:
            handle.write('%s %s %s\n' % (sha, found['date'], tag))
        os.makedirs(SHARE, exist_ok=True)
        with open(LOG, 'w', encoding='utf-8') as log:
            log.write('%s: installing %s (%s), signed with %s; the archive is the one signed: %d bytes, '
                      'SHA-256 %s\n\n' % (time.ctime(), tag, sha, signed['key'], size, signed['sha256'][:16]))
            log.flush()
            env = dict(os.environ, PATH=PATH)
            rc = subprocess.run(['/bin/sh', INSTALLER], cwd=tree, stdout=log,
                                stderr=subprocess.STDOUT, env=env, check=False).returncode
        if rc != 0:
            return say({'status': 'failed', 'tag': tag,
                        'message': '%s: %s failed with status %d; see %s' % (tag, INSTALLER, rc, LOG)})
        return say({'status': 'updated', 'tag': tag, 'sha': sha, 'signed_with': signed['key'],
                    'message': 'updated to %s (%s), signed with %s' % (tag, sha[:12], signed['key'])})
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv):
    # One name for every plugin's self-update, and this plugin's in the message: syslog-ng files a
    # line by matching its program name as an unanchored pattern, so "os-frontpanel-update" went
    # to the ntpd log - it contains "ntp". "self-update" matches none of OPNsense's filters.
    syslog.openlog('self-update', 0, syslog.LOG_DAEMON)
    dry = len(argv) > 1 and argv[1] == 'check'
    os.makedirs(SHARE, exist_ok=True)
    with open(LOCK, 'w', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({'status': 'busy', 'message': 'another update is running'}))
            return 0
        try:
            found, signed = plan()
        except Unsigned as reason:
            return say({'status': 'failed', 'message': str(reason)})
        except urllib.error.HTTPError as error:
            return say({'status': 'failed',
                        'message': "GitHub refused the latest release's manifest (%s)" % error.code})
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as error:
            return say({'status': 'failed', 'message': 'could not reach GitHub: %s' % error})
        if dry:
            print(json.dumps(found))
            return 0
        if not found['take']:
            return say({'status': 'current', 'tag': found['tag'],
                        'message': '%s is not ahead of what is installed (%s); nothing to do'
                                   % (found['tag'], found['compare'])})
        try:
            return update(found, signed)
        except (urllib.error.URLError, OSError, tarfile.TarError, http.client.HTTPException) as error:
            return say({'status': 'failed', 'tag': found['tag'],
                        'message': '%s could not be installed: %s' % (found['tag'], error)})


if __name__ == '__main__':
    sys.exit(main(sys.argv))
