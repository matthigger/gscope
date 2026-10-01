"""Set up a scanned quiz from one file: assignments, outlines, scoring, rubric, scans.

A setup file (JSON; relative paths are relative to it)::

    {"course_id": 123,
     "scoring": "negative",
     "assignments": [
       {"title": "quiz2a_q1_cov_match16",
        "template": "templates/quiz2a_q1_cov_match16.pdf",
        "questions": [{"title": "Covariance Matching", "weight": 20}],
        "scans": ["scans/a1*.pdf"]},
       {"title": "quiz2a_q2_cov_corr01",
        "template": "templates/quiz2a_q2_cov_corr01.pdf",
        "questions": [{"title": "Sample Covariance", "parts": [
            {"title": "i", "weight": 7}, {"title": "ii", "weight": 7}, {"title": "iii", "weight": 6}]}]}],
     "rubric": {"doc": "quiz2_rubric.md",
                "map": {"A": {"Q1": ["quiz2a_q1_cov_match16", "1"],
                              "Q2.1": ["quiz2a_q2_cov_corr01", "1.1"]}}}}

An assignment's ``"outline"`` may be given explicitly (an outline JSON, see
:mod:`gscope.outline`); otherwise its regions are guessed from the template.

Every step is skipped when already done, so the file can be re-run as the
quiz moves along (e.g. once more when the scans exist):

    create   an "Exam / Quiz" assignment with this title, if none exists
    outline  the questions and regions, if the assignment has no questions yet
    scoring  every question's scoring type, with floor and ceiling
    rubric   the rubric items, on questions that have none
    scans    upload scan PDFs whose file names are not uploaded yet, then wait
"""
from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import assignment, course, outline, rubric, scans, spec as spec_mod, submissions
from .session import Session

STEPS = ('create', 'outline', 'scoring', 'rubric', 'scans')


@dataclass
class AssignmentSetup:
    title: str
    template: Path
    questions: list[dict]
    outline: Optional[dict] = None
    scans: list[str] = field(default_factory=list)


@dataclass
class QuizSetup:
    course_id: int
    assignments: list[AssignmentSetup]
    scoring: str = 'negative'
    rubric_doc: Optional[Path] = None
    rubric_map: Optional[dict] = None
    root: Path = Path('.')

    @classmethod
    def load(cls, path) -> 'QuizSetup':
        path = Path(path)
        d = json.loads(path.read_text())
        root = path.parent
        rel = lambda p: (root / p).resolve()
        asg = [AssignmentSetup(a['title'], rel(a['template']), a.get('questions', []),
                               a.get('outline'), a.get('scans', [])) for a in d['assignments']]
        rub = d.get('rubric') or {}
        rmap = rub.get('map')
        if isinstance(rmap, str):
            rmap = json.loads(rel(rmap).read_text())
        return cls(int(d['course_id']), asg, d.get('scoring', 'negative'),
                   rel(rub['doc']) if rub.get('doc') else None, rmap, root)

    def outline_for(self, a: AssignmentSetup) -> outline.Outline:
        if a.outline:
            return outline.Outline.from_json(a.outline)
        return outline.guess(a.template, a.questions)

    def scan_files(self, a: AssignmentSetup) -> list[Path]:
        out = []
        for pattern in a.scans:
            hits = sorted(glob.glob(str(self.root / pattern)))
            out += [Path(h) for h in hits]
        return out


def _same_tree(live: assignment.Assignment, want: outline.Outline) -> bool:
    a = [(q.title, q.weight, [(c.title, c.weight) for c in q.children]) for q in live.questions]
    b = [(q.title, q.total(), [(p.title, p.weight) for p in q.parts]) for q in want.questions]
    return a == b


