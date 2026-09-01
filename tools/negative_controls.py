"""Prove the newest wiring_smoke checks by reintroducing the bugs they exist to catch.

A check that has never been seen to fail is not evidence. Every one of these was
written after the fact - one because a CSS fix could not reach a phone that had already
cached the broken stylesheet, one because a routine bounded-read fix would have silently
stopped paying sellers past row 100, one because a keystroke handler closed the phone
keyboard mid-word, one because Flask serves the whole static tree and the checks that
guard a paid download live on a different URL - so all of them are exactly the kind of
assertion that can be subtly inert and still print `[ok  ]`.

Each control patches the source *in bytes*, runs `tools/wiring_smoke.py`, and restores
the original bytes from a try/finally. Byte-level rather than line-level so the restore
is provably exact on a CRLF checkout, and try/finally rather than a copy-and-move so
there is no window where an interrupted run leaves the tree patched. The md5 of every
touched file is printed before and after; if a pair does not match, that is the only
line in the output that matters.

Two controls point at the same check on purpose. That check asserts both that the
private upload folders are refused and that the public ones are not, and a fix for
either half breaks the other, so a single control would leave half of it unproven.

Run it by hand, not from run_all_checks.py: it deliberately breaks the working tree for
a few seconds at a time, which is not something a routine suite should ever do.

    .\\venv\\Scripts\\python.exe -u tools\\negative_controls.py
"""

import hashlib
import io
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, 'venv', 'Scripts', 'python.exe')
SMOKE = os.path.join('tools', 'wiring_smoke.py')

# The check each control is aimed at. Matched against the failing output so a control
# that trips some *other* check - a real regression, or a patch that broke the parse -
# is reported as inconclusive instead of counting as a pass.
CONTROLS = [
    {
        'name': 'an unversioned stylesheet is caught',
        'file': 'templates/base.html',
        'old': b"href=\"{{ url_for('static', filename='style.css', "
               b"v=asset_version('style.css')) }}\"",
        'new': b"href=\"{{ url_for('static', filename='style.css') }}\"",
        'expect': 'first-party assets are cache-busted',
        'why': 'the phone CSS fix could not reach an already-installed app without it',
    },
    {
        'name': 'a payment run over a capped list is caught',
        'file': 'main.py',
        'old': b'for withdrawal in withdrawal_batch:',
        'new': b"for withdrawal in snapshot['pending_withdrawals']:",
        'expect': 'the payment run does not iterate a capped list',
        'why': 'paying only the first 100 sellers while flashing success is the worst '
               'failure this codebase can have',
    },
    {
        'name': 'a keystroke that submits a form is caught',
        'file': 'static/main.js',
        'old': b'    // No auto-submit on the search box, deliberately.',
        'new': b"    document.querySelector('[name=\"search\"]')"
               b".addEventListener('keyup', function () { this.form.submit(); });",
        'expect': 'typing in a search box does not navigate',
        'why': 'a navigation per keystroke pause closes the phone keyboard mid-word '
               'and runs the product search for a half-typed term',
    },
    # Two controls for one check, because that check has two halves and each half
    # guards against the other's fix. A guard that is switched off publishes paid
    # files and ID documents; a guard that is too wide 404s every product photo on
    # the site. Both are green everywhere else in the suite.
    {
        'name': 'a static route that serves private uploads is caught',
        'file': 'main.py',
        'old': b"    if request.endpoint == 'static':",
        'new': b"    if request.endpoint == 'static' and False:",
        'expect': 'the static route does not hand out private uploads',
        'why': 'this is the pre-fix state exactly: /static/uploads/digital/<name> '
               'hands over a paid file, and seller_docs hands over an ID photograph, '
               'to anyone holding the URL',
    },
    {
        'name': 'a guard wide enough to break every product photo is caught',
        'file': 'main.py',
        'old': b'    rule = GUARDED_UPLOAD_FOLDERS.get(folder)',
        'new': b"    rule = GUARDED_UPLOAD_FOLDERS.get(folder, 'nobody')",
        'expect': 'the static route does not hand out private uploads',
        'why': 'a one-word default turns the guard on for products, banners, services '
               'and inspo too, which passes any test that only asks whether private '
               'things are blocked while blanking every image on the shop',
    },
]


def md5(path):
    return hashlib.md5(io.open(path, 'rb').read()).hexdigest()


def run_smoke():
    """Run the wiring smoke and return (exit code, the lines worth reading)."""
    proc = subprocess.run([PYTHON, '-u', SMOKE], cwd=REPO,
                          capture_output=True, text=True)
    blob = (proc.stdout or '') + (proc.stderr or '')
    keep = []
    for line in blob.splitlines():
        low = line.lower()
        if ('[fail' in low or 'checks passed' in low or 'assertionerror' in low
                or 'traceback' in low):
            keep.append(line.rstrip())
    return proc.returncode, keep, blob


def main():
    failures = []
    print('=' * 72)
    print('negative controls - each bug is reintroduced, then restored')
    print('=' * 72)

    for control in CONTROLS:
        path = os.path.join(REPO, control['file'])
        raw = io.open(path, 'rb').read()
        before = hashlib.md5(raw).hexdigest()
        count = raw.count(control['old'])

        print('')
        print('--- %s' % control['name'])
        print('    file       : %s  md5 %s' % (control['file'], before))
        print('    why it     : %s' % control['why'])

        if count != 1:
            # Not a failed control - a control that cannot be set up. Say which,
            # because "expected 1 match, found 0" usually means the line was edited
            # since and this file needs updating, not that the app is broken.
            print('    RESULT     : INCONCLUSIVE - expected exactly 1 occurrence of the '
                  'patch target, found %d' % count)
            failures.append('%s (patch target not found)' % control['name'])
            continue

        try:
            io.open(path, 'wb').write(raw.replace(control['old'], control['new']))
            code, lines, blob = run_smoke()
        finally:
            io.open(path, 'wb').write(raw)

        after = md5(path)
        restored = after == before
        named = control['expect'].lower() in blob.lower()

        print('    smoke exit : %d' % code)
        for line in lines[:12]:
            print('    | %s' % line)
        print('    restored   : %s  md5 %s' % ('yes' if restored else 'NO', after))

        if code == 0:
            print('    RESULT     : FAILED - the smoke stayed green with the bug present')
            failures.append('%s (check did not fire)' % control['name'])
        elif not named:
            print('    RESULT     : INCONCLUSIVE - the smoke went red but never named '
                  '"%s"' % control['expect'])
            failures.append('%s (a different check fired)' % control['name'])
        else:
            print('    RESULT     : ok - the smoke went red and named the culprit')

        if not restored:
            print('    RESULT     : ALSO FAILED - %s was not restored byte-for-byte'
                  % control['file'])
            failures.append('%s (restore mismatch)' % control['file'])

    print('')
    print('=' * 72)
    if failures:
        print('%d control(s) did not prove anything:' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('all %d negative controls proved their check' % len(CONTROLS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
