"""Assignments, outlines, scans, submissions and the setup runner, against the fake."""
import json

import pytest

from gscope import GradescopeError, Session, assignment, outline, scans, submissions
from gscope import setup as setup_mod
from gscope.cli import main
from gscope.outline import Outline, OutlineQuestion, Rect, _Word

from fake_gs import FakeGradescope


@pytest.fixture
def gs():
    return FakeGradescope()


@pytest.fixture
def s(gs):
    return Session(http=gs)


def two_part():
    return Outline([OutlineQuestion('Perceptron', 0, [], [
        OutlineQuestion('i', 12, [Rect(0, 100, 12.5, 63.7, 1)]),
        OutlineQuestion('ii', 8, [Rect(0, 100, 63.7, 100, 1), Rect(0, 100, 0, 100, 2)])])],
        name=Rect(7.3, 64.4, 5.5, 13.8, 1), sid=Rect(64.9, 95, 5.5, 13.8, 1))


# ---- outline payloads -------------------------------------------------------------

def test_payload_group_weight_and_regions_from_parts():
    q = two_part().payload()['question_data'][0]
    assert q['weight'] == 20
    assert q['crop_rect_list'] == [{'x1': 0.0, 'x2': 100.0, 'y1': 12.5, 'y2': 100.0, 'page_number': 1},
                                   {'x1': 0.0, 'x2': 100.0, 'y1': 0.0, 'y2': 100.0, 'page_number': 2}]
    assert [c['title'] for c in q['children']] == ['i', 'ii'] and 'id' not in q


def test_rect_clamps_and_rounds():
    assert Rect(-3, 100.04, 12.345, 120, 2).to_json() == \
        {'x1': 0.0, 'x2': 100.0, 'y1': 12.3, 'y2': 100.0, 'page_number': 2}


@pytest.mark.parametrize('bad, msg', [
    (Outline([OutlineQuestion('q', 5, [Rect(0, 1, 0, 1)])], Rect(0, 1, 0, 1, 1), Rect(0, 1, 0, 1, 2)), 'same page'),
    (Outline([OutlineQuestion('q', 0, [], [OutlineQuestion('a', 1, [], [OutlineQuestion('x', 1, [Rect(0, 1, 0, 1)])])])]), 'one level'),
    (Outline([OutlineQuestion('q', 5)]), 'no page region')])
def test_payload_rejects(bad, msg):
    with pytest.raises(ValueError, match=msg):
        bad.payload()


def test_outline_json_round_trip():
    o = two_part()
    assert Outline.from_json(json.loads(json.dumps(o.to_json()))).payload() == o.payload()


# ---- guessing from template words -------------------------------------------------

def fake_template(monkeypatch, pages=2):
    w = lambda t, x, y, p=1: _Word(t, x, y, x + 3, y + 1.5, p)
    words = [w('Name', 8.8, 8.5), w('NUID:', 65.4, 8.5), w('Problem', 10, 13.5),
             w('i.', 10, 20), w('ii.', 10, 40), w('iii.', 10, 64.2),
             w('(a)', 14, 70), w('(b)', 14, 80), w('3.', 50, 30)]
    monkeypatch.setattr(outline, 'template_words', lambda pdf: (words, pages))


def test_guess_parts_and_back_page(monkeypatch):
    fake_template(monkeypatch)
    o = outline.guess('t.pdf', [{'title': 'Sample Covariance', 'parts': [
        {'title': 'i', 'weight': 7}, {'title': 'ii', 'weight': 7}, {'title': 'iii', 'weight': 6}]}])
    parts = o.questions[0].parts
    assert [(p.regions[0].y1, p.regions[0].y2) for p in parts] == [(12.5, 39), (39, 63.2), (63.2, 100)]
    assert [len(p.regions) for p in parts] == [1, 1, 2] and parts[-1].regions[1].page == 2
    assert o.name.page == o.sid.page == 1 and o.name.x2 < o.sid.x1
    assert o.total() == 20


