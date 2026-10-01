"""Courses, their assignments, roster and score exports."""
from __future__ import annotations

import csv
import html
import io
import re
from dataclasses import dataclass
from typing import Optional

from .session import Session

_COURSE_BOX = re.compile(
    r'<a[^>]*class="courseBox[^"]*"[^>]*href="/courses/(\d+)"[^>]*>(.*?)</a>', re.S)
_SHORTNAME = re.compile(r'courseBox--shortname[^>]*>([^<]*)<')
_NAME = re.compile(r'courseBox--name[^>]*>([^<]*)<')


@dataclass
class CourseInfo:
    id: int
    shortname: str
    name: str


@dataclass
class AssignmentInfo:
    id: int
    title: str
    total_points: Optional[float]
    num_submissions: Optional[int]
    grading_progress: Optional[float]
    is_published: Optional[bool]
    url: str


def courses(s: Session) -> list[CourseInfo]:
    """Every course on the account page (instructor and student alike)."""
    page = s.page('/account')
    out, seen = [], set()
    for cid, body in _COURSE_BOX.findall(page.html):
        if cid in seen:
            continue
        seen.add(cid)
        short = _SHORTNAME.search(body)
        name = _NAME.search(body)
        out.append(CourseInfo(int(cid), html.unescape(short.group(1)).strip() if short else '',
                              html.unescape(name.group(1)).strip() if name else ''))
    return out


def _num(x, typ):
    try:
        return typ(x)
    except (TypeError, ValueError):
        return None


def assignments(s: Session, course_id: int) -> list[AssignmentInfo]:
    """The course dashboard's assignment table."""
    d = s.page(f'/courses/{course_id}').find_props('table_data')
    out = []
    for row in d['table_data']:
        if row.get('type') != 'assignment':
            continue
        aid = str(row['id']).replace('assignment_', '')
        out.append(AssignmentInfo(
            id=int(aid), title=row.get('title', ''),
            total_points=_num(row.get('total_points'), float),
            num_submissions=_num(row.get('num_active_submissions'), int),
            grading_progress=_num(row.get('grading_progress'), float),
            is_published=row.get('is_published'), url=row.get('url', '')))
    return out


def _csv(s: Session, path: str) -> list[dict]:
    r = s.request('GET', path)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig'))))


def roster(s: Session, course_id: int) -> list[dict]:
    """memberships.csv: First Name, Last Name, SID, Email, Role, Section..."""
    return _csv(s, f'/courses/{course_id}/memberships.csv')


def scores(s: Session, course_id: int, assignment_id: int) -> list[dict]:
    """One assignment's scores.csv, one row per student."""
    return _csv(s, f'/courses/{course_id}/assignments/{assignment_id}/scores.csv')
