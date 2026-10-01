"""gscope: an unofficial instructor-side Gradescope command line."""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import __version__, assignment, course, rubric, spec
from .session import AuthError, GradescopeError, Session


def _session(args) -> Session:
    return Session.from_env(args.cookie)


def _write_csv(rows: list[dict], out):
    if not rows:
        return
    w = csv.DictWriter(out, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)


def cmd_courses(args):
    for c in course.courses(_session(args)):
        print(f'{c.id:>9}  {c.shortname}' + (f'  ({c.name})' if c.name and c.name != c.shortname else ''))


def cmd_assignments(args):
    for a in course.assignments(_session(args), args.course):
        pts = '' if a.total_points is None else f'{a.total_points:g} pts'
        subs = '' if a.num_submissions is None else f'{a.num_submissions} subs'
        prog = '' if a.grading_progress is None else f'{a.grading_progress:.0f}% graded'
        print(f'{a.id:>9}  {a.title:<40} {pts:>9} {subs:>9} {prog:>12}')


def cmd_questions(args):
    a = assignment.load(_session(args), args.course, args.assignment)
    print(f'{a.id}  {a.title}')
    for q in a.walk():
        depth = 1 if q.parent_id is None else 2
        print(f"{'  ' * depth}{q.id}  {q.title or '(untitled)'}: {q.weight:g} pts, "
              f'{q.scoring_type} scoring, {len(q.items)} rubric items')


def cmd_rubric_show(args):
    a = assignment.load(_session(args), args.course, args.assignment)
    for q in a.walk():
        print(f"{q.id}  {q.title or '(untitled)'} ({q.weight:g} pts, {q.scoring_type} scoring)")
        for it in q.items:
            pts = rubric.weight_to_points(it.weight, q.scoring_type)
            print(f'    [{it.id}] {pts:+g}  {it.description}')


def cmd_rubric_to_json(args):
    cid, sp = spec.load(args.doc, args.map)
    text = json.dumps(spec.to_json(cid, sp), indent=1, ensure_ascii=False)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(text + '\n')
        print(f'wrote {args.output}: {len(sp)} questions, {sum(len(q.items) for q in sp)} items')
    else:
        print(text)


def cmd_rubric_push(args):
    s = _session(args)
    cid, sp = spec.load(args.spec, args.map)
    plan = rubric.plan_push(s, cid, sp)
    for e in plan:
        print('\n'.join(e.describe()))
    todo = [e for e in plan if not e.skip]
    n = sum(len(e.create) for e in todo)
    if not args.apply:
        print(f'\ndry run: would create {n} items on {len(todo)} questions; rerun with --apply')
        return
    rubric.apply_push(s, cid, plan)
    print(f'\ncreated {n} items on {len(todo)} questions; read back and verified')


def cmd_roster(args):
    _write_csv(course.roster(_session(args), args.course),
               open(args.output, 'w', newline='') if args.output else sys.stdout)


def cmd_scores(args):
    _write_csv(course.scores(_session(args), args.course, args.assignment),
               open(args.output, 'w', newline='') if args.output else sys.stdout)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='gscope', description=__doc__,
        epilog='Auth: the Cookie header from a logged-in browser, read from --cookie FILE, '
               '$GSCOPE_COOKIE, $GSCOPE_COOKIE_FILE, or ~/.config/gscope/cookie.')
    p.add_argument('-V', '--version', action='version', version=f'gscope {__version__}')
    p.add_argument('--cookie', metavar='FILE', help='file holding the Cookie header')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('courses', help='list your courses').set_defaults(func=cmd_courses)

    a = sub.add_parser('assignments', help="list a course's assignments")
    a.add_argument('course', type=int)
    a.set_defaults(func=cmd_assignments)

    a = sub.add_parser('questions', help="an assignment's questions, points and scoring")
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.set_defaults(func=cmd_questions)

    r = sub.add_parser('rubric', help='show or push rubrics').add_subparsers(dest='rcmd', required=True)
    a = r.add_parser('show', help="print an assignment's rubric items, as signed points")
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.set_defaults(func=cmd_rubric_show)
    a = r.add_parser('push', help='create rubric items from a spec (dry run unless --apply)')
    a.add_argument('spec', help='.json spec, or a rubric .md with --map')
    a.add_argument('--map', help='for a .md spec: json mapping its headings to ids')
    a.add_argument('--apply', action='store_true', help='actually create the items')
    a.set_defaults(func=cmd_rubric_push)
    a = r.add_parser('to-json', help='convert a rubric .md and its map to a .json spec')
    a.add_argument('doc')
    a.add_argument('--map', required=True)
    a.add_argument('-o', '--output')
    a.set_defaults(func=cmd_rubric_to_json)

    a = sub.add_parser('roster', help="a course's roster as csv")
    a.add_argument('course', type=int)
    a.add_argument('-o', '--output')
    a.set_defaults(func=cmd_roster)

    a = sub.add_parser('scores', help="an assignment's scores as csv")
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.add_argument('-o', '--output')
    a.set_defaults(func=cmd_scores)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (AuthError, GradescopeError, ValueError) as e:
        print(f'gscope: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
