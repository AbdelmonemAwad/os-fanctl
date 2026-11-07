#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""self-update.py against releases made here and signed with throwaway keys: what a check says of a
release signed with a key the firewall holds, of one signed with another key, of one with no
signature, and of a signed manifest it must not take; which release is taken against what is
installed; and what an update installs - the archive whose SHA-256 and size were signed, its tag
confirmed - and that nothing is installed when the archive is not that one or the tag has moved.
Nothing here asks GitHub: the releases, the list of references and the API are files in a temporary
directory, and OpenSSL makes and checks the signatures as it does on the firewall.

    python3 tools/test-self-update.py"""
import contextlib
import fcntl
import glob
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# The plugin's one self-update.py, wherever its repository keeps it - under src/, or at the top - and
# not this test's own copies of anything.
FOUND = [path for path in glob.glob(os.path.join(ROOT, '**', 'self-update.py'), recursive=True)
         if os.path.relpath(path, ROOT).split(os.sep)[0] not in ('tools', 'docs')]
OPENSSL = shutil.which('openssl')
failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += 0 if ok else 1
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ' - got %r, want %r' % (got, want)))


if len(FOUND) != 1 or not OPENSSL:
    print('FAIL this needs the one self-update.py of the plugin (found %d) and openssl (%s)'
          % (len(FOUND), OPENSSL or 'none'))
    sys.exit(1)

spec = importlib.util.spec_from_file_location('self_update', FOUND[0])
su = importlib.util.module_from_spec(spec)
spec.loader.exec_module(su)


class Quiet:
    """syslog, for a run that is not on a firewall."""
    LOG_NOTICE = LOG_DAEMON = 0

    @staticmethod
    def syslog(*args):
        pass

    @staticmethod
    def openlog(*args):
        pass


TOP = tempfile.mkdtemp(prefix='test-self-update.')
SHARE, KEYS, RELEASES, WORK = (os.path.join(TOP, d) for d in ('share', 'keys', 'releases', 'work'))
for d in (SHARE, KEYS, RELEASES, WORK):
    os.makedirs(d)
su.syslog = Quiet
su.SHARE, su.KEYS, su.WORK, su.OPENSSL = SHARE, KEYS, WORK, OPENSSL
su.INSTALLED, su.RESULT, su.LOG, su.LOCK, su.RELEASES_FROM = (
    os.path.join(SHARE, f) for f in ('installed.json', 'update.json', 'update.log', 'update.lock', 'releases-from'))
su.RELEASES = 'file://' + RELEASES
su.REFS = 'file://' + os.path.join(TOP, 'refs')
su.API = 'file://' + os.path.join(TOP, 'api')
MARK = os.environ['TEST_MARK'] = os.path.join(TOP, 'mark')

COMMIT = {'v1.0.8': '8' * 40, 'v1.0.9': '9' * 40}
DATE = {'v1.0.8': '2026-11-01T10:00:00+00:00', 'v1.0.9': '2026-11-07T10:00:00+00:00'}
# What the installer a release carries does here: it says the stamp the update left in the tree.
INSTALL = '#!/bin/sh\ncat "${PWD}/.source-commit" > "${TEST_MARK}"\n'


def openssl(*args):
    subprocess.run([OPENSSL] + list(args), check=True, capture_output=True)


def make_key(name):
    pem = os.path.join(TOP, name + '.pem')
    openssl('genpkey', '-algorithm', 'ed25519', '-out', pem)
    openssl('pkey', '-in', pem, '-pubout', '-out', os.path.join(TOP, name + '.pub'))
    return pem


def sign(pem, data):
    path = os.path.join(TOP, 'to-sign')
    with open(path, 'wb') as f:
        f.write(data)
    openssl('pkeyutl', '-sign', '-inkey', pem, '-rawin', '-in', path, '-out', path + '.sig')
    with open(path + '.sig', 'rb') as f:
        return f.read()


def trust(*names):
    """The keys the firewall holds: these, and no other."""
    shutil.rmtree(KEYS)
    os.makedirs(KEYS)
    for name in names:
        shutil.copy(os.path.join(TOP, name + '.pub'), os.path.join(KEYS, 'xgs-release-key-%s.pub' % name))


def archive_of(tag, files=None):
    """A release's archive as git archive makes it: every file under one top directory."""
    files = files if files is not None else {su.INSTALLER: INSTALL, 'src/a.txt': 'a\n'}
    buf = io.BytesIO()
    top = '%s-%s' % (su.NAME, tag)
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        entry = tarfile.TarInfo(top)
        entry.type, entry.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(entry)
        for name, text in files.items():
            body = text.encode()
            entry = tarfile.TarInfo(name if name.startswith('../') else top + '/' + name)
            entry.size, entry.mode = len(body), 0o755
            tar.addfile(entry, io.BytesIO(body))
    return buf.getvalue()


