"""Assignment outlines: question tree, points and page regions.

Saved with the request the Edit Outline page sends::

    PATCH /courses/<c>/assignments/<a>/outline/
    {"assignment": {"identification_regions": {"name": RECT|null, "sid": RECT|null}},
     "question_data": [{"id"?, "title", "weight", "crop_rect_list": [RECT...],
                        "children"?: [...]}]}

RECT is ``{x1, x2, y1, y2, page_number}`` in percent of the page (y from the
top), pages counted from 1.  A question left out of ``question_data`` is
deleted along with its grading, so :func:`push` refuses to drop questions
unless told to, and never once students' submissions exist.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .session import GradescopeError, Session


@dataclass
class Rect:
    x1: float
    x2: float
    y1: float
    y2: float
    page: int = 1

    def to_json(self) -> dict:
        r = lambda v: round(max(0.0, min(100.0, v)), 1)
        return {'x1': r(self.x1), 'x2': r(self.x2), 'y1': r(self.y1), 'y2': r(self.y2),
                'page_number': self.page}

    @classmethod
    def from_json(cls, d: dict) -> 'Rect':
        return cls(float(d['x1']), float(d['x2']), float(d['y1']), float(d['y2']),
                   int(d.get('page_number', d.get('page', 1))))


@dataclass
class OutlineQuestion:
    title: str
    weight: float
    regions: list[Rect] = field(default_factory=list)
    parts: list['OutlineQuestion'] = field(default_factory=list)
    id: Optional[int] = None

    def total(self) -> float:
        return round(sum(p.weight for p in self.parts), 4) if self.parts else self.weight

    def to_json(self) -> dict:
        regions = self.regions or _union([r for p in self.parts for r in p.regions])
        d = {'title': self.title, 'weight': self.total(),
             'crop_rect_list': [r.to_json() for r in regions]}
        if self.parts:
            d['children'] = [p.to_json() for p in self.parts]
        if self.id is not None:
            d['id'] = self.id
        return d


@dataclass
class Outline:
    questions: list[OutlineQuestion]
    name: Optional[Rect] = None
    sid: Optional[Rect] = None

    def total(self) -> float:
        return round(sum(q.total() for q in self.questions), 4)

    def payload(self) -> dict:
        if self.name and self.sid and self.name.page != self.sid.page:
            raise ValueError('the name and SID regions must be on the same page')
        for q in self.questions:
            for p in q.parts:
                if p.parts:
                    raise ValueError(f'{q.title}/{p.title}: Gradescope outlines nest one level only')
            if not (q.regions or any(p.regions for p in q.parts)):
                raise ValueError(f'question {q.title!r} has no page region')
        return {'assignment': {'identification_regions': {
                    'name': self.name.to_json() if self.name else None,
                    'sid': self.sid.to_json() if self.sid else None}},
                'question_data': [q.to_json() for q in self.questions]}

    # spec files ------------------------------------------------------------------
    @classmethod
    def from_json(cls, d: dict) -> 'Outline':
        def q(x):
            return OutlineQuestion(x['title'], float(x.get('weight', 0)),
                                   [Rect.from_json(r) for r in x.get('regions', [])],
                                   [q(p) for p in x.get('parts', [])], x.get('id'))
        return cls([q(x) for x in d['questions']],
                   Rect.from_json(d['name']) if d.get('name') else None,
                   Rect.from_json(d['sid']) if d.get('sid') else None)

    def to_json(self) -> dict:
        def q(x):
            d = {'title': x.title, 'weight': x.total(), 'regions': [r.to_json() for r in x.regions]}
            if x.parts:
                d['parts'] = [q(p) for p in x.parts]
            if x.id is not None:
                d['id'] = x.id
            return d
        return {'name': self.name.to_json() if self.name else None,
                'sid': self.sid.to_json() if self.sid else None,
                'questions': [q(x) for x in self.questions]}


def _union(rects: list[Rect]) -> list[Rect]:
    """Per page, the bounding box of the rects on it."""
    out = {}
    for r in rects:
        u = out.get(r.page)
        out[r.page] = r if u is None else Rect(min(u.x1, r.x1), max(u.x2, r.x2),
                                               min(u.y1, r.y1), max(u.y2, r.y2), r.page)
    return [out[k] for k in sorted(out)]


# ---- reading and pushing ---------------------------------------------------------

def current(s: Session, course_id: int, assignment_id: int) -> dict:
    """Edit Outline's page data: assignment (with id_regions), outline, has_submissions_and_students."""
    return s.page(f'/courses/{course_id}/assignments/{assignment_id}/outline/edit') \
            .find_props('outline', 'assignment')


def _ids(questions: list[dict]) -> set[int]:
    out = set()
    for q in questions:
        if q.get('id') is not None:
            out.add(int(q['id']))
        out |= _ids(q.get('children') or [])
    return out


