"""gscope: an unofficial instructor-side Gradescope command line."""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys

from . import __version__, assignment, course, login, outline, rubric, scans, setup, spec, submissions
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


def _parts(text):
    """"i:7,ii:7,iii:6" -> [{"title": "i", "weight": 7}, ...]"""
    out = []
    for chunk in text.split(','):
        t, _, w = chunk.rpartition(':')
        out.append({'title': t.strip(), 'weight': float(w)})
    return out


def cmd_assignment_create(args):
    if not args.apply:
        print(f'dry run: would create exam/quiz assignment {args.title!r} in course {args.course} '
              f'from {args.template}; rerun with --apply')
        return
    aid = assignment.create_exam(_session(args), args.course, args.title, args.template)
    print(f'created assignment {aid}: {args.title}')


def cmd_assignment_delete(args):
    s = _session(args)
    a = assignment.load(s, args.course, args.assignment)
    if args.confirm != a.title:
        raise ValueError(f'to delete assignment {a.id} with its outline and all submissions, '
                         f'pass --confirm "{a.title}"')
    if not args.apply:
        print(f'dry run: would delete assignment {a.id} {a.title!r}; rerun with --apply')
        return
    assignment.delete(s, args.course, a.id)
    print(f'deleted assignment {a.id}: {a.title}')


def cmd_outline_show(args):
    cur = outline.current(_session(args), args.course, args.assignment)
    print(json.dumps({'id_regions': cur['assignment'].get('id_regions'),
                      'has_submissions_and_students': cur.get('has_submissions_and_students'),
                      'outline': cur['outline']}, indent=1))


def cmd_outline_guess(args):
    q = {'title': args.title}
    if args.parts:
        q['parts'] = _parts(args.parts)
    else:
        q['weight'] = args.weight
    o = outline.guess(args.template, [q], back=not args.no_back)
    text = json.dumps(o.to_json(), indent=1)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(text + '\n')
        print(f'wrote {args.output} ({o.total():g} pts)')
    else:
        print(text)


def cmd_outline_push(args):
    o = outline.load_spec(args.outline)
    if not args.apply:
        print(json.dumps(o.payload(), indent=1))
        print(f'\ndry run: would save this outline ({o.total():g} pts) to assignment {args.assignment}; '
              'rerun with --apply')
        return
    outline.push(_session(args), args.course, args.assignment, o, replace=args.replace)
    print(f'saved outline ({o.total():g} pts) to assignment {args.assignment}')


def cmd_scans_list(args):
    for b in scans.batches(_session(args), args.course, args.assignment):
        print(f'{b.id:>10}  {b.filename:<30} {b.page_count or 0:>4} pages  {b.num_submissions:>3} subs  '
              f'{b.complete_status or ""} {b.status or ""}')


def cmd_scans_upload(args):
    if not args.apply:
        print(f'dry run: would upload {len(args.pdf)} file(s) to assignment {args.assignment}; '
              'rerun with --apply')
        return
    s = _session(args)
    bs = [scans.upload(s, args.course, args.assignment, p) for p in args.pdf]
    print(f'uploaded {len(bs)} file(s)')
    if args.no_wait:
        return
    bs = scans.wait(s, args.course, args.assignment, [b.id for b in bs], timeout=args.timeout,
                    progress=lambda bb: print('  ' + ', '.join(f'{b.filename}: {b.complete_status}' for b in bb)))
    for b in bs:
        note = 'split it by hand in Manage Scans' if b.needs_manual_split else ''
        print(f'{b.filename}: {b.num_submissions} submissions {note}')


def cmd_submissions(args):
    s = _session(args)
    subs = submissions.list_all(s, args.course, args.assignment)
    un = [x for x in subs if not x.matched]
    auto = sum(1 for x in subs if x.matched and x.automatic)
    print(f'{len(subs)} submissions: {len(subs) - len(un)} matched ({auto} automatically), '
          f'{len(un)} unmatched')
    for x in un:
        print(f'  unmatched submission {x.id} (batch {x.batch_id})')


def cmd_setup(args):
    qs = setup.QuizSetup.load(args.setup)
    steps = tuple(args.steps.split(',')) if args.steps else setup.STEPS
    bad = [x for x in steps if x not in setup.STEPS]
    if bad:
        raise ValueError(f'unknown steps {bad}; choose from {",".join(setup.STEPS)}')
    setup.run(_session(args), qs, apply=args.apply, steps=steps)
    if not args.apply:
        print('\ndry run; rerun with --apply')