def publish(tag, pem, latest=True, fields=None, archive=None, serve=None, signature=True, sig=None,
            where=RELEASES):
    """A release on the place where: its archive, its manifest - fields put in or taken out (None) -
    and the manifest's signature with pem; serve, an archive served in place of the one signed."""
    archive = archive if archive is not None else archive_of(tag)
    manifest = {'format': 1, 'repository': su.REPO, 'tag': tag, 'commit': COMMIT[tag], 'date': DATE[tag],
                'archive': '%s-%s.tar.gz' % (su.NAME, tag), 'sha256': hashlib.sha256(archive).hexdigest(),
                'size': len(archive)}
    for name, value in (fields or {}).items():
        if value is None:
            manifest.pop(name, None)
        else:
            manifest[name] = value
    body = (json.dumps(manifest, sort_keys=True, indent=1) + '\n').encode()
    files = {'%s-%s.tar.gz' % (su.NAME, tag): serve if serve is not None else archive, 'release.json': body}
    if signature:
        files['release.json.sig'] = sig if sig is not None else sign(pem, body)
    places = [os.path.join(where, 'download', tag)] + ([os.path.join(where, 'latest', 'download')] if latest else [])
    for place in places:
        shutil.rmtree(place, ignore_errors=True)
        os.makedirs(place)
        for name, data in files.items():
            with open(os.path.join(place, name), 'wb') as f:
                f.write(data)


def refs(tags, lightweight=False):
    """GitHub's list of references, as its git endpoint gives it: an annotated tag's own line, then
    the commit it points to; a lightweight tag's line alone."""
    def pkt(text):
        return ('%04x' % (len(text) + 4)).encode() + text.encode()
    out = pkt('# service=git-upload-pack\n') + b'0000'
    out += pkt('%s refs/heads/main\0multi_ack thin-pack side-band\n' % ('1' * 40))
    for tag, sha in tags.items():
        if lightweight:
            out += pkt('%s refs/tags/%s\n' % (sha, tag))
        else:
            out += pkt('%s refs/tags/%s\n' % ('c' * 40, tag))
            out += pkt('%s refs/tags/%s^{}\n' % (sha, tag))
    with open(os.path.join(TOP, 'refs'), 'wb') as f:
        f.write(out + b'0000')


def api(tag, sha):
    os.makedirs(os.path.join(TOP, 'api', 'commits'), exist_ok=True)
    with open(os.path.join(TOP, 'api', 'commits', tag), 'w', encoding='utf-8') as f:
        json.dump({'sha': sha}, f)


def installed(commit, date):
    for path in (su.INSTALLED, su.RESULT, MARK):
        if os.path.exists(path):
            os.unlink(path)
    if commit is not None:
        with open(su.INSTALLED, 'w', encoding='utf-8') as f:
            json.dump({'commit': commit, 'date': date, 'source': 'stamp', 'installed_at': 0}, f)