def push(s: Session, course_id: int, assignment_id: int, outline: Outline,
         replace: bool = False) -> dict:
    """Save ``outline``.  Existing questions not in it (by id) are deleted, which
    needs ``replace=True`` and is refused outright once there are submissions."""
    cur = current(s, course_id, assignment_id)
    body = outline.payload()
    dropped = _ids(cur['outline']) - _ids(body['question_data'])
    if dropped and cur.get('has_submissions_and_students'):
        raise GradescopeError(f'assignment {assignment_id} has submissions; refusing to delete '
                              f'questions {sorted(dropped)} (and their grading)')
    if dropped and not replace:
        raise GradescopeError(f'assignment {assignment_id} already has questions {sorted(dropped)} '
                              'that this outline would delete; pass replace=True (--replace)')
    return s.write('PATCH', f'/courses/{course_id}/assignments/{assignment_id}/outline/', body,
                   referer=f'/courses/{course_id}/assignments/{assignment_id}/outline/edit')


# ---- guessing regions from a template PDF -----------------------------------------

_LABEL = re.compile(r'^(\(?\d{1,2}[.)]|\(?[ivx]{1,5}[.)]?|\(?[a-h][.)])$')


@dataclass
class _Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


def template_words(pdf) -> tuple[list[_Word], int]:
    """Every word with its box in percent of its page, and the page count
    (needs poppler's pdftotext)."""
    if not shutil.which('pdftotext'):
        raise GradescopeError('outline guessing needs pdftotext (poppler-utils)')
    out = subprocess.run(['pdftotext', '-bbox', str(pdf), '-'], capture_output=True,
                         text=True, check=True).stdout
    words, page, W, H = [], 0, 1.0, 1.0
    for line in out.splitlines():
        if m := re.search(r'<page width="([\d.]+)" height="([\d.]+)"', line):
            page += 1
            W, H = map(float, m.groups())
        elif m := re.search(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', line):
            x0, y0, x1, y1 = map(float, m.groups()[:4])
            words.append(_Word(m.group(5), 100 * x0 / W, 100 * y0 / H, 100 * x1 / W, 100 * y1 / H, page))
    return words, page


def _part_labels(words: list[_Word], n: int, below: float) -> list[_Word]:
    """The n part labels: same left indent, below the problem header, top to bottom."""
    cands = [w for w in words if w.page == 1 and w.y0 > below and w.x0 < 20 and _LABEL.match(w.text)]
    clusters: dict[float, list[_Word]] = {}
    for w in cands:
        key = next((k for k in clusters if abs(k - w.x0) < 1.0), w.x0)
        clusters.setdefault(key, []).append(w)
    for x in sorted(clusters):
        group = sorted(clusters[x], key=lambda w: w.y0)
        if len(group) == n:
            return group
    raise GradescopeError(f'could not find {n} part labels on page 1 of the template; '
                          'give the regions explicitly')


def guess(pdf, questions: list[dict], back: bool = True) -> Outline:
    """Regions for a one-question-per-sheet template: the name/NUID boxes from the
    "Name" and "NUID:" labels, the question from its "Problem" header down, and
    each part from its label (1. / i / (a) ...) to the next.  With ``back`` the last
    part (or the question) also covers all of page 2, the back of the sheet, so a
    grader sees work that continued there.

    ``questions`` is ``[{"title": ..., "weight": 20}]`` or with
    ``"parts": [{"title": "i", "weight": 7}, ...]``; only one question per
    template is supported.
    """
    if len(questions) != 1:
        raise ValueError('guess() handles one question per template; write the outline by hand')
    words, pages = template_words(pdf)
    find = lambda t: next((w for w in words if w.page == 1 and w.text == t), None)
    name_w, sid_w = find('Name'), find('NUID:')
    name = sid = None
    if name_w:
        right = sid_w.x0 - 1 if sid_w else 95
        name = Rect(name_w.x0 - 1.5, right, name_w.y0 - 3, name_w.y1 + 4, 1)
    if sid_w:
        sid = Rect(sid_w.x0 - 0.5, 95, sid_w.y0 - 3, sid_w.y1 + 4, 1)
    head = find('Problem')
    top = (head.y0 - 1) if head else ((name.y2 + 1) if name else 0)

    spec = questions[0]
    tail = [Rect(0, 100, 0, 100, 2)] if back and pages >= 2 else []
    if not spec.get('parts'):
        q = OutlineQuestion(spec['title'], float(spec['weight']), [Rect(0, 100, top, 100, 1)] + tail)
        return Outline([q], name, sid)
    labels = _part_labels(words, len(spec['parts']), top + 1)
    starts = [top] + [w.y0 - 1 for w in labels[1:]]
    ends = starts[1:] + [100]
    parts = []
    for k, (p, y1, y2) in enumerate(zip(spec['parts'], starts, ends)):
        regions = [Rect(0, 100, y1, y2, 1)] + (tail if k == len(spec['parts']) - 1 else [])
        parts.append(OutlineQuestion(p['title'], float(p['weight']), regions))
    return Outline([OutlineQuestion(spec['title'], 0, [], parts)], name, sid)


def load_spec(path) -> Outline:
    import json
    return Outline.from_json(json.loads(Path(path).read_text()))
