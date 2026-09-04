"""Prove the newest smoke checks by reintroducing the bugs they exist to catch.

A check that has never been seen to fail is not evidence. Every one of these was
written after the fact - one because a CSS fix could not reach a phone that had already
cached the broken stylesheet, one because a routine bounded-read fix would have silently
stopped paying sellers past row 100, one because a keystroke handler closed the phone
keyboard mid-word, one because Flask serves the whole static tree and the checks that
guard a paid download live on a different URL, two because a services page told a concert
ticket buyer there was no pickup and an unattended request told nobody at all, and two because a
public grid's cost was set by how well its raffles had sold - so all of
them are exactly the kind of assertion that can be subtly inert and still print `[ok  ]`.

Each control names the smoke script that owns its check, so the first five run
`tools/wiring_smoke.py`, the services pair run `tools/services_smoke.py`, and the public-list pair
run `tools/list_page_smoke.py`.

Each control patches the source *in bytes*, runs that script, and restores the original
bytes from a try/finally. Byte-level rather than line-level so the restore is provably
exact on a CRLF checkout, and try/finally rather than a copy-and-move so there is no
window where an interrupted run leaves the tree patched. The md5 of every touched file is
printed before and after; if a pair does not match, that is the only line in the output
that matters.

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
SERVICES_SMOKE = os.path.join('tools', 'services_smoke.py')
LIST_SMOKE = os.path.join('tools', 'list_page_smoke.py')

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
    # The last two run services_smoke instead, because that is where the checks they
    # aim at live. Both reintroduce a state the platform was actually shipped in.
    {
        'name': 'a ticket told there is no pickup is caught',
        'file': 'models.py',
        'smoke': SERVICES_SMOKE,
        # One line, no newline in the target: main.py is LF but models.py need not be,
        # and a multi-line target silently finds nothing on a CRLF checkout - which is
        # reported as INCONCLUSIVE, not as a pass, but proves nothing either way.
        'old': b"        if not self.has_field('pickup'):",
        'new': b"        if False and not self.has_field('pickup'):",
        'expect': 'says nothing about pickup',
        'why': 'this is the pre-profile state exactly: a concert ticket, a haircut, a '
               'tutoring hour and a rented room each told the client "No pickup '
               'offered, take to the location as directed", which is the complaint '
               'the six profiles were written to answer',
    },
    {
        'name': 'an unattended request that notifies nobody is caught',
        'file': 'main.py',
        'smoke': SERVICES_SMOKE,
        'old': b'        try:\n'
               b'            notify_service_provider(link_request)\n'
               b'        except Exception:\n'
               b"            logger.exception('automatic provider notify failed for %s', request_id)\n",
        'new': b'        pass\n',
        'expect': 'the provider was notified without anyone pressing anything',
        'why': 'without it a request that lands on an empty desk sits there: the MVP '
               'asked for the provider to be messaged "immediately it lands ... and '
               'it does not need clicking any button or monitoring", and the client '
               'still gets the same cheerful welcome either way, so nothing else in '
               'the product would show it had stopped',
    },
    # And two on the public bounded reads, which run list_page_smoke. Both are the
    # shipped state of a page anonymous traffic lands on directly.
    {
        'name': 'a grid that loads every ticket to count buyers is caught',
        'file': 'templates/raffles.html',
        'smoke': LIST_SMOKE,
        'old': b'{% set buyer_count = buyer_counts.get(raffle.id, 0) %}',
        'new': b"{% set buyer_count = raffle.tickets|map(attribute='user_id')"
               b'|unique|list|length %}',
        'expect': 'did not get more expensive as raffles were added',
        'why': 'this is what the page shipped as, and it is the reason the check '
               'measures /raffles twice instead of just counting cards: the lazy load '
               'is one query per card whatever the tickets do, so nothing about the '
               'query count says the queries got bigger. The card count is capped now, '
               'which bounds how many of these loads happen - it does nothing about '
               'the fifty thousand rows inside one of them',
    },
    {
        'name': 'an unbounded row of ticket badges is caught',
        'file': 'main.py',
        'smoke': LIST_SMOKE,
        'old': b'            RaffleTicket.ticket_number.asc()).limit('
               b'RAFFLE_TICKET_DISPLAY_LIMIT).all()',
        'new': b'            RaffleTicket.ticket_number.asc()).all()',
        'expect': 'badges for',
        'why': 'raffle_buy_ticket takes 50 tickets a press and caps no total, so this '
               'is the one list on the page that a single buyer can grow without limit '
               'and without anyone noticing - and the buyer paid for every row of it',
    },
]


def md5(path):
    return hashlib.md5(io.open(path, 'rb').read()).hexdigest()


def run_smoke(script=SMOKE):
    """Run a smoke script and return (exit code, the lines worth reading)."""
    proc = subprocess.run([PYTHON, '-u', script], cwd=REPO,
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
        print('    smoke      : %s' % control.get('smoke', SMOKE))
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
            code, lines, blob = run_smoke(control.get('smoke', SMOKE))
        finally:
            io.open(path, 'wb').write(raw)

        after = md5(path)
        restored = after == before
        # Matched against the lines that are *not* passing checks. Searching the whole
        # blob would count the check's own `[ok  ]` line as evidence that it fired,
        # which is the one mistake this whole script exists to catch.
        named = any(control['expect'].lower() in line.lower()
                    for line in blob.splitlines() if '[ok' not in line.lower())

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