def run(s: Session, qs: QuizSetup, apply: bool = False, steps=STEPS,
        say: Callable[[str], None] = print, scan_timeout: float = 1800) -> None:
    """Report (and with ``apply``, do) each step for each assignment."""
    cid = qs.course_id
    act = 'doing' if apply else 'would'
    existing = {}
    for a in course.assignments(s, cid):
        existing.setdefault(a.title, []).append(a.id)

    ids: dict[str, int] = {}
    for a in qs.assignments:
        say(f'== {a.title}')
        hits = existing.get(a.title, [])
        if len(hits) > 1:
            say(f'   {len(hits)} assignments share this title; fix that by hand first')
            continue
        if hits:
            ids[a.title] = hits[0]
            say(f'   create: exists, id {hits[0]}')
        elif 'create' in steps:
            if not a.template.exists():
                say(f'   create: template missing: {a.template}')
                continue
            if apply:
                ids[a.title] = assignment.create_exam(s, cid, a.title, a.template)
                say(f'   create: created, id {ids[a.title]}')
            else:
                say(f'   create: {act} create it from {a.template.name}')
        aid = ids.get(a.title)

        want = qs.outline_for(a) if ('outline' in steps or 'scoring' in steps) else None
        if 'outline' in steps and want is not None:
            live = assignment.load(s, cid, aid) if aid else None
            pts = f'{want.total():g} pts: ' + ', '.join(
                f'{q.title} ({"+".join(f"{p.weight:g}" for p in q.parts) or f"{q.total():g}"})'
                for q in want.questions)
            if live and live.questions:
                ok = _same_tree(live, want)
                say(f'   outline: has questions already; {"matches" if ok else "DIFFERS from"} the setup')
            elif apply and aid:
                outline.push(s, cid, aid, want)
                say(f'   outline: saved, {pts}')
            else:
                say(f'   outline: {act} save {pts}')

        if 'scoring' in steps:
            live = assignment.load(s, cid, aid) if aid else None
            if live is None or not live.questions:
                say(f'   scoring: {act} set {qs.scoring} scoring once the outline exists')
            else:
                todo = [q for q in live.walk() if q.scoring_type != qs.scoring]
                if not todo:
                    say(f'   scoring: all questions {qs.scoring}')
                elif apply:
                    for q in todo:
                        assignment.set_grading_options(s, cid, q.id, qs.scoring)
                    say(f'   scoring: set {qs.scoring} on {len(todo)} questions')
                else:
                    say(f'   scoring: {act} set {qs.scoring} on {len(todo)} questions')
                groups = [q for q in live.walk() if q.children and rubric.only_default(q)]
                if groups and apply:
                    for q in groups:
                        rubric.delete_items(s, cid, q.id, [i.id for i in q.items])
                    say(f'   scoring: removed the default "Correct" item from {len(groups)} question groups')
                elif groups:
                    say(f'   scoring: {act} remove the default "Correct" item from {len(groups)} question groups')

        if 'scans' in steps and a.scans and not qs.scan_files(a):
            say(f'   scans: none yet ({", ".join(a.scans)})')
        elif 'scans' in steps and a.scans:
            files = qs.scan_files(a)
            done = {b.filename for b in scans.batches(s, cid, aid)} if aid else set()
            new = [f for f in files if f.name not in done]
            say(f'   scans: {len(files)} files, {len(files) - len(new)} uploaded already')
            if new and apply and aid:
                bs = [scans.upload(s, cid, aid, f) for f in new]
                say(f'   scans: uploaded {len(bs)}; waiting for Gradescope to split them')
                bs = scans.wait(s, cid, aid, [b.id for b in bs], timeout=scan_timeout)
                manual = [b for b in bs if b.needs_manual_split or b.complete_status == 'failed']
                say(f'   scans: {sum(b.num_submissions for b in bs)} submissions made'
                    + (f'; split by hand in Manage Scans: {", ".join(b.filename for b in manual)}' if manual else ''))
            elif new:
                say(f'   scans: {act} upload {", ".join(f.name for f in new)}')
            if aid:
                subs = submissions.list_all(s, cid, aid)
                if subs:
                    unmatched = [x for x in subs if not x.matched]
                    say(f'   scans: {len(subs)} submissions, {len(subs) - len(unmatched)} matched to students, '
                        f'{len(unmatched)} to match by hand (Manage Submissions)')

    if 'rubric' in steps and qs.rubric_doc and qs.rubric_map:
        say('== rubric')
        if not qs.rubric_doc.exists():
            say(f'   rubric: not yet ({qs.rubric_doc} does not exist)')
            return
        rmap = {'course_id': cid, **qs.rubric_map}
        try:
            _, sp = spec_mod.from_markdown(qs.rubric_doc.read_text(), rmap)
            plan = rubric.plan_push(s, cid, sp)
        except (KeyError, ValueError) as e:
            say(f'   rubric: not yet ({e})')
            return
        for e in plan:
            n = len(e.create)
            say(f'   {e.spec.label}: ' + (e.skip if e.skip else f'{act} create {n} items'))
        if apply:
            rubric.apply_push(s, cid, plan)
            say('   rubric: created and verified')
