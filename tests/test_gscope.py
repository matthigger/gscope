import json

import pytest

from gscope import AuthError, GradescopeError, Session, assignment, course, rubric, spec
from gscope.cli import main
from gscope.session import Page, parse_cookie_string

from fake_gs import CSRF, FakeGradescope, page


@pytest.fixture
def gs():
    return FakeGradescope()


@pytest.fixture
def s(gs):
    return Session(http=gs)


# ---- session ------------------------------------------------------------------

def test_parse_cookie_string():
    assert parse_cookie_string(' a=1; b=x=y;c=3 \n') == {'a': '1', 'b': 'x=y', 'c': '3'}


def test_from_cookie_sets_jar_for_host(gs):
    sess = Session.from_cookie('signed_token=abc; _gradescope_session=def', http=gs)
    assert sess.http.cookies.get('signed_token', domain='www.gradescope.com') == 'abc'


def test_page_props_csrf_title():
    p = Page('u', page('Grading', {'a': 1}, {'b': 2, 'c': [3]}))
    assert p.title == 'Grading | Gradescope'
    assert p.csrf == CSRF
    assert p.find_props('b', 'c') == {'b': 2, 'c': [3]}
    with pytest.raises(GradescopeError):
        p.find_props('zzz')


def test_expired_cookie_raises_auth_error():
    sess = Session(http=FakeGradescope(logged_in=False))
    with pytest.raises(AuthError):
        sess.page('/courses/11')


def test_writes_carry_csrf_from_a_fetched_page(s, gs):
    s.page('/courses/11')
    s.write('PATCH', '/courses/11/questions/31/rubric/update_entries',
            {'rubric_items': {}, 'rubric_item_groups': {}})
    method, path, body, headers = gs.writes()[0]
    assert headers['X-CSRF-Token'] == CSRF
    assert headers['Content-Type'] == 'application/json'


def test_cookie_resolution_order(tmp_path, monkeypatch, gs):
    f = tmp_path / 'c'
    f.write_text('k=fromfile')
    monkeypatch.setenv('GSCOPE_COOKIE', 'k=fromenv')
    assert Session.from_env(str(f), http=gs).http.cookies.get('k') == 'fromfile'
    gs2 = FakeGradescope()
    assert Session.from_env(None, http=gs2).http.cookies.get('k') == 'fromenv'
    monkeypatch.delenv('GSCOPE_COOKIE')
    monkeypatch.setenv('GSCOPE_COOKIE_FILE', str(tmp_path / 'missing'))
    with pytest.raises(AuthError):
        Session.from_env(None, http=FakeGradescope())


# ---- course / assignment ------------------------------------------------------------

def test_courses_and_assignments(s):
    cs = course.courses(s)
    assert [(c.id, c.shortname) for c in cs] == [(11, 'TEST101 Fall')]
    asg = course.assignments(s, 11)
    assert [(a.id, a.title, a.total_points) for a in asg] == [(21, 'quiz1a_q1', 20.0), (22, 'quiz1a_q2', 20.0)]


def test_roster(s):
    assert course.roster(s, 11) == [{'First Name': 'Ada', 'Last Name': 'Lovelace', 'SID': '001',
                                     'Email': 'ada@x.edu', 'Role': 'Student'}]


def test_load_assignment_tree_and_items(s, gs):
    gs.state['items'] += [
        {'id': 2, 'question_id': 33, 'description': 'second', 'weight': '3.0', 'position': 1},
        {'id': 1, 'question_id': 33, 'description': 'first', 'weight': '0.0', 'position': 0}]
    a = assignment.load(s, 11, 22)
    assert [q.id for q in a.walk()] == [32, 33, 34]
    assert [q.id for q in a.leaves()] == [33, 34]
    assert a.question(32).is_group and not a.question(33).is_group
    assert [i.description for i in a.question(33).items] == ['first', 'second']


# ---- rubric -----------------------------------------------------------------------

@pytest.mark.parametrize('points,scoring,weight', [
    (-8, 'negative', 8.0), (0, 'negative', 0.0), (2, 'negative', -2.0),
    (5, 'positive', 5.0), (-1, 'positive', -1.0)])
def test_points_to_weight(points, scoring, weight):
    w = rubric.points_to_weight(points, scoring)
    assert w == weight and str(w) != '-0.0'
    assert rubric.weight_to_points(w, scoring) == points


def _spec():
    return [rubric.SpecQuestion(21, 31, [rubric.SpecItem(0, 'Correct.'),
                                         rubric.SpecItem(-8, 'Denominator wrong.')], 'A Q1'),
            rubric.SpecQuestion(22, 33, [rubric.SpecItem(-2, 'Wrong side.')], 'A Q2.1'),
            rubric.SpecQuestion(22, 34, [rubric.SpecItem(1, 'Not unique.')], 'A Q2.2'),
            rubric.SpecQuestion(22, 32, [rubric.SpecItem(0, 'group item')], 'group')]


def test_plan_is_read_only_and_converts_signs(s, gs):
    plan = rubric.plan_push(s, 11, _spec())
    assert gs.writes() == []
    assert plan[0].create == [{'description': 'Correct.', 'weight': 0.0},
                              {'description': 'Denominator wrong.', 'weight': 8.0}]
    assert plan[1].create[0]['weight'] == 2.0          # negative scoring
    assert plan[2].create[0]['weight'] == 1.0          # positive scoring
    assert 'group' in plan[3].skip


