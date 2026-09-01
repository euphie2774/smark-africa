"""Report where every digital product's file actually lives, and who can reach it.

Three files are tracked in git under `static/uploads/digital/`, and a commit body
promised a check before anything was done about them: "Still tracked, deliberately,
pending a check that nothing serves them from local disk". This is that check, written
as a standing audit rather than a one-off query, because the answer changes every time
a seller uploads.

What it establishes, per digital product:

  storage      whether file_path is a Cloudinary reference (private, signed per
               download) or a plain filename served from static/uploads/digital/
  on disk      whether the local file is actually there
  in git       whether it is tracked, which for a public repository means anyone can
               download it regardless of what the app allows
  sold         how many order items exist for it, so "can this be untracked safely"
               has an answer that is not a guess - static/uploads is wiped on every
               redeploy, so a tracked file is the only reason a legacy local download
               still works in production

The point of the last two columns together: untracking a file that has been sold
breaks a paid download on the next deploy, and leaving one tracked in a public repo
publishes it. Which of those is acceptable is not a decision this script makes.

Read-only. It opens no transaction and writes nothing.

    .\\venv\\Scripts\\python.exe -u tools\\digital_file_audit.py
"""

import os
import subprocess
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('DISABLE_OUTBOUND_WORKER', '1')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402,F401 - imported for its side effects (app, db, migrations)
from main import app, db, is_cloudinary_reference  # noqa: E402
from models import OrderItem, Product  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tracked_in_git():
    """The set of repo-relative paths git is tracking under static/uploads/digital."""
    try:
        out = subprocess.run(['git', 'ls-files', 'static/uploads/digital'],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {line.strip().rsplit('/', 1)[-1] for line in out.stdout.splitlines()
            if line.strip()}


def main_report():
    tracked = tracked_in_git()
    folder = os.path.join(app.config['UPLOAD_FOLDER'], 'digital')

    with app.app_context():
        rows = Product.query.filter(
            Product.is_digital == True,  # noqa: E712 - SQLAlchemy needs the operator
            Product.file_path.isnot(None),
            Product.file_path != ''
        ).order_by(Product.id.asc()).all()

        # One grouped count rather than a query per product: this is an audit, but it
        # is also the shape every list page here got wrong at least once.
        sold = dict(db.session.query(OrderItem.product_id, db.func.count(OrderItem.id))
                    .group_by(OrderItem.product_id).all())

        print('%-5s %-11s %-7s %-7s %-6s %s' %
              ('id', 'storage', 'on disk', 'in git', 'sold', 'file_path'))
        print('-' * 100)

        exposed = []
        for product in rows:
            path = product.file_path or ''
            cloudy = is_cloudinary_reference(path)
            base = os.path.basename(path)
            on_disk = os.path.isfile(os.path.join(folder, base)) if not cloudy else None
            in_git = (base in tracked) if (tracked is not None and not cloudy) else None
            count = sold.get(product.id, 0)

            print('%-5s %-11s %-7s %-7s %-6s %s' % (
                product.id,
                'cloudinary' if cloudy else 'local',
                '-' if on_disk is None else ('yes' if on_disk else 'MISSING'),
                '-' if in_git is None else ('YES' if in_git else 'no'),
                count,
                path[:60]))

            if not cloudy and on_disk:
                exposed.append((product.id, base, in_git, count))

        print('')
        print('%d digital product(s) with a file; %d served from local disk.'
              % (len(rows), len(exposed)))

        if tracked is None:
            print('git ls-files could not be read, so the "in git" column is unknown.')

        # Files on disk that no product points at. These are the ones with nothing to
        # break: nobody can buy them, and nothing serves them but the static route.
        if os.path.isdir(folder):
            referenced = {os.path.basename(p.file_path or '') for p in rows}
            orphans = sorted(name for name in os.listdir(folder)
                             if name not in referenced)
            if orphans:
                print('')
                print('%d file(s) on disk that no product references:' % len(orphans))
                for name in orphans:
                    flag = ''
                    if tracked is not None and name in tracked:
                        flag = '   [tracked in git]'
                    print('  %s%s' % (name, flag))

        if exposed:
            print('')
            print('Every "local" row above is also reachable at '
                  '/static/uploads/digital/<filename> without a login, because Flask '
                  'serves the whole static tree and download_digital\'s ownership '
                  'check sits on a different URL.')
    return 0


if __name__ == '__main__':
    sys.exit(main_report())