def run(*argv):
    """main() with argv: its exit status, and the last line it printed, read as JSON."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = su.main(['self-update.py'] + list(argv))
    lines = out.getvalue().strip().splitlines()
    return status, json.loads(lines[-1]) if lines else {}


def marked():
    try:
        with open(MARK, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return None


def left_in_work():
    return sorted(os.listdir(WORK))


owner, stranger = make_key('owner'), make_key('stranger')
trust('owner')
refs(COMMIT)

# -- what a check says
publish('v1.0.9', owner)
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
status, said = run('check')
check('a release signed with a key held, after what is installed: taken',
      (status, said.get('tag'), said.get('sha'), said.get('compare'), said.get('take'), said.get('signed_with')),
      (0, 'v1.0.9', COMMIT['v1.0.9'], 'ahead', True, 'xgs-release-key-owner.pub'))
check('a check installs nothing and leaves no update record', (marked(), os.path.exists(su.RESULT)), (None, False))

installed(COMMIT['v1.0.9'], DATE['v1.0.9'])
check('the release installed: not taken', [run('check')[1].get(k) for k in ('compare', 'take')], ['identical', False])

installed('d' * 40, '2026-11-08T09:00:00+00:00')
check('something newer installed - main, a branch: not taken back',
      [run('check')[1].get(k) for k in ('compare', 'take')], ['behind', False])
status, said = run()
check('...and an update says so and installs nothing',
      (status, said.get('status'), 'not ahead of what is installed (behind)' in said.get('message', ''), marked()),
      (0, 'current', True, None))

installed('d' * 40, '2026-11-02T09:00:00+00:00')
check('another commit dated before the release (a branch it does not hold): taken',
      [run('check')[1].get(k) for k in ('compare', 'take')], ['ahead', True])

installed('d' * 40, '')
check('another commit whose date is unknown: taken', [run('check')[1].get(k) for k in ('compare', 'take')],
      ['ahead', True])

installed(None, None)
check('nothing recorded as installed: taken once', [run('check')[1].get(k) for k in ('compare', 'take')],
      ['unknown', True])

installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
publish('v1.0.9', stranger)
status, said = run('check')
check('signed with a key the firewall does not hold: not taken, and said',
      (status, said.get('status'), said.get('message')),
      (1, 'failed', 'the signature of the latest release does not verify with any release key of this firewall'))

publish('v1.0.9', owner)
trust()
check('no key on the firewall at all: not taken, and said', run('check')[1].get('message'),
      "the signature of the latest release does not verify with this firewall's release keys - it has none")
trust('stranger', 'owner')
check('one key of several held is enough, and named', run('check')[1].get('signed_with'), 'xgs-release-key-owner.pub')
trust('owner')

publish('v1.0.9', owner, signature=False)
check('a release with no signature: not signed yet', run('check')[1].get('message'),
      'the latest release is not signed yet: it has no release.json.sig')
shutil.rmtree(os.path.join(RELEASES, 'latest'))
check('a release with no manifest - every release before 2026-11-07', run('check')[1].get('message'),
      'the latest release is not signed yet: it has no release.json')

for name, fields, said in (
        ('another repository', {'repository': 'AbdelmonemAwad/os-other'}, 'has no repository'),
        ('a format it does not read', {'format': 2}, 'is of a format this self-update.py does not read'),
        ('no commit', {'commit': None}, 'has no commit'),
        ('a commit not of 40 hex digits', {'commit': 'abc'}, 'has no commit'),
        ('an archive name with a path in it', {'archive': '../x.tar.gz'}, 'has no archive'),
        ('no date', {'date': None}, 'has no date'),
        ('a date that is no date', {'date': 'yesterday'}, 'has no date'),
        ('a size of true', {'size': True}, 'has no size'),
        ('a size of nothing', {'size': 0}, 'has no size'),
        ('a size larger than an archive can be', {'size': (256 << 20) + 1}, 'has no size'),
        ('a size as text', {'size': '1000'}, 'has no size')):
    publish('v1.0.9', owner, fields=fields)
    status, answer = run('check')
    check('a signed manifest with %s: not taken' % name, (status, said in answer.get('message', '')), (1, True))

publish('v1.0.9', owner, sig=b'x' * 65)
check('a signature longer than one can be: not read further', run('check')[1].get('message'),
      'release.json.sig of the latest release is larger than a release file of its kind can be')
publish('v1.0.9', owner, fields={'notes': 'x' * (64 << 10)})
check('a manifest larger than one can be: not read further', run('check')[1].get('message'),
      'release.json of the latest release is larger than a release file of its kind can be')

# A test firewall's own place for a release's files: what it serves must still be signed.
ELSEWHERE = os.path.join(TOP, 'elsewhere')
publish('v1.0.8', owner, where=ELSEWHERE)
publish('v1.0.9', owner)
with open(su.RELEASES_FROM, 'w', encoding='utf-8') as f:
    f.write('file://%s/\n' % ELSEWHERE)
installed(None, None)
check('releases-from names the place a release is read from', run('check')[1].get('tag'), 'v1.0.8')
publish('v1.0.8', stranger, where=ELSEWHERE)
check('...and what it serves is taken only as signed', run('check')[1].get('status'), 'failed')
os.unlink(su.RELEASES_FROM)

# -- what an update installs
publish('v1.0.9', owner)
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
status, said = run()
with open(su.LOG, encoding='utf-8') as f:
    first = f.readline()
check('an update installs the signed archive, and the installer finds the commit, date and tag',
      (status, said.get('status'), said.get('tag'), said.get('sha'), marked()),
      (0, 'updated', 'v1.0.9', COMMIT['v1.0.9'], '%s %s v1.0.9' % (COMMIT['v1.0.9'], DATE['v1.0.9'])))
check('...says which key signed it, in its result and its log',
      (said.get('signed_with'), said.get('message'), 'signed with xgs-release-key-owner.pub; the archive is the one '
       'signed' in first), ('xgs-release-key-owner.pub', 'updated to v1.0.9 (%s), signed with '
                            'xgs-release-key-owner.pub' % COMMIT['v1.0.9'][:12], True))
check('...and leaves nothing in its work directory', left_in_work(), [])

signed = archive_of('v1.0.9')
for name, served, said in (
        ('another of the same size', signed[:-1] + bytes([signed[-1] ^ 1]), 'bytes, SHA-256'),
        ('a longer one', signed + b'more', 'more than %d bytes' % len(signed)),
        ('a shorter one', signed[:-10], '%d bytes, SHA-256' % (len(signed) - 10))):
    publish('v1.0.9', owner, archive=signed, serve=served)
    installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
    status, answer = run()
    message = answer.get('message', '')
    check('an archive not the one signed (%s): nothing installed' % name,
          (status, answer.get('status'), 'is not the one signed' in message, said in message, marked(), left_in_work()),
          (1, 'failed', True, True, None, []))

publish('v1.0.9', owner)
refs({'v1.0.9': 'e' * 40})
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
status, answer = run()
check('a tag moved since it was signed: nothing downloaded, nothing installed',
      (status, answer.get('message'), marked(), left_in_work()),
      (1, 'GitHub says v1.0.9 is commit eeeeeeeeeeee, not the 999999999999 it was signed as; nothing was installed',
       None, []))

refs(COMMIT, lightweight=True)
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
check("a lightweight tag's own line names its commit", run()[1].get('status'), 'updated')

os.unlink(os.path.join(TOP, 'refs'))
api('v1.0.9', COMMIT['v1.0.9'])
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
check('no list of references: the API names the commit', run()[1].get('status'), 'updated')
os.unlink(os.path.join(TOP, 'api', 'commits', 'v1.0.9'))
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
status, answer = run()
check('neither can be read: nothing installed, and said',
      (status, answer.get('message', '').startswith('v1.0.9 could not be confirmed with GitHub'), marked()),
      (1, True, None))
refs(COMMIT)

publish('v1.0.9', owner, archive=archive_of('v1.0.9', {su.INSTALLER: '#!/bin/sh\nexit 3\n'}))
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
status, answer = run()
check('an installer that fails: said with its status', (status, ('%s failed with status 3' % su.INSTALLER)
                                                       in answer.get('message', '')), (1, True))
publish('v1.0.9', owner, archive=archive_of('v1.0.9', {'src/a.txt': 'a\n'}))
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
check('an archive with no installer: nothing installed', run()[1].get('message'),
      'v1.0.9 has no %s; nothing was installed' % su.INSTALLER)

OUTSIDE = os.path.join(WORK, 'outside.txt')
publish('v1.0.9', owner, archive=archive_of('v1.0.9', {su.INSTALLER: INSTALL, '../outside.txt': 'x\n',
                                                         'src/../../outside.txt': 'x\n'}))
installed(COMMIT['v1.0.8'], DATE['v1.0.8'])
check('nothing of an archive lands outside its tree', (run()[1].get('status'), os.path.exists(OUTSIDE)),
      ('updated', False))

with open(su.LOCK, 'w', encoding='utf-8') as held:
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    check('one update at a time', run()[1], {'status': 'busy', 'message': 'another update is running'})

shutil.rmtree(TOP, ignore_errors=True)
print('%d failure(s)' % failures)
sys.exit(1 if failures else 0)
