"""Shrink the brand images that ship on every page load.

Run with: .\\venv\\Scripts\\python.exe tools\\optimize_brand_images.py

Why this exists. `static/images/` carries three byte-identical 913KB PNGs -
`favicon.png`, `smark-africa-logo.png` and `smarkafrica-logo.png` - and two of
them are fetched on a first visit to any page: the navbar logo (`base.html:29`)
and the favicon (`base.html:7-9`, plus the `/favicon.ico` route). That is roughly
1.8MB of images to paint a 128x34 logo slot (`style.css:107`) and a 16px tab icon.

At the traffic this platform is being sized for that is real egress and a slow
first paint on a phone, and it is the cheapest large win available: nothing about
the layout changes, the same filenames stay in place, so no template needs editing.

Two deliberate choices:

  * Nothing is backed up into `static/`. Git already tracks all three files, so the
    originals are one `git checkout -- static/images/<name>` away; a backup folder
    under `static/` would be publicly served *and* shipped in the deploy slug,
    which is strictly worse than the thing it protects against. To make that
    guarantee real the script refuses to touch a file git reports as already
    modified, so there is always a clean version to go back to.
  * The logo is rendered at 512px wide rather than the 256px that would cover the
    navbar at 2x DPI, because invoices are *printed* - `invoice_document_html`
    sizes the logo at 132px CSS, which is about 412px of real pixels at 300dpi.

All three sources are 1254x1254, 8-bit, PNG colour type 2 (truecolour, no alpha),
which is why nothing here converts to RGBA unless it has to pad.

Because the sources are square, the two targets below land on exactly the icon
sizes a web app manifest wants: `favicon.png` becomes 192x192 and
`smark-africa-logo.png` becomes 512x512. `/manifest.webmanifest` declares those
two sizes, so running this script is what makes those declarations literally
true - before it runs the icons still work, they are just the full 1254px art.

Idempotent: a file that is already at or under its target is left alone, so this
can be re-run after adding new art without degrading what it already did.
"""

import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, 'static', 'images')

# (filename, longest edge in px, square). Square pads to a transparent square
# instead of distorting, because a favicon that is not square gets stretched by
# the browser rather than letterboxed.
TARGETS = [
    ('favicon.png', 192, True),
    ('smark-africa-logo.png', 512, False),
    ('smarkafrica-logo.png', 512, False),
]


def human(size):
    return f'{size / 1024:.0f}KB' if size < 1024 * 1024 else f'{size / 1048576:.2f}MB'


def is_clean_in_git(relative):
    """True when git tracks this path and reports no local modification to it.

    The whole reversibility story rests on this: if git has a clean copy then
    overwriting the file is undoable, and if it does not then it is not our art to
    quietly replace.
    """
    try:
        listed = subprocess.run(['git', 'ls-files', '--error-unmatch', relative],
                                cwd=ROOT, capture_output=True, text=True)
        if listed.returncode != 0:
            return False, 'not tracked by git'
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', relative],
                               cwd=ROOT, capture_output=True, text=True)
        if dirty.returncode != 0:
            return False, 'git status failed'
        if dirty.stdout.strip():
            return False, 'already modified in the working tree'
        return True, ''
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f'git unavailable ({exc})'


def optimize(name, longest_edge, square):
    from PIL import Image

    path = os.path.join(IMAGES, name)
    if not os.path.exists(path):
        print(f'  [skip] {name} - not found')
        return 0, 0

    before = os.path.getsize(path)
    with Image.open(path) as source:
        # Keep the source's channel count. All three of these files are PNG colour
        # type 2 - truecolour with no alpha - so converting to RGBA would add a
        # fourth channel that is constant 255: a bigger file for no visible gain.
        # Only pad-to-square below actually needs transparency.
        has_alpha = source.mode in ('RGBA', 'LA', 'PA') or 'transparency' in source.info
        image = source.convert('RGBA' if has_alpha else 'RGB')
        width, height = image.size

        if max(width, height) <= longest_edge and before < 60 * 1024:
            print(f'  [ok]   {name} - already {width}x{height}, {human(before)}')
            return before, before

        # thumbnail keeps the aspect ratio and never scales up, so re-running this
        # on an already-small file cannot blur it.
        image.thumbnail((longest_edge, longest_edge), Image.LANCZOS)

        if square and image.width != image.height:
            # The one place transparency genuinely matters, so this converts
            # regardless of what the source carried.
            side = max(image.width, image.height)
            canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
            canvas.paste(image.convert('RGBA'),
                         ((side - image.width) // 2, (side - image.height) // 2))
            image = canvas

    relative = f'static/images/{name}'
    clean, why = is_clean_in_git(relative)
    if not clean:
        print(f'  [skip] {name} - {why}, so there is no clean copy to restore; '
              f'commit or stash it first')
        return before, before

    image.save(path, format='PNG', optimize=True)
    after = os.path.getsize(path)

    # Refuse to make things worse. Some already-tuned PNGs come back larger from a
    # re-encode; in that case put the original back and say so.
    if after >= before:
        subprocess.run(['git', 'checkout', '--', relative], cwd=ROOT,
                       capture_output=True, text=True)
        print(f'  [keep] {name} - re-encode was not smaller ({human(after)}), reverted')
        return before, before

    print(f'  [cut]  {name} - {width}x{height} {human(before)} '
          f'-> {image.width}x{image.height} {human(after)}')
    return before, after


def main():
    try:
        import PIL  # noqa: F401
    except ImportError:
        print('Pillow is not importable in this interpreter. Use .\\venv\\Scripts\\python.exe')
        return 1

    print('shrinking the brand images served on every page')
    total_before = total_after = 0
    for name, edge, square in TARGETS:
        before, after = optimize(name, edge, square)
        total_before += before
        total_after += after

    print()
    if total_before:
        saved = total_before - total_after
        print(f'{human(total_before)} -> {human(total_after)} '
              f'({human(saved)} saved, {saved / total_before * 100:.0f}%)')
    print('Nothing else to change: the filenames are unchanged, so base.html, the '
          '/favicon.ico route and invoice_document_html all pick this up as-is.')
    print('Check the tab icon and the navbar at a glance before committing. To undo: '
          'git checkout -- static/images/')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
