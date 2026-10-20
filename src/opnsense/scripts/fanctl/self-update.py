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

ONLY FORWARD. What is installed is read from /usr/local/share/os-fanctl/installed.json, which
install/install.sh writes from a git checkout, or from the .source-commit stamp that an
xgs-image-builder image or this script leaves in the tree. The release is taken only when GitHub
says its commit is ahead of that one; a firewall running something newer, from main or from a
branch, is not taken back. An install from before the stamp existed takes the latest release once.

WHAT AN UPDATE IS. The release's source, unpacked and installed by the install/install.sh it carries -
the same script a hand install runs, so an update is never a different procedure from an install.
The output of the last one is kept in /usr/local/share/os-fanctl/update.log, and what happened in
/usr/local/share/os-fanctl/update.json and in the system log, as self-update.
"""
import fcntl
import json
import os
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
# GitHub, unless a test points these somewhere else.
API = os.environ.get('SELF_UPDATE_API', 'https://api.github.com') + '/repos/' + REPO
TARBALL = os.environ.get('SELF_UPDATE_TARBALL', 'https://codeload.github.com') + '/' + REPO + \
    '/tar.gz/'
INSTALLED = os.path.join(SHARE, 'installed.json')
RESULT = os.path.join(SHARE, 'update.json')
LOG = os.path.join(SHARE, 'update.log')
LOCK = os.path.join(SHARE, 'update.lock')
# cron starts this with /usr/bin:/bin, and the installers call configctl, php and python3.
PATH = '/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin'


class NoRelease(Exception):
    pass


def get(url):
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': NAME + '-self-update',
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def read_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle) or {}
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


def plan():
    """The latest release, its commit, and whether it is ahead of what is installed."""
    have = read_json(INSTALLED).get('commit', '')
    try:
        release = get(API + '/releases/latest')
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NoRelease() from error
        raise
    tag = release['tag_name']
    commit = get(API + '/commits/' + urllib.request.quote(tag, safe=''))
    found = {'tag': tag, 'sha': commit['sha'], 'date': commit['commit']['committer']['date'],
             'installed': have}
    if have == found['sha']:
        found['compare'] = 'identical'
    elif have:
        found['compare'] = get('%s/compare/%s...%s' % (API, have, found['sha'])).get('status')
    else:
        found['compare'] = 'unknown'
    # ahead: the release is newer. behind, identical, diverged: it is not, or not plainly.
    found['take'] = found['compare'] in ('ahead', 'unknown')
    return found


def unpack(archive, tree):
    """Everything under the one top directory GitHub puts in a tarball, nothing outside it."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        top = members[0].name.split('/')[0] if members else ''
        for member in members:
            if not member.name.startswith(top + '/') or '..' in member.name.split('/'):
                continue
            member.name = member.name[len(top) + 1:]
            if member.name:
                tar.extract(member, tree, filter='tar')


def update(found):
    work = tempfile.mkdtemp(prefix=NAME + '-update.', dir='/var/tmp')
    try:
        archive = os.path.join(work, 'release.tar.gz')
        tree = os.path.join(work, 'tree')
        request = urllib.request.Request(TARBALL + found['sha'],
                                         headers={'User-Agent': NAME + '-self-update'})
        with urllib.request.urlopen(request, timeout=120) as response, \
                open(archive, 'wb') as handle:
            shutil.copyfileobj(response, handle)
        os.makedirs(tree)
        unpack(archive, tree)
        if not os.path.isfile(os.path.join(tree, INSTALLER)):
            return say({'status': 'failed', 'tag': found['tag'],
                        'message': '%s has no %s; nothing was installed' % (found['tag'],
                                                                           INSTALLER)})
        # The installer records what it installed from this, as it does from a git checkout.
        with open(os.path.join(tree, '.source-commit'), 'w', encoding='utf-8') as handle:
            handle.write('%s %s %s\n' % (found['sha'], found['date'], found['tag']))
        os.makedirs(SHARE, exist_ok=True)
        with open(LOG, 'w', encoding='utf-8') as log:
            log.write('%s: installing %s (%s)\n\n' % (time.ctime(), found['tag'], found['sha']))
            log.flush()
            env = dict(os.environ, PATH=PATH)
            rc = subprocess.run(['/bin/sh', INSTALLER], cwd=tree, stdout=log,
                                stderr=subprocess.STDOUT, env=env, check=False).returncode
        if rc != 0:
            return say({'status': 'failed', 'tag': found['tag'],
                        'message': '%s: %s failed with status %d; see %s' % (
                            found['tag'], INSTALLER, rc, LOG)})
        return say({'status': 'updated', 'tag': found['tag'], 'sha': found['sha'],
                    'message': 'updated to %s (%s)' % (found['tag'], found['sha'][:12])})
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
            found = plan()
        except NoRelease:
            return say({'status': 'none', 'message': 'no release is published yet'})
        except urllib.error.HTTPError as error:
            return say({'status': 'failed',
                        'message': 'GitHub refused the request (%s)' % error.code})
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            return say({'status': 'failed', 'message': 'could not reach GitHub: %s' % error})
        if dry:
            print(json.dumps(found))
            return 0
        if not found['take']:
            return say({'status': 'current', 'tag': found['tag'],
                        'message': '%s is not ahead of what is installed (%s); nothing to do'
                                   % (found['tag'], found['compare'])})
        try:
            return update(found)
        except (urllib.error.URLError, OSError, tarfile.TarError) as error:
            return say({'status': 'failed', 'tag': found['tag'],
                        'message': '%s could not be installed: %s' % (found['tag'], error)})


if __name__ == '__main__':
    sys.exit(main(sys.argv))
