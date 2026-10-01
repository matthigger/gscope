"""Submissions of an assignment, and matching scanned ones to students.

    GET   /courses/<c>/assignments/<a>/submissions?page=N           {submissions, detailed_submissions}
    GET   /courses/<c>/assignments/<a>/submissions/matching_status  {processed_fraction, ...}
    PATCH /courses/<c>/assignments/<a>/submissions/<s>/identify     assignment_submission[user_id]
    PATCH /courses/<c>/assignments/<a>/submissions/<s>/detach_name

Gradescope matches scans to the roster on its own, from the outline's name and
SID regions; ``identify`` fixes the ones it could not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .session import GradescopeError, Session


@dataclass
class Submission:
    id: int
    batch_id: Optional[int]
    user_ids: list[int]
    automatic: bool             # matched to the roster by Gradescope
    graded: bool
    grading_progress: Optional[float]
    name_crop_url: Optional[str] = field(default=None, repr=False)   # the scanned name region, for matching by eye

    @property
    def matched(self) -> bool:
        return bool(self.user_ids)


def _base(course_id: int, assignment_id: int) -> str:
    return f'/courses/{course_id}/assignments/{assignment_id}/submissions'


def _json(s: Session, path: str):
    r = s.request('GET', path, headers={'Accept': 'application/json'})
    if r.status_code != 200:
        raise GradescopeError(f'GET {path}: HTTP {r.status_code}')
    return r.json()


def list_all(s: Session, course_id: int, assignment_id: int, max_pages: int = 100) -> list[Submission]:
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        d = _json(s, f'{_base(course_id, assignment_id)}?page={page}')
        det = d.get('detailed_submissions') or {}
        new = [k for k in det if k not in seen]
        if not new:
            break
        for k in new:
            seen.add(k)
            x = det[k]
            crop = (x.get('masked_identification_region_crop') or {}).get('url')
            out.append(Submission(int(x['id']), x.get('batch_id'), list(x.get('active_user_ids') or []),
                                  bool(x.get('ownership_created_automatically')), bool(x.get('graded')),
                                  x.get('grading_progress'), crop))
    return out


def matching_status(s: Session, course_id: int, assignment_id: int) -> float:
    """Fraction of submissions Gradescope has finished trying to match, 0 to 1."""
    return float(_json(s, f'{_base(course_id, assignment_id)}/matching_status').get('processed_fraction') or 0)


def identify(s: Session, course_id: int, assignment_id: int, submission_id: int, user_id: int) -> None:
    """Assign an unmatched submission to a roster user (ids from course.roster_users)."""
    r = s.request('PATCH', f'{_base(course_id, assignment_id)}/{submission_id}/identify',
                  data={'assignment_submission[user_id]': str(user_id)},
                  headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest',
                           'X-CSRF-Token': s.csrf()})
    if r.status_code not in (200, 204):
        raise GradescopeError(f'identify submission {submission_id}: HTTP {r.status_code}')


def detach(s: Session, course_id: int, assignment_id: int, submission_id: int) -> None:
    """Remove the student from a submission (to fix a wrong match)."""
    s.write('PATCH', f'{_base(course_id, assignment_id)}/{submission_id}/detach_name', None)


def roster_users(s: Session, course_id: int, assignment_id: int) -> list[dict]:
    """Roster with Gradescope user ids (id, name, email, sid, role), as Manage Scans sees it."""
    d = _json(s, f'/courses/{course_id}/assignments/{assignment_id}/submission_batches')
    return d.get('roster', [])
