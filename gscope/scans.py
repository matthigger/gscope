"""Uploading scanned PDFs ("Manage Scans") and waiting for Gradescope to split them.

    POST   /courses/<c>/assignments/<a>/submission_batches      multipart, one field: file
    GET    /courses/<c>/assignments/<a>/submission_batches      every batch, as JSON
    GET    /courses/<c>/assignments/<a>/submission_batches/<b>  one batch, as JSON
    DELETE /courses/<c>/assignments/<a>/submission_batches/<b>?batch_updated_at=...

Gradescope splits each upload into submissions by itself, using the length of
the template, and matches them to students from the name and SID regions.
A batch it cannot split confidently ends "ready" with no submissions; split it
in the web page (Manage Scans).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .session import GradescopeError, Session

DONE = ('ready', 'failed', 'template failed')


@dataclass
class Batch:
    id: int
    filename: str
    page_count: Optional[int]
    complete_status: Optional[str]      # template processing, extracting pages, finding split points, ready, failed, template failed
    status: Optional[str]               # was_automatically_split, automatic_split_overruled, None
    num_submissions: int
    updated_at: str

    @classmethod
    def from_json(cls, d: dict) -> 'Batch':
        att = d.get('pdf_attachment') or {}
        url = att.get('url') or ''
        name = url.split('?')[0].rsplit('/', 1)[-1] if url else ''
        subs = d.get('assignment_submissions') or []
        return cls(int(d['id']), name, d.get('page_count'), d.get('complete_status'),
                   d.get('status'), int(d.get('num_active_submissions') or len(subs)),
                   d.get('updated_at') or '')

    @property
    def done(self) -> bool:
        return self.num_submissions > 0 or self.complete_status in DONE

    @property
    def needs_manual_split(self) -> bool:
        return self.complete_status == 'ready' and self.num_submissions == 0


def _base(course_id: int, assignment_id: int) -> str:
    return f'/courses/{course_id}/assignments/{assignment_id}/submission_batches'


def _json(s: Session, path: str):
    r = s.request('GET', path, headers={'Accept': 'application/json'})
    if r.status_code != 200:
        raise GradescopeError(f'GET {path}: HTTP {r.status_code}')
    return r.json()


def batches(s: Session, course_id: int, assignment_id: int) -> list[Batch]:
    d = _json(s, _base(course_id, assignment_id))
    return [Batch.from_json(b) for b in d.get('batches', [])]


def batch(s: Session, course_id: int, assignment_id: int, batch_id: int) -> Batch:
    return Batch.from_json(_json(s, f'{_base(course_id, assignment_id)}/{batch_id}'))


def upload(s: Session, course_id: int, assignment_id: int, pdf) -> Batch:
    """Upload one scan PDF (up to 256 MB) as a new batch."""
    path = Path(pdf).expanduser()
    base = _base(course_id, assignment_id)
    headers = {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest',
               'X-CSRF-Token': s.csrf(f'/courses/{course_id}/assignments/{assignment_id}/outline/edit'),
               'Referer': s.url(base)}
    with open(path, 'rb') as f:
        r = s.request('POST', base, files={'file': (path.name, f, 'application/pdf')}, headers=headers)
    if r.status_code not in (200, 201):
        raise GradescopeError(f'upload {path.name}: HTTP {r.status_code}: {r.text[:300]}')
    return Batch.from_json(r.json())


def wait(s: Session, course_id: int, assignment_id: int, batch_ids: list[int],
         timeout: float = 900, poll: float = 5,
         progress: Optional[Callable[[list[Batch]], None]] = None) -> list[Batch]:
    """Poll until every batch is split (or failed / needs a manual split)."""
    t0 = time.monotonic()
    while True:
        bs = [batch(s, course_id, assignment_id, b) for b in batch_ids]
        if progress:
            progress(bs)
        if all(b.done for b in bs):
            return bs
        if time.monotonic() - t0 > timeout:
            raise GradescopeError(f'batches still processing after {timeout:.0f}s: '
                                  + ', '.join(f'{b.id} ({b.complete_status})' for b in bs if not b.done))
        time.sleep(poll)


def delete(s: Session, course_id: int, assignment_id: int, b: Batch) -> None:
    """Delete a batch and the submissions made from it."""
    s.write('DELETE', f'{_base(course_id, assignment_id)}/{b.id}?batch_updated_at={b.updated_at}', None)
