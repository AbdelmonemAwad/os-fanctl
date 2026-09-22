#!/usr/bin/env python3
"""Refuse to publish anything that carries the author's own network or a secret.

These plugins are written on a live firewall, and that is their strength: everything in
them was measured somewhere real. It is also how a home LAN address ends up in a form
hint, and from there - because a hint goes through gettext like any other string - in the
translation catalogue, where nobody thinks to look for it.

This runs in CI on every push and every pull request, so the answer is not "we remembered
to check" but "it cannot land without being checked".

It is written so that IT NAMES NOTHING. A check that listed the addresses to look for
would put them in the repository it is protecting. So the rule is the other way round:
every address literal must be one of the ranges that exist for documentation, and anything
else in private space fails. The author's real addresses are caught without ever being
written down, and so is the next contributor's.

    tools/check-private-data.py [paths...]

Exit status is 0 when clean, 1 when something must be replaced.
"""
import pathlib
import re
import sys

# RFC 5737 (documentation), RFC 3849's v6 equivalent is not matched here, plus the ranges
# every example uses. 192.168.1.x is the one a reader recognises as "some home LAN".
ALLOWED_PREFIXES = (
    '0.', '127.', '255.',
    '192.0.2.', '198.51.100.', '203.0.113.',   # RFC 5737, made for documentation
    '192.168.1.',                              # the conventional example LAN
    '10.0.0.',                                 # the conventional example flat network
    '172.16.0.',
    '224.0.0.', '239.',                        # multicast
    '169.254.',                                # link-local
)

# Space that belongs to somebody's actual network if it is not one of the above.
PRIVATE_PATTERNS = (
    re.compile(r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
    re.compile(r'\b192\.168\.\d{1,3}\.\d{1,3}\b'),
    re.compile(r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b'),
)

SECRETS = (
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), 'a private key'),
    (re.compile(r'\bssh-(?:rsa|ed25519|dss) AAAA'), 'an SSH public key'),
    (re.compile(r'\b[A-Za-z0-9_-]*(?:api[_-]?key|secret|passwd|password)\s*[:=]\s*'
                r'["\']?[A-Za-z0-9/+_-]{12,}'), 'something that looks like a credential'),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'), 'a GitHub token'),
)

# A MAC that is not one of the documentation prefixes is somebody's actual hardware.
MAC = re.compile(r'\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b')
ALLOWED_MACS = ('00:00:00:', 'ff:ff:ff:', '01:00:5e:', '33:33:', 'de:ad:be:', '02:00:00:')

SKIP_DIRS = {'.git', '__pycache__', 'node_modules'}
BINARY = {'.png', '.jpg', '.jpeg', '.gif', '.mo', '.ico', '.pdf', '.zip', '.gz'}

# Not every address in a repository is the author's. A translation catalogue carries the
# upstream project's own example addresses inside its msgids, and rewriting them would
# make the translation wrong - the string would no longer match what the program asks for.
# So a repository may declare exceptions, one glob per line with the reason beside it, and
# the exception is then visible to anybody reading the repository rather than hidden in
# the checker. A pattern that matches nothing is itself an error: a stale exception is how
# a rule quietly stops applying.
IGNORE_FILE = '.private-data-ignore'


def exceptions(root):
    path = pathlib.Path(root) / IGNORE_FILE
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            out.append(line)
    return out


def text_files(roots):
    for root in roots:
        root = pathlib.Path(root)
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in BINARY:
                continue
            yield path


def complaints(path, text):
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in PRIVATE_PATTERNS:
            for found in pattern.findall(line):
                if not found.startswith(ALLOWED_PREFIXES):
                    yield (number, 'an address in private space that is not one of the '
                                   'documentation ranges')
        for found in MAC.findall(line):
            if not found.lower().startswith(ALLOWED_MACS):
                yield (number, 'a MAC address belonging to real hardware')
        for pattern, what in SECRETS:
            if pattern.search(line):
                yield (number, what)


def main(argv):
    roots = argv[1:] or ['.']
    failures = 0
    checked = 0
    skipped = 0
    allowed = {root: exceptions(root) for root in roots}
    used = set()

    for path in text_files(roots):
        excused = None
        for root, patterns in allowed.items():
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            for pattern in patterns:
                if relative.match(pattern) or relative.as_posix() == pattern:
                    excused = (root, pattern)
        if excused:
            used.add(excused)
            skipped += 1
            continue

        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        checked += 1
        # The checker names patterns, never values, so it can describe itself safely.
        if path.name == pathlib.Path(__file__).name:
            continue
        for number, what in complaints(path, text):
            print('%s:%d: %s' % (path, number, what))
            failures += 1

    for root, patterns in allowed.items():
        for pattern in patterns:
            if (root, pattern) not in used:
                print('%s/%s: "%s" matches nothing - remove it rather than leave a rule '
                      'that has quietly stopped applying' % (root, IGNORE_FILE, pattern))
                failures += 1

    if failures:
        print()
        print('%d line(s) must be replaced before this can be published.' % failures)
        print('Use 192.168.1.x, 10.0.0.x or the RFC 5737 ranges for examples, and keep')
        print('real addresses, hardware identifiers and credentials out of the tree.')
        print('Text that belongs to an upstream project and must stay verbatim goes in')
        print('%s, with the reason on the same line.' % IGNORE_FILE)
        return 1
    print('%d file(s) checked, %d excused by %s, nothing private found.'
          % (checked, skipped, IGNORE_FILE))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