def test_guess_single_question_no_back(monkeypatch):
    fake_template(monkeypatch, pages=1)
    o = outline.guess('t.pdf', [{'title': 'Covariance Matching', 'weight': 20}])
    assert [r.to_json()['y1'] for r in o.questions[0].regions] == [12.5]


def test_guess_wrong_part_count_says_so(monkeypatch):
    fake_template(monkeypatch)
    with pytest.raises(GradescopeError, match='4 part labels'):
        outline.guess('t.pdf', [{'title': 'q', 'parts': [{'title': str(k), 'weight': 1} for k in range(4)]}])


# ---- assignments and outline push ---------------------------------------------------

def test_create_push_outline_and_delete(s, gs, tmp_path):
    pdf = tmp_path / 'quiz.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    aid = assignment.create_exam(s, 11, 'quiz2a_q2', pdf)
    assert gs.state['assignments'][aid]['title'] == 'quiz2a_q2'
    form = gs.writes()[0][2]
    assert form['assignment[student_submission]'] == 'false' and form['template_pdf'][0] == 'quiz.pdf'

    outline.push(s, 11, aid, two_part())
    live = assignment.load(s, 11, aid)
    assert [(q.title, q.weight) for q in live.walk()] == [('Perceptron', 20), ('i', 12), ('ii', 8)]
    assert outline.current(s, 11, aid)['assignment']['id_regions']['sid']['x1'] == 64.9

    assignment.delete(s, 11, aid)
    assert aid not in gs.state['assignments']


def test_push_refuses_to_drop_questions(s):
    small = Outline([OutlineQuestion('Bayes2', 20, [Rect(0, 100, 10, 100)])])
    with pytest.raises(GradescopeError, match='replace'):
        outline.push(s, 11, 21, small)
    outline.push(s, 11, 21, small, replace=True)
    assert [q.title for q in assignment.load(s, 11, 21).questions] == ['Bayes2']


def test_push_keeps_questions_given_by_id(s):
    keep = Outline([OutlineQuestion('Bayes, renamed', 20, [Rect(0, 100, 10, 100)], id=31)])
    outline.push(s, 11, 21, keep)
    assert assignment.load(s, 11, 21).questions[0].id == 31


def test_push_never_drops_questions_once_scans_exist(s, gs, tmp_path):
    f = tmp_path / 'scan.pdf'
    f.write_bytes(b'%PDF')
    scans.upload(s, 11, 21, f)
    with pytest.raises(GradescopeError, match='has submissions'):
        outline.push(s, 11, 21, Outline([OutlineQuestion('x', 1, [Rect(0, 1, 0, 1)])]), replace=True)


def test_grading_options(s, gs):
    assignment.set_grading_options(s, 11, 34, 'negative')
    assert assignment.load(s, 11, 22).question(34).scoring_type == 'negative'
    with pytest.raises(ValueError):
        assignment.set_grading_options(s, 11, 34, 'sideways')


# ---- scans and submissions ------------------------------------------------------------

def test_upload_wait_identify(s, gs, tmp_path):
    f = tmp_path / 'a_01.pdf'
    f.write_bytes(b'%PDF')
    b = scans.upload(s, 11, 21, f)
    assert gs.writes()[-1][2]['file'] == ('a_01.pdf', b'%PDF')
    done = scans.wait(s, 11, 21, [b.id], poll=0)
    assert done[0].done and not done[0].needs_manual_split and done[0].num_submissions == 2
    assert [x.filename for x in scans.batches(s, 11, 21)] == ['a_01.pdf']

    subs = submissions.list_all(s, 11, 21)
    assert len(subs) == 2 and not any(x.matched for x in subs)
    uid = submissions.roster_users(s, 11, 21)[0]['id']
    submissions.identify(s, 11, 21, subs[0].id, uid)
    assert submissions.list_all(s, 11, 21)[0].user_ids == [uid]


# ---- setup runner ------------------------------------------------------------------------