def test_apply_creates_in_order_and_verifies(s, gs):
    plan = rubric.plan_push(s, 11, _spec())
    rubric.apply_push(s, 11, plan)
    posts = [c for c in gs.writes() if c[0] == 'POST']
    assert [c[1] for c in posts] == ['/courses/11/questions/31/rubric_items'] * 2 + \
        ['/courses/11/questions/33/rubric_items', '/courses/11/questions/34/rubric_items']
    assert posts[1][2] == {'rubric_item': {'description': 'Denominator wrong.', 'weight': 8.0}}
    q = assignment.load(s, 11, 21).question(31)
    assert [(i.description, i.weight) for i in q.items] == [('Correct.', 0.0), ('Denominator wrong.', 8.0)]


def test_existing_items_block_a_second_push(s, gs):
    rubric.apply_push(s, 11, rubric.plan_push(s, 11, _spec()))
    n = len(gs.writes())
    plan = rubric.plan_push(s, 11, _spec())
    assert all(e.skip for e in plan)
    rubric.apply_push(s, 11, plan)
    assert len(gs.writes()) == n


def test_update_and_delete(s, gs):
    it = rubric.create_item(s, 11, 31, 'x', -8.0)
    rubric.update_items(s, 11, 31, {it.id: {'weight': 8}})
    assert assignment.load(s, 11, 21).question(31).items[0].weight == 8.0
    rubric.delete_items(s, 11, 31, [it.id])
    assert assignment.load(s, 11, 21).question(31).items == []


# ---- spec ---------------------------------------------------------------------------

DOC = """# Quiz rubric

## Version A

### Q1: Bayes (20 pts)

- **0**: Correct.  P(y | K=3) = 0.384
- **−8**: Denominator missing or wrong.
- **+1**: Bonus for **clarity**.

### Q2.1: Perceptron (16 pts)

- **-2**: Wrong side.

## TA notes (not uploaded)

### Q1

- **-8**: not a rubric item, this section is not in the map
- Typical scores: 10
"""

QMAP = {'course_id': 11, 'A': {'Q1': [21, 31], 'Q2.1': [22, 33]}}


def test_parse_markdown():
    p = spec.parse_markdown(DOC)
    assert [(i.points, i.description) for i in p[('A', 'Q1')]] == [
        (0.0, 'Correct.  P(y | K=3) = 0.384'), (-8.0, 'Denominator missing or wrong.'),
        (1.0, 'Bonus for clarity.')]
    assert ('TA notes (not uploaded)', 'Q1') in p          # parsed, but unmapped


def test_from_markdown_uses_map_only():
    cid, sp = spec.from_markdown(DOC, QMAP)
    assert cid == 11 and [(q.label, q.question_id, len(q.items)) for q in sp] == [
        ('A Q1', 31, 3), ('A Q2.1', 33, 1)]


def test_from_markdown_missing_section_raises():
    with pytest.raises(ValueError, match='B/Q1'):
        spec.from_markdown(DOC, {**QMAP, 'B': {'Q1': [1, 2]}})


def test_json_round_trip():
    cid, sp = spec.from_markdown(DOC, QMAP)
    assert spec.from_json(json.loads(json.dumps(spec.to_json(cid, sp)))) == (cid, sp)


# ---- cli ----------------------------------------------------------------------------

@pytest.fixture
def cli_env(tmp_path, monkeypatch, gs):
    monkeypatch.setattr(Session, 'from_env', classmethod(lambda cls, f=None, **kw: Session(http=gs)))
    (tmp_path / 'r.md').write_text(DOC)
    (tmp_path / 'm.json').write_text(json.dumps(QMAP))
    return tmp_path


def test_cli_push_dry_run_writes_nothing(cli_env, gs, capsys):
    assert main(['rubric', 'push', str(cli_env / 'r.md'), '--map', str(cli_env / 'm.json')]) == 0
    assert gs.writes() == []
    out = capsys.readouterr().out
    assert 'dry run: would create 4 items on 2 questions' in out
    assert '-8 (weight 8)' in out


def test_cli_push_apply(cli_env, gs, capsys):
    assert main(['rubric', 'push', str(cli_env / 'r.md'), '--map', str(cli_env / 'm.json'), '--apply']) == 0
    assert len([c for c in gs.writes() if c[0] == 'POST']) == 4
    assert 'read back and verified' in capsys.readouterr().out
    main(['rubric', 'show', '11', '21'])
    assert '-8  Denominator missing or wrong.' in capsys.readouterr().out


def test_cli_reports_auth_errors(monkeypatch, capsys):
    monkeypatch.setattr(Session, 'from_env',
                        classmethod(lambda cls, f=None, **kw: Session(http=FakeGradescope(logged_in=False))))
    assert main(['courses']) == 1
    assert 'not logged in' in capsys.readouterr().err


def test_cli_missing_cookie_file_is_a_clean_error(capsys):
    assert main(['--cookie', '/nonexistent/cookie', 'courses']) == 1
    assert 'cannot read cookie file' in capsys.readouterr().err
