"""An assignment's questions, outline and rubric, read from its instructor pages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .session import Session


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