def write_setup(tmp_path):
    (tmp_path / 'scans').mkdir()
    (tmp_path / 'scans' / 'a_01.pdf').write_bytes(b'%PDF')
    (tmp_path / 't.pdf').write_bytes(b'%PDF template')
    (tmp_path / 'rubric.md').write_text(
        '## Version A\n### Q2.1: first part\n- **0**: Correct.\n- **-6**: Half wrong.\n'
        '### Q2.2: second\n- **0**: Correct.\n- **-8**: No explanation.\n'
        '## TA notes\n- **-1**: ignored.\n')
    d = {'course_id': 11, 'scoring': 'negative',
         'assignments': [{'title': 'quiz2a_q2', 'template': 't.pdf', 'scans': ['scans/*.pdf'],
                          'questions': [{'title': 'Sample Covariance', 'parts': [
                              {'title': 'i', 'weight': 7}, {'title': 'ii', 'weight': 7},
                              {'title': 'iii', 'weight': 6}]}]}],
         'rubric': {'doc': 'rubric.md',
                    'map': {'A': {'Q2.1': ['quiz2a_q2', '1.1'], 'Q2.2': ['quiz2a_q2', '1.2']}}}}
    p = tmp_path / 'setup.json'
    p.write_text(json.dumps(d))
    return p


def test_setup_dry_run_writes_nothing(s, gs, tmp_path, monkeypatch):
    fake_template(monkeypatch)
    out = []
    setup_mod.run(s, setup_mod.QuizSetup.load(write_setup(tmp_path)), apply=False, say=out.append)
    assert gs.writes() == []
    assert any('would create it from t.pdf' in x for x in out)
    assert any('rubric: not yet' in x for x in out)


def test_setup_apply_end_to_end_and_rerun(s, gs, tmp_path, monkeypatch):
    fake_template(monkeypatch)
    qs = setup_mod.QuizSetup.load(write_setup(tmp_path))
    out = []
    setup_mod.run(s, qs, apply=True, say=out.append)
    aid = next(k for k, a in gs.state['assignments'].items() if a['title'] == 'quiz2a_q2')
    live = assignment.load(s, 11, aid)
    group, i, ii, iii = live.walk()
    assert group.items == []                                # default item removed from the group
    assert [(x.description, x.weight) for x in i.items] == [('Correct.', 0), ('Half wrong.', 6)]
    assert [(x.description, x.weight) for x in ii.items] == [('Correct.', 0), ('No explanation.', 8)]
    assert [x.description for x in iii.items] == ['Correct']    # not in the map: left alone
    assert len(submissions.list_all(s, 11, aid)) == 2
    assert any('2 submissions, 0 matched' in x for x in out)

    n = len(gs.writes())
    out = []
    setup_mod.run(s, qs, apply=True, say=out.append)        # everything is done: no writes
    assert len(gs.writes()) == n
    assert any('outline: has questions already; matches' in x for x in out)
    assert any('1 uploaded already' in x for x in out)


def test_cli_assignment_delete_needs_matching_title(s, gs, monkeypatch, capsys):
    monkeypatch.setattr(Session, 'from_env', classmethod(lambda cls, *a, **k: s))
    assert main(['assignment', 'delete', '11', '21', '--confirm', 'wrong', '--apply']) != 0
    assert 21 in gs.state['assignments']
    assert main(['assignment', 'delete', '11', '21', '--confirm', 'quiz1a_q1', '--apply']) == 0
    assert 21 not in gs.state['assignments']


def test_setup_before_rubric_and_scans_exist(s, gs, tmp_path, monkeypatch):
    fake_template(monkeypatch)
    p = write_setup(tmp_path)
    (tmp_path / 'rubric.md').unlink()
    (tmp_path / 'scans' / 'a_01.pdf').unlink()
    out = []
    setup_mod.run(s, setup_mod.QuizSetup.load(p), apply=True, say=out.append)
    assert any('scans: none yet' in x for x in out)
    assert any('does not exist' in x for x in out)
