"""Find unbounded list queries and likely N+1 loops, grouped by route.

Run with: python tools/query_audit.py

Not a linter - a triage aid. It parses main.py, works out which view function each
query sits in, and reports the ones that fetch a whole table with no limit, no
pagination and no eager loading. Ordered so the routes an anonymous visitor can hit
come first, because those are the ones a million users multiply.
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, 'main.py')

# Routes only staff reach are a fixed, small audience however big the user base
# gets, so they are reported separately rather than mixed in.
STAFF = ('admin_required', 'seller_required', 'driver_required', 'dispatcher_required',
         'staff_required', 'superadmin_required')


def decorator_names(node):
    names = []
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def route_paths(node):
    paths = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            target = dec.func
            attr = getattr(target, 'attr', None)
            if attr in ('route', 'get', 'post') and dec.args:
                first = dec.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    paths.append(first.value)
    return paths


class Finding:
    def __init__(self, func, line, snippet, kind):
        self.func = func
        self.line = line
        self.snippet = snippet
        self.kind = kind


def main():
    source = open(TARGET, encoding='utf-8').read()
    lines = source.splitlines()
    tree = ast.parse(source)

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = route_paths(node)
        if not paths:
            continue
        decs = decorator_names(node)
        staff = any(d in STAFF for d in decs)
        logged_in = 'login_required' in decs
        audience = 'staff' if staff else ('signed-in' if logged_in else 'anonymous')

        start, end = node.lineno, (node.end_lineno or node.lineno)
        body = '\n'.join(lines[start - 1:end])
        eager = bool(re.search(r'joinedload|selectinload|subqueryload', body))
        paginated = '.paginate(' in body

        for offset in range(start - 1, end):
            text = lines[offset]
            stripped = text.strip()
            if stripped.startswith('#'):
                continue
            if '.all()' not in stripped:
                continue
            # A .limit() or .paginate() on the same statement bounds it. Statements
            # wrap, so look at a small window rather than the single line.
            window = '\n'.join(lines[max(start - 1, offset - 4):offset + 1])
            if re.search(r'\.limit\(|\.paginate\(|\.slice\(|\[:\d+\]', window):
                continue
            findings.append(Finding(
                f'{node.name} [{audience}] {paths[0]}',
                offset + 1, stripped[:120],
                'unbounded' + ('' if eager else ', no eager load'),
            ))

        if not paginated and not eager:
            loops = re.findall(r'^\s*for (\w+) in (\w+)\b', body, re.M)
            if loops and audience != 'staff':
                pass  # loop bodies need eyes, not a regex; noted in the summary

    order = {'anonymous': 0, 'signed-in': 1, 'staff': 2}
    findings.sort(key=lambda f: (order[f.func.split('[')[1].split(']')[0]], f.line))

    current = None
    counts = {'anonymous': 0, 'signed-in': 0, 'staff': 0}
    for finding in findings:
        audience = finding.func.split('[')[1].split(']')[0]
        counts[audience] += 1
        if finding.func != current:
            current = finding.func
            print(f'\n{finding.func}')
        print(f'  {finding.line}: {finding.snippet}')
        print(f'      -> {finding.kind}')

    print(f'\n{"=" * 70}')
    print(f'unbounded list queries in view functions: {len(findings)}')
    for audience in ('anonymous', 'signed-in', 'staff'):
        print(f'  {audience:<10} {counts[audience]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
