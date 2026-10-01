"""Rubric specs: what to push, read from JSON or from a rubric markdown document.

JSON::

    {"course_id": 123,
     "questions": [
       {"assignment_id": 456, "question_id": 789, "label": "A Q1",
        "items": [{"points": 0, "description": "Correct."},
                  {"points": -8, "description": "Denominator missing or wrong."}]}]}

Markdown: ``## <group>`` headings (a leading "Version " is dropped), ``### <question>``
headings under them, and rubric items as list lines ``- **<points>**: <text>``::

    ## Version A
    ### Q1 Bayes rule (20 pts)
    - **0**: Correct.
    - **-8**: Denominator missing or wrong.

The question key is the heading's first word ("Q1").  A map file ties the keys
to Gradescope ids: ``{"course_id": 123, "A": {"Q1": [456, 789]}}``, the pair being
[assignment id, question id].  Sections not in the map (TA notes, ...) are ignored.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .rubric import SpecItem, SpecQuestion

_ITEM = re.compile(r'^\s*[-*] \*\*([+\-−–]?\d+(?:\.\d+)?)\*\*\s*:\s*(.*\S)\s*$')


def _num(text: str) -> float:
    return float(text.replace('−', '-').replace('–', '-'))


def parse_markdown(md: str) -> dict[tuple[str, str], list[SpecItem]]:
    """{(group, question key): items} for every rubric item line in the document."""
    out: dict[tuple[str, str], list[SpecItem]] = {}
    group = key = None
    for line in md.splitlines():
        if line.startswith('## '):
            group = re.sub(r'^Version\s+', '', line[3:].strip())
            key = None
        elif line.startswith('### ') and group is not None:
            key = line[4:].split()[0].rstrip(':') if line[4:].strip() else None
        elif group is not None and key is not None and (m := _ITEM.match(line)):
            text = m.group(2).replace('**', '').strip()
            out.setdefault((group, key), []).append(SpecItem(_num(m.group(1)), text))
    return out


def from_markdown(md: str, qmap: dict) -> tuple[int, list[SpecQuestion]]:
    """(course id, spec) from a rubric document plus its id map."""
    parsed = parse_markdown(md)
    spec, missing = [], []
    for group, qs in qmap.items():
        if group == 'course_id':
            continue
        for key, (aid, qid) in qs.items():
            items = parsed.get((group, key))
            if not items:
                missing.append(f'{group}/{key}')
                continue
            spec.append(SpecQuestion(int(aid), int(qid), items, label=f'{group} {key}'))
    if missing:
        raise ValueError(f'no rubric items in the document for: {", ".join(missing)}')
    return int(qmap['course_id']), spec


def from_json(d: dict) -> tuple[int, list[SpecQuestion]]:
    spec = [SpecQuestion(int(q['assignment_id']), int(q['question_id']),
                         [SpecItem(float(i['points']), i['description']) for i in q['items']],
                         label=q.get('label', ''))
            for q in d['questions']]
    return int(d['course_id']), spec


def to_json(course_id: int, spec: list[SpecQuestion]) -> dict:
    return {'course_id': course_id,
            'questions': [{'assignment_id': q.assignment_id, 'question_id': q.question_id,
                           'label': q.label,
                           'items': [{'points': i.points, 'description': i.description}
                                     for i in q.items]} for q in spec]}


def load(path, qmap_path=None) -> tuple[int, list[SpecQuestion]]:
    """A .json spec, or a .md document (which needs ``qmap_path``)."""
    path = Path(path)
    if path.suffix.lower() in ('.md', '.markdown'):
        if not qmap_path:
            raise ValueError('a markdown rubric needs a map file (--map)')
        return from_markdown(path.read_text(), json.loads(Path(qmap_path).read_text()))
    return from_json(json.loads(path.read_text()))
