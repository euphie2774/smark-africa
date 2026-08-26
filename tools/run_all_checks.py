"""Run every check script in the repo and report one line each.

Run with: python tools/run_all_checks.py

There are twenty-two of these now, spread between the repository root and tools/, and
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
]


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
            for line in output.splitlines():
                if 'FAIL' in line or 'Error' in line or 'error' in line:
                    print(f'         {line.strip()[:160]}')
        print(f'  [{"ok  " if ok else "FAIL"}] {script} - {note}')

    print()
    if failed:
        print(f'{len(failed)} script(s) failed: {", ".join(failed)}')
        return 1
    print(f'all {len(SCRIPTS)} check scripts passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
