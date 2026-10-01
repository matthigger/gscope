# gscope

An unofficial, instructor-side Gradescope client for Python and the command
line.  It reads courses, assignments, outlines and rubrics.  It also sets up
scanned quizzes from one file: it creates the assignments, saves their outlines,
pushes rubric items written once in a document, and uploads the scans, so
graders can start without anyone clicking through each version.

    pip install gscope-cli

The package installs the `gscope` command and the `gscope` Python module.

> **Unofficial.**  Gradescope has no public API.  gscope makes the same requests
> Gradescope's own web pages make, using your logged-in session.  Gradescope
> can change those routes without notice, so every write is a dry run until
> you pass `--apply`, and is read back and checked afterwards.  Use it on
> courses you teach, at a human pace, and read Gradescope's terms of service.

## Logging in

Log in to Gradescope in your browser as usual, then:

    pip install "gscope-cli[browser]"
    gscope login                     # Brave; or --browser chrome|chromium|firefox

`gscope login` copies the session cookie out of the browser's profile (snap
and flatpak installs included), checks it, and saves it to
`~/.config/gscope/cookie`, readable only by you.  Every other command reads it
from there.  When it stops working, commands say so: open gradescope.com in the
browser and run `gscope login` again.  Set `$GSCOPE_BROWSER` to change the
default browser.

**Single sign-on courses.**  A course can be set to accept only school (SSO)
logins.  A browser session that Gradescope restored from "remember me" lists
such a course, but cannot open it.  `gscope login` checks your newest course
and says when this happens.  To fix it, log out of Gradescope in the browser,
log back in with School Credentials, and rerun `gscope login`.

Chrome and Brave encrypt cookies with a key in your desktop keyring, so the
first run may ask for permission.

**Without browser access**, `gscope login --paste` takes the Cookie header by hand:
1. Open DevTools (F12) on any gradescope.com page.
2. Go to the **Network** tab and reload the page.
3. Click the first `www.gradescope.com` request.
4. Copy the whole **Cookie** value under **Request Headers**, and paste it in.

gscope looks for the cookie in, in order:
- `--cookie FILE`
- `$GSCOPE_COOKIE`, holding the header itself
- `$GSCOPE_COOKIE_FILE`
- `~/.config/gscope/cookie`

Logging out of Gradescope in the browser ends that session, and the saved
cookie stops working.  Accounts with a Gradescope password can also use
`gscope.Session.login(email, password)` from Python.

## Command line

    gscope login [--browser B] [--paste]   # save the session cookie (see above)
    gscope courses                         # id and name of every course
    gscope assignments COURSE              # points, submissions, % graded
    gscope questions COURSE ASSIGNMENT     # question tree, points, scoring type
    gscope rubric show COURSE ASSIGNMENT   # rubric items, as signed points
    gscope rubric push SPEC [--map MAP] [--apply]
    gscope rubric to-json RUBRIC.md --map MAP [-o SPEC.json]
    gscope assignment create COURSE TITLE TEMPLATE.pdf [--apply]
    gscope assignment delete COURSE ASSIGNMENT --confirm TITLE --apply
    gscope outline show COURSE ASSIGNMENT
    gscope outline guess TEMPLATE.pdf --parts "i:7,ii:7,iii:6" [-o OUTLINE.json]
    gscope outline push COURSE ASSIGNMENT OUTLINE.json [--replace] [--apply]
    gscope scans list COURSE ASSIGNMENT
    gscope scans upload COURSE ASSIGNMENT SCAN.pdf... [--apply]
    gscope submissions COURSE ASSIGNMENT   # who is matched, who is not
    gscope setup SETUP.json [--steps create,outline,scoring,rubric,scans] [--apply]
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

### Setting up a scanned quiz

`gscope setup` takes one JSON file and does everything a scanned quiz needs
before grading starts: it creates an "Exam / Quiz" assignment per template,
saves each outline, sets the scoring type, pushes the rubric, uploads the scans,
and reports which submissions still need matching to a student.

```json
{"course_id": 123456,
 "scoring": "negative",
 "assignments": [
   {"title": "quiz2a_q1", "template": "templates/quiz2a_q1.pdf",
    "questions": [{"title": "Covariance Matching", "weight": 20}],
    "scans": ["scans/quiz2a_q1_*.pdf"]},
   {"title": "quiz2a_q2", "template": "templates/quiz2a_q2.pdf",
    "questions": [{"title": "Sample Covariance", "parts": [
        {"title": "i", "weight": 7}, {"title": "ii", "weight": 7}, {"title": "iii", "weight": 6}]}]}],
 "rubric": {"doc": "quiz2_rubric.md",
            "map": {"A": {"Q1": ["quiz2a_q1", "1"], "Q2.1": ["quiz2a_q2", "1.1"]}}}}
```

Paths are relative to the setup file.  Rubric map entries name an assignment by
title and a question by its number, so the map can be written before the
assignments exist.  Every step is skipped once it is done, so you can rerun the
same file as the quiz moves along: create the assignments now, push the rubric
once it is written, and upload the scans once they exist.  Without `--apply`, it only
reports what it would do.

Outlines are guessed from the template PDF (this needs `pdftotext` from
poppler).  The name and NUID boxes come from the "Name" and "NUID:" labels,
the question runs from its "Problem" header down, and each part runs from its
label (`i.`, `(a)`, `2.`) to the next one.  The last part also covers page 2, so
graders see work that continued on the back.  Check the guess with
`gscope outline guess ... -o outline.json` and put an explicit `"outline"` in
the setup file when it is wrong.

Gradescope splits uploaded scans by the template's page count, and matches
them to students from the name and SID boxes.  `gscope submissions` lists the
ones it could not match; fix those in Manage Submissions.

New questions start with a 0-point "Correct" item.  `rubric push` replaces it,
and the scoring step removes it from question groups (whose parts hold the
rubric).

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
| Creating and deleting assignments, outline editing, uploading scans, matching | done |
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
