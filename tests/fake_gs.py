"""An in-memory stand-in for the slice of Gradescope gscope talks to.

Pages are rendered from a small state dict the same way Gradescope ships them:
JSON in data-react-props attributes plus a csrf-token meta tag.  Writes mutate
the state, so a test can push a rubric and read it back.  All data is made up.
"""
import html
import json
import re
from urllib.parse import urlparse

import requests

CSRF = 'test-csrf-token'


def props_attr(d):
    return f'<div data-react-props="{html.escape(json.dumps(d), quote=True)}"></div>'


def page(title, *blobs):
    return (f'<html><head><title>{title} | Gradescope</title>'
            f'<meta name="csrf-token" content="{CSRF}"></head><body>'
            + ''.join(props_attr(b) for b in blobs) + '</body></html>')


class Resp:
    def __init__(self, url, status=200, text='', history=()):
        self.url, self.status_code, self.text = url, status, text
        self.content = text.encode()
        self.history = list(history)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


def default_state():
    return {
        'courses': {11: {'shortname': 'TEST101 Fall', 'name': 'Test Course 101'}},
        'assignments': {
            21: {'course': 11, 'title': 'quiz1a_q1', 'questions': [
                {'id': 31, 'title': 'Bayes', 'weight': '20.0', 'scoring_type': 'negative',
                 'parent_id': None, 'type': 'FreeResponseQuestion', 'children': None}]},
            22: {'course': 11, 'title': 'quiz1a_q2', 'questions': [
                {'id': 32, 'title': 'Perceptron', 'weight': '20.0', 'scoring_type': 'negative',
                 'parent_id': None, 'type': 'QuestionGroup', 'children': [
                     {'id': 33, 'title': '2.1', 'weight': '16.0', 'scoring_type': 'negative',
                      'parent_id': 32, 'type': 'FreeResponseQuestion', 'children': None},
                     {'id': 34, 'title': '2.2', 'weight': '4.0', 'scoring_type': 'positive',
                      'parent_id': 32, 'type': 'FreeResponseQuestion', 'children': None}]}]},
        },
        'items': [],            # rubric item dicts, as Gradescope returns them
        'next_id': 1000,
        'roster_csv': 'First Name,Last Name,SID,Email,Role\nAda,Lovelace,001,ada@x.edu,Student\n',
    }


class FakeGradescope:
    """Quacks like requests.Session for Session.request()."""

    def __init__(self, state=None, logged_in=True):
        self.state = state or default_state()
        self.logged_in = logged_in
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.calls = []                     # (method, path, body or None, headers)

    # -- routing ---------------------------------------------------------------
    def request(self, method, url, data=None, headers=None, **kw):
        path = urlparse(url).path
        body = json.loads(data) if isinstance(data, str) and data else data
        self.calls.append((method, path, body, headers or {}))
        if not self.logged_in and path not in ('/', '/login'):
            return Resp('https://www.gradescope.com/', 200, page('Gradescope'),
                        history=[Resp(url, 302)])
        if method != 'GET' and (headers or {}).get('X-CSRF-Token') != CSRF:
            return Resp(url, 422, '{"error":"bad csrf"}')
        for pattern, handler in self.routes():
            m = re.fullmatch(pattern, path)
            if m and handler[0] == method:
                return handler[1](url, body, *m.groups())
        return Resp(url, 404, '{"error":"Not Found"}')

    def routes(self):
        return [
            (r'/account', ('GET', self.account)),
            (r'/courses/(\d+)', ('GET', self.course_page)),
            (r'/courses/(\d+)/memberships\.csv', ('GET', self.roster)),
            (r'/courses/(\d+)/assignments/(\d+)/rubric/edit', ('GET', self.rubric_page)),
            (r'/courses/(\d+)/assignments/(\d+)/outline/edit', ('GET', self.outline_page)),
            (r'/courses/(\d+)/questions/(\d+)/rubric_items', ('POST', self.create_item)),
            (r'/courses/(\d+)/questions/(\d+)/rubric/update_entries', ('PATCH', self.update)),
            (r'/courses/(\d+)/questions/(\d+)/rubric/delete_entries', ('DELETE', self.delete)),
        ]

    # -- pages -----------------------------------------------------------------
    def account(self, url, body):
        boxes = ''.join(
            f'<a class="courseBox" data-loginrequired="false" href="/courses/{cid}">'
            f'<h3 class="courseBox--shortname" title="x">{c["shortname"]}</h3>'
            f'<div class="courseBox--name" title="x">{c["name"]}</div></a>'
            for cid, c in self.state['courses'].items())
        return Resp(url, 200, page('Your Courses').replace('</body>', boxes + '</body>'))

    def course_page(self, url, body, cid):
        rows = [{'type': 'assignment', 'id': f'assignment_{aid}', 'title': a['title'],
                 'url': f'/courses/{cid}/assignments/{aid}', 'total_points': '20.0',
                 'num_active_submissions': 7, 'grading_progress': 0.0, 'is_published': False}
                for aid, a in self.state['assignments'].items() if a['course'] == int(cid)]
        return Resp(url, 200, page('Dashboard', {'table_data': rows, 'course_dashboard': True}))

    def roster(self, url, body, cid):
        return Resp(url, 200, self.state['roster_csv'])

    def _qids(self, aid):
        out, todo = [], list(self.state['assignments'][aid]['questions'])
        while todo:
            q = todo.pop()
            out.append(q['id'])
            todo += q.get('children') or []
        return out

    def rubric_page(self, url, body, cid, aid):
        a = self.state['assignments'][int(aid)]
        qids = self._qids(int(aid))
        items = [i for i in self.state['items'] if i['question_id'] in qids]
        return Resp(url, 200, page('Create Rubric', {
            'assignment': {'id': int(aid), 'title': a['title']},
            'questions': a['questions'], 'rubric_items': items, 'rubric_item_groups': []}))

    def outline_page(self, url, body, cid, aid):
        return Resp(url, 200, page('Edit Outline', {'outline': self.state['assignments'][int(aid)]['questions']}))

    # -- writes ----------------------------------------------------------------
    def create_item(self, url, body, cid, qid):
        it = body['rubric_item']
        pos = sum(1 for i in self.state['items'] if i['question_id'] == int(qid))
        new = {'id': self.state['next_id'], 'question_id': int(qid),
               'description': it['description'], 'weight': f"{float(it['weight']):.1f}",
               'position': pos, 'group_id': it.get('group_id')}
        self.state['next_id'] += 1
        self.state['items'].append(new)
        return Resp(url, 200, json.dumps({k: v for k, v in new.items() if k != 'question_id'}))

    def update(self, url, body, cid, qid):
        for iid, fields in body['rubric_items'].items():
            for i in self.state['items']:
                if i['id'] == int(iid):
                    i.update({k: (f'{float(v):.1f}' if k == 'weight' else v) for k, v in fields.items()})
        return Resp(url, 200, '{}')

    def delete(self, url, body, cid, qid):
        ids = set(body['rubric_item_ids'])
        self.state['items'] = [i for i in self.state['items'] if i['id'] not in ids]
        return Resp(url, 204, '')

    def writes(self):
        return [c for c in self.calls if c[0] != 'GET']
