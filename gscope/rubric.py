"""Create, update and delete rubric items, and push a whole rubric spec.

Routes (the ones the grading page's JavaScript uses):
    POST   /courses/<c>/questions/<q>/rubric_items          {"rubric_item": {description, weight}}
    PATCH  /courses/<c>/questions/<q>/rubric/update_entries {"rubric_items": {id: {...}}, "rubric_item_groups": {}}
    DELETE /courses/<c>/questions/<q>/rubric/delete_entries {"rubric_item_ids": [...]}
(``rubric/create_items`` exists too but is for matrix-rubric columns and needs a group.)

Sign convention.  Specs write points the way a rubric reads: ``-8`` takes off 8,
``+2`` adds 2.  Gradescope stores a *weight* whose meaning depends on the
question's scoring type: under negative scoring a deduction is a positive
weight, and a negative weight adds points.  :func:`points_to_weight` converts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .assignment import Question, RubricItem, load
from .session import Session


def points_to_weight(points: float, scoring_type: Optional[str]) -> float:
    """Signed rubric points (-8 = take off 8) to Gradescope's stored weight."""
    w = -points if scoring_type == 'negative' else points
    return w + 0.0                      # normalise -0.0


def weight_to_points(weight: float, scoring_type: Optional[str]) -> float:
    return points_to_weight(weight, scoring_type)       # the map is its own inverse


def _q(course_id: int, qid: int, tail: str) -> str:
    return f'/courses/{course_id}/questions/{qid}/{tail}'


def create_item(s: Session, course_id: int, question_id: int, description: str,
                weight: float, group_id: Optional[int] = None) -> RubricItem:
    """One rubric item.  ``weight`` is Gradescope's stored value, not signed points."""
    body = {'description': description, 'weight': weight}
    if group_id is not None:
        body['group_id'] = group_id
    d = s.write('POST', _q(course_id, question_id, 'rubric_items'), {'rubric_item': body})
    return RubricItem.from_json({'question_id': question_id, **d})


def update_items(s: Session, course_id: int, question_id: int, changes: dict[int, dict]) -> None:
    """``changes`` maps item id to fields, e.g. ``{297464299: {"weight": 8}}``."""
    s.write('PATCH', _q(course_id, question_id, 'rubric/update_entries'),
            {'rubric_items': {str(k): v for k, v in changes.items()}, 'rubric_item_groups': {}})


def delete_items(s: Session, course_id: int, question_id: int, item_ids: list[int]) -> None:
    s.write('DELETE', _q(course_id, question_id, 'rubric/delete_entries'),
            {'rubric_item_ids': list(item_ids)})


# ---- pushing a spec -----------------------------------------------------------

@dataclass
class SpecItem:
    points: float           # signed as written: -8 takes off 8
    description: str


@dataclass
class SpecQuestion:
    assignment_id: int
    question_id: int
    items: list[SpecItem]
    label: str = ''


@dataclass
class PlanEntry:
    spec: SpecQuestion
    question: Question
    create: list[dict] = field(default_factory=list)    # rubric_item bodies, weights converted
    skip: str = ''                                      # why nothing will be written

    def describe(self) -> list[str]:
        q = self.question
        head = (f"{self.spec.label or q.id}: question {q.id} '{q.title}', {q.weight:g} pts, "
                f"{q.scoring_type} scoring, {len(q.items)} existing items")
        lines = [head]
        if self.skip:
            lines.append(f'    SKIP: {self.skip}')
        for it, body in zip(self.spec.items, self.create):
            lines.append(f"    {it.points:+g} (weight {body['weight']:g})  {it.description[:90]}")
        return lines


def plan_push(s: Session, course_id: int, spec: list[SpecQuestion]) -> list[PlanEntry]:
    """What :func:`apply_push` would do.  Reads only."""
    cache: dict[int, object] = {}
    plan = []
    for sq in spec:
        if sq.assignment_id not in cache:
            cache[sq.assignment_id] = load(s, course_id, sq.assignment_id)
        q = cache[sq.assignment_id].question(sq.question_id)
        e = PlanEntry(sq, q)
        if q.children:
            e.skip = 'question is a group; put rubric items on its parts'
        elif q.items:
            e.skip = 'question already has rubric items (delete them first to re-push)'
        else:
            e.create = [{'description': it.description,
                         'weight': points_to_weight(it.points, q.scoring_type)}
                        for it in sq.items]
        plan.append(e)
    return plan


def apply_push(s: Session, course_id: int, plan: list[PlanEntry]) -> list[list[RubricItem]]:
    """Create the planned items, then read each question back and check it matches."""
    made = []
    for e in plan:
        if e.skip:
            made.append([])
            continue
        made.append([create_item(s, course_id, e.question.id, b['description'], b['weight'])
                     for b in e.create])
    verify(s, course_id, [e for e in plan if not e.skip])
    return made


def verify(s: Session, course_id: int, plan: list[PlanEntry]) -> None:
    """Raise unless each planned question now holds exactly the planned items, in order."""
    bad = []
    for e in plan:
        q = load(s, course_id, e.spec.assignment_id).question(e.question.id)
        live = [(i.description, i.weight) for i in q.items]
        want = [(b['description'], float(b['weight'])) for b in e.create]
        if live != want:
            bad.append(f'question {q.id}: live rubric differs from the spec')
    if bad:
        raise RuntimeError('; '.join(bad))
