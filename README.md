# gscope

An unofficial, instructor-side Gradescope client for Python and the command
line.  It reads courses, assignments, outlines and rubrics, and pushes rubric
items from a spec file, so a rubric written once in a document goes up to every
version of a quiz without retyping.

    pip install gscope-cli

The package installs the `gscope` command and the `gscope` Python module.

> **Unofficial.**  Gradescope has no public API.  gscope makes the same requests
> Gradescope's own web pages make, using your logged-in session.  Gradescope
> can change those routes without notice, so every write is a dry run until
> you pass `--apply`, and is read back and checked afterwards.  Use it on
> courses you teach, at a human pace, and read Gradescope's terms of service.

## Logging in

gscope uses the session cookie from a browser where you are already logged
in.  That works with single sign-on (school) accounts, which have no
Gradescope password.

1. Open any gradescope.com page while logged in, and open DevTools (F12).
2. Go to the **Network** tab and reload the page.
3. Click the first `www.gradescope.com` request.  Under **Request Headers**,
   copy the whole value of **Cookie**.
4. Save it to `~/.config/gscope/cookie`, then `chmod 600` that file.

gscope looks for the cookie in, in order:
- `--cookie FILE`
- `$GSCOPE_COOKIE`, holding the header itself
- `$GSCOPE_COOKIE_FILE`
- `~/.config/gscope/cookie`

Logging out of Gradescope in the browser ends the session, and the cookie
stops working.

Accounts with a Gradescope password can also use
`gscope.Session.login(email, password)` from Python.

## Command line

    gscope courses                         # id and name of every course
    gscope assignments COURSE              # points, submissions, % graded
    gscope questions COURSE ASSIGNMENT     # question tree, points, scoring type
    gscope rubric show COURSE ASSIGNMENT   # rubric items, as signed points
    gscope rubric push SPEC [--map MAP] [--apply]
    gscope rubric to-json RUBRIC.md --map MAP [-o SPEC.json]
    gscope roster COURSE [-o roster.csv]
    gscope scores COURSE ASSIGNMENT [-o scores.csv]

The ids are the numbers in Gradescope URLs: `/courses/COURSE/assignments/ASSIGNMENT`.
`gscope questions` lists the question ids.

### Pushing a rubric

`rubric push` creates the items on each question, in order.  It refuses any
question that already has rubric items, or that is a group of parts, so it
cannot make duplicates.  Without `--apply` it only prints what it would do.

A spec is JSON:

```json
{"course_id": 123,
 "questions": [
   {"assignment_id": 456, "question_id": 789, "label": "A Q1",
    "items": [{"points": 0,  "description": "Correct."},
              {"points": -8, "description": "Denominator missing or wrong."}]}]}
```

Or it is a markdown rubric document plus a small map.  In the document, `##`
headings group the questions (a leading "Version " is dropped), and `###`
headings name them by their first word.  Rubric items are list lines with
the points in bold:

```markdown
## Version A
### Q1: Bayes rule (20 pts)
- **0**: Correct.
- **-8**: Denominator missing or wrong.
```

The map says where each section goes, as `[assignment id, question id]`:

```json
{"course_id": 123, "A": {"Q1": [456, 789]}, "B": {"Q1": [457, 790]}}
```

Sections the map doesn't mention, such as TA notes, are ignored.

### Signs

Specs write points the way a rubric reads: `-8` takes off 8 and `+2` adds 2.
Gradescope stores a weight whose meaning depends on the question's scoring
type.  Under negative scoring, a deduction is a **positive** weight, and a
negative weight adds points.  gscope converts using each question's scoring
type, and `rubric show` converts back.

## Python

```python
from gscope import Session, assignment, rubric

s = Session.from_cookie_file('~/.config/gscope/cookie')
a = assignment.load(s, course_id, assignment_id)
for q in a.walk():
    print(q.id, q.title, q.weight, q.scoring_type, len(q.items))

item = rubric.create_item(s, course_id, question_id, 'Wrong sign', weight=2)
rubric.update_items(s, course_id, question_id, {item.id: {'weight': 3}})
rubric.delete_items(s, course_id, question_id, [item.id])
```

## Status

| Area | State |
|---|---|
| Courses, assignments, roster, scores | done |
| Question tree, outline (read) | done |
| Rubric items: create, update, delete, push spec | done |
| Outline editing, creating assignments, uploading scans | planned |
| Applying grades from a reviewed file | planned |
| `gscope-cli[pset]`: outlines and rubrics from [pset](https://github.com/matthigger/pset) LaTeX problems | reserved, not built |

## Related work

- [nyuoss/gradescope-api](https://github.com/nyuoss/gradescope-api)
  (`gradescopeapi` on PyPI) covers courses, assignments, extensions, due
  dates and submission upload.  gscope's password login follows its form
  fields.
- [Yuanpeng-Li/gradescope-mcp](https://github.com/Yuanpeng-Li/gradescope-mcp)
  is an MCP server for AI assistants, with rubric and grading tools.  Its
  notes on the negative-scoring sign rule match what gscope found.

gscope differs from these by its cookie login (no password needed), the
rubric push with read-back checks, and the planned course-setup features.

## Development

    pip install -e '.[test]'
    pytest

The tests run against an in-memory fake of the Gradescope pages and routes,
in `tests/fake_gs.py`.  They need no network access and contain no real
course data.

MIT licensed.  Not affiliated with Gradescope or Turnitin.
