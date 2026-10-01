"""An assignment's questions, outline and rubric, read from its instructor pages."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .session import GradescopeError, Session


@dataclass
class RubricItem:
    id: int
    question_id: int
    description: str
    weight: float           # as Gradescope stores it; see rubric.points_to_weight
    position: int = 0
    group_id: Optional[int] = None

    @classmethod
    def from_json(cls, d: dict) -> 'RubricItem':
        return cls(int(d['id']), int(d['question_id']), d.get('description', ''),
                   float(d.get('weight') or 0), int(d.get('position') or 0), d.get('group_id'))


@dataclass
class Question:
    id: int
    title: str
    weight: float
    scoring_type: Optional[str]          # 'positive' or 'negative'
    parent_id: Optional[int] = None
    type: Optional[str] = None           # 'FreeResponseQuestion', 'QuestionGroup', ...
    index: Optional[int] = None
    children: list['Question'] = field(default_factory=list)
    items: list[RubricItem] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return bool(self.children) or self.type == 'QuestionGroup'


@dataclass
class Assignment:
    course_id: int
    id: int
    title: str
    questions: list[Question]            # top level, children nested

    def walk(self):
        """Every question, depth first, groups before their children."""
        todo = list(reversed(self.questions))
        while todo:
            q = todo.pop()
            yield q
            todo.extend(reversed(q.children))

    def question(self, qid: int) -> Question:
        for q in self.walk():
            if q.id == qid:
                return q
        raise KeyError(f'assignment {self.id} has no question {qid}')

    def leaves(self) -> list[Question]:
        return [q for q in self.walk() if not q.children]

    def numbered(self) -> dict[str, Question]:
        """Questions by their number as Gradescope shows it: "1", "2", "2.1", "2.2"."""
        out = {}
        for i, q in enumerate(self.questions, 1):
            out[str(i)] = q
            for j, c in enumerate(q.children, 1):
                out[f'{i}.{j}'] = c
        return out

    def find(self, ref) -> Question:
        """A question by id (int), number ("2.1"), or exact title."""
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit() and len(ref) > 3):
            return self.question(int(ref))
        num = self.numbered()
        if str(ref) in num:
            return num[str(ref)]
        hits = [q for q in self.walk() if q.title == ref]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f'assignment {self.id}: no single question {ref!r} '
                       f'(numbers: {", ".join(num)})')


def _question(d: dict) -> Question:
    return Question(
        id=int(d['id']), title=d.get('title') or '', weight=float(d.get('weight') or 0),
        scoring_type=d.get('scoring_type'), parent_id=d.get('parent_id'), type=d.get('type'),
        index=d.get('index'), children=[_question(c) for c in d.get('children') or []])


def load(s: Session, course_id: int, assignment_id: int) -> Assignment:
    """Questions (nested) with their rubric items, from the Create Rubric page.

    That page holds the whole assignment in one blob.  A question's own grading
    page is less dependable: it sometimes redirects to the submissions list.
    """
    page = s.page(f'/courses/{course_id}/assignments/{assignment_id}/rubric/edit')
    d = page.find_props('questions', 'rubric_items')
    a = Assignment(course_id, assignment_id, (d.get('assignment') or {}).get('title', ''),
                   [_question(q) for q in d['questions']])
    by_id = {q.id: q for q in a.walk()}
    for it in d['rubric_items']:
        item = RubricItem.from_json(it)
        if item.question_id in by_id:
            by_id[item.question_id].items.append(item)
    for q in by_id.values():
        q.items.sort(key=lambda i: i.position)
    return a


def outline(s: Session, course_id: int, assignment_id: int) -> list[dict]:
    """The raw outline (questions, points, page regions) from Edit Outline."""
    page = s.page(f'/courses/{course_id}/assignments/{assignment_id}/outline/edit')
    return page.find_props('outline')['outline']


# ---- creating, deleting, settings ------------------------------------------------

def _form_csrf(s: Session, course_id: int) -> str:
    return s.page(f'/courses/{course_id}/assignments').csrf or s.csrf()


def create_exam(s: Session, course_id: int, title: str, template_pdf,
                anonymized: bool = False, rubric_locking: str = 'all_edit',
                when_to_create_rubric: str = 'while_grading') -> int:
    """Create an "Exam / Quiz" (instructor-uploaded scans) assignment; returns its id.

    The same multipart form the Create Assignment dialog submits: title, the
    template PDF, and the dialog's defaults for instructor-uploaded exams.
    """
    from pathlib import Path
    path = Path(template_pdf).expanduser()
    data = {'authenticity_token': _form_csrf(s, course_id),
            'assignment[title]': title,
            'assignment[submissions_anonymized]': '1' if anonymized else '0',
            'assignment[student_submission]': 'false',
            'assignment[when_to_create_rubric]': when_to_create_rubric,
            'assignment[rubric_locking_setting]': rubric_locking}
    with open(path, 'rb') as f:
        r = s.request('POST', f'/courses/{course_id}/assignments', data=data,
                      files={'template_pdf': (path.name, f, 'application/pdf')},
                      allow_redirects=False)
    loc = r.headers.get('Location', '')
    m = re.search(rf'/courses/{course_id}/assignments/(\d+)', loc)
    if r.status_code not in (301, 302, 303) or not m:
        raise GradescopeError(f'create assignment {title!r}: HTTP {r.status_code}, '
                              f'redirect {loc or "none"} (Gradescope re-shows the form on errors)')
    return int(m.group(1))


def delete(s: Session, course_id: int, assignment_id: int) -> None:
    """Delete an assignment with its template, outline and every submission."""
    r = s.request('POST', f'/courses/{course_id}/assignments/{assignment_id}',
                  data={'_method': 'delete', 'authenticity_token': _form_csrf(s, course_id)},
                  allow_redirects=False)
    if r.status_code not in (200, 302, 303, 204):
        raise GradescopeError(f'delete assignment {assignment_id}: HTTP {r.status_code}')


def set_grading_options(s: Session, course_id: int, question_id: int,
                        scoring_type: str = 'negative', floor: bool = True, ceiling: bool = True) -> None:
    """A question's scoring type and its floor/ceiling at 0 and full marks."""
    if scoring_type not in ('negative', 'positive'):
        raise ValueError(f'scoring_type must be negative or positive, not {scoring_type!r}')
    s.write('POST', f'/courses/{course_id}/questions/{question_id}/save_grading_options',
            {'question': {'id': question_id, 'scoring_type': scoring_type,
                          'floor': '1' if floor else '0', 'ceiling': '1' if ceiling else '0'}})
