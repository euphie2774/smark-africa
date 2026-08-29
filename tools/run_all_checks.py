"""Run every check script in the repo and report one line each.

Run with: python tools/run_all_checks.py

There are twenty-four of these now, spread between the repository root and tools/, and
they are the only evidence that any of the scaling work actually holds. Running them
one at a time invites running only the ones you remember, which is how a regression
in the fourteenth survives a green run of the first three.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = [
    # First, because it needs no fixture and a failure here explains failures below:
    # the app boots bare, and every template link still resolves.
    'tools/wiring_smoke.py',
    'test_card_barcode.py',
    'test_delivery_follows_ads.py',
    'test_deploy_migration.py',
    'test_dispatch_and_seller_alerts.py',
    'test_promo_codes.py',
    'test_seller_hub_notifications.py',
    'test_shipping_tracking.py',
    'test_storefront_listing.py',
    'test_trace_map_matching.py',
    'tools/scale_smoke.py',
    'tools/ledger_smoke.py',
    'tools/fanout_smoke.py',
    'tools/page_smoke.py',
    'tools/list_page_smoke.py',
    'tools/bulk_digital_smoke.py',
    'tools/admin_bulk_digital_smoke.py',
    'tools/phone_evidence_smoke.py',
    'tools/private_storage_smoke.py',
    'tools/services_smoke.py',
    'tools/invoice_smoke.py',
    'tools/semantic_search_smoke.py',
    'tools/bounded_read_smoke.py',
    'tools/market_facts_smoke.py',
]


def report_failure(output, returncode, context=30, tail=20):
    """Print enough of a failing script's output to diagnose it here.

    Filtering to lines containing 'FAIL' threw away the one thing worth keeping.
    The comparative checks in list_page_smoke.py print *why* a query count grew
    immediately after the failing line - which statement, and how many times - and
    none of those lines say 'FAIL', so a run of the suite reported two numbers and
    discarded the explanation. An intermittent failure that only reproduces inside
    the suite is then undiagnosable from the only place it appears.

    So: from the first failing line onward, bounded. And when nothing matched at
    all, the tail instead - a script killed by the OS (Windows exit 3221225477 is
    an access violation) writes no failure line of its own, and the last thing it
    printed is the only clue to where it died.
    """
    lines = output.splitlines()
    marks = [i for i, line in enumerate(lines)
             if 'FAIL' in line or 'Error' in line or 'error' in line]
    if marks:
        start = marks[0]
        window = lines[start:start + context]
        truncated = len(lines) - (start + len(window))
    else:
        window = lines[-tail:]
        truncated = 0
        print(f'         (exit {returncode}, no failure line printed; last '
              f'{len(window)} line(s) of output)')
    for line in window:
        text = line.rstrip()
        if text:
            print(f'         {text[:200]}')
    if truncated > 0:
        print(f'         ... {truncated} further line(s) not shown')


def main():
    env = dict(os.environ, DISABLE_BACKGROUND_JOBS='1', PYTHONWARNINGS='ignore')
    failed = []
    for script in SCRIPTS:
        path = os.path.join(ROOT, script.replace('/', os.sep))
        if not os.path.exists(path):
            print(f'  [skip] {script} - not found')
            continue
        result = subprocess.run([sys.executable, path], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=900)
        output = (result.stdout or '') + (result.stderr or '')
        # The count of passing checks, when the script reports one, is worth more in
        # a summary than the exit code alone: a script that silently stopped running
        # half its checks still exits zero.
        tally = [line for line in output.splitlines()
                 if 'checks passed' in line or 'check(s) failed' in line]
        note = tally[-1] if tally else f'exit {result.returncode}'
        ok = result.returncode == 0
        if not ok:
            failed.append(script)
            report_failure(output, result.returncode)
        print(f'  [{"ok  " if ok else "FAIL"}] {script} - {note}')

    print()
    if failed:
        print(f'{len(failed)} script(s) failed: {", ".join(failed)}')
        return 1
    print(f'all {len(SCRIPTS)} check scripts passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