def cmd_login(args):
    out = args.output or args.cookie or os.environ.get('GSCOPE_COOKIE_FILE') or login.DEFAULT_COOKIE_FILE
    cookie = None
    if args.paste:
        if sys.stdin.isatty():
            cookie = getpass.getpass('Paste the Cookie header (hidden), then Enter: ')
        else:
            cookie = sys.stdin.read()
        if not cookie.strip():
            raise AuthError('no cookie given')
    else:
        print(f'reading the gradescope.com cookie from {args.browser}...', file=sys.stderr)
    path, n = login.login(args.browser, cookie, args.cookie_db, out)
    print(f'logged in: {n} courses visible; cookie saved to {path}')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='gscope', description=__doc__,
        epilog='Sign in with "gscope login", which copies the session cookie from a browser '
               'where you are logged in to Gradescope.  Commands then read it from --cookie FILE, '
               '$GSCOPE_COOKIE, $GSCOPE_COOKIE_FILE, or ~/.config/gscope/cookie.')
    p.add_argument('-V', '--version', action='version', version=f'gscope {__version__}')
    p.add_argument('--cookie', metavar='FILE', help='file holding the Cookie header')
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('login', help='save the Gradescope cookie from your browser (or pasted)')
    a.add_argument('--browser', choices=login.BROWSERS, default=os.environ.get('GSCOPE_BROWSER', 'brave'),
                   help='browser you are logged in with (default $GSCOPE_BROWSER or brave)')
    a.add_argument('--cookie-db', metavar='PATH', help="the browser's cookie database, if not found")
    a.add_argument('--paste', action='store_true', help='paste the Cookie header from DevTools instead')
    a.add_argument('-o', '--output', help='where to save it (default ~/.config/gscope/cookie)')
    a.set_defaults(func=cmd_login)

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

    g = sub.add_parser('assignment', help='create or delete assignments').add_subparsers(dest='acmd', required=True)
    a = g.add_parser('create', help='create an "Exam / Quiz" assignment from a template PDF')
    a.add_argument('course', type=int)
    a.add_argument('title')
    a.add_argument('template')
    a.add_argument('--apply', action='store_true')
    a.set_defaults(func=cmd_assignment_create)
    a = g.add_parser('delete', help='delete an assignment and everything in it')
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.add_argument('--confirm', metavar='TITLE', help="the assignment's exact title")
    a.add_argument('--apply', action='store_true')
    a.set_defaults(func=cmd_assignment_delete)

    g = sub.add_parser('outline', help='show, guess or save outlines').add_subparsers(dest='ocmd', required=True)
    a = g.add_parser('show', help="an assignment's outline and name/SID regions, as JSON")
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.set_defaults(func=cmd_outline_show)
    a = g.add_parser('guess', help='an outline JSON with regions found in a template PDF')
    a.add_argument('template')
    a.add_argument('--title', required=True)
    w = a.add_mutually_exclusive_group(required=True)
    w.add_argument('--weight', type=float, help='points, for a question without parts')
    w.add_argument('--parts', help='part titles and points, e.g. "i:7,ii:7,iii:6"')
    a.add_argument('--no-back', action='store_true', help="don't include the back page in the last region")
    a.add_argument('-o', '--output')
    a.set_defaults(func=cmd_outline_guess)
    a = g.add_parser('push', help='save an outline JSON to an assignment')
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.add_argument('outline')
    a.add_argument('--replace', action='store_true', help='allow deleting existing questions (never with submissions)')
    a.add_argument('--apply', action='store_true')
    a.set_defaults(func=cmd_outline_push)

    g = sub.add_parser('scans', help='upload or list scanned PDFs').add_subparsers(dest='scmd', required=True)
    a = g.add_parser('list', help="an assignment's uploaded scan batches")
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.set_defaults(func=cmd_scans_list)
    a = g.add_parser('upload', help='upload scan PDFs and wait for them to be split')
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.add_argument('pdf', nargs='+')
    a.add_argument('--no-wait', action='store_true')
    a.add_argument('--timeout', type=float, default=1800)
    a.add_argument('--apply', action='store_true')
    a.set_defaults(func=cmd_scans_upload)

    a = sub.add_parser('submissions', help='how many submissions are matched to students')
    a.add_argument('course', type=int)
    a.add_argument('assignment', type=int)
    a.set_defaults(func=cmd_submissions)

    a = sub.add_parser('setup', help='create, outline, score, rubric and upload a quiz from one file')
    a.add_argument('setup', help='setup JSON (see gscope.setup)')
    a.add_argument('--steps', help=f'comma list, default all: {",".join(setup.STEPS)}')
    a.add_argument('--apply', action='store_true')
    a.set_defaults(func=cmd_setup)

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
    except AuthError as e:
        print(f'gscope: {e}', file=sys.stderr)
        if args.cmd != 'login':
            print('gscope: sign in with "gscope login" (log in to Gradescope in your browser first)',
                  file=sys.stderr)
        return 1
    except (GradescopeError, ValueError) as e:
        print(f'gscope: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
