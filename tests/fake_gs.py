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
    def __init__(self, url, status=200, text='', history=(), headers=None):
        self.url, self.status_code, self.text = url, status, text
        self.content = text.encode()
        self.history = list(history)
        self.headers = headers or {}

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
        'batches': [],          # {id, assignment, filename, pages}
        'submissions': [],      # {id, assignment, batch_id, user_ids}
        'roster_users': [{'id': 501, 'name': 'Ada Lovelace', 'email': 'ada@x.edu', 'sid': '001', 'role': 'student'}],
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
    def request(self, method, url, data=None, headers=None, files=None, **kw):
        path = urlparse(url).path
        body = json.loads(data) if isinstance(data, str) and data else data
        if files:
            body = {**(body or {}), **{k: (v[0], v[1].read()) for k, v in files.items()}}
        self.calls.append((method, path, body, headers or {}))
        if not self.logged_in and path not in ('/', '/login'):
            return Resp('https://www.gradescope.com/', 200, page('Gradescope'),
                        history=[Resp(url, 302)])
        form_token = body.get('authenticity_token') if isinstance(body, dict) else None
        if method != 'GET' and CSRF not in ((headers or {}).get('X-CSRF-Token'), form_token):
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
            (r'/courses/(\d+)/assignments', ('GET', self.assignments_page)),
            (r'/courses/(\d+)/assignments', ('POST', self.create_assignment)),
            (r'/courses/(\d+)/assignments/(\d+)', ('POST', self.delete_assignment)),
            (r'/courses/(\d+)/assignments/(\d+)/outline/', ('PATCH', self.save_outline)),
            (r'/courses/(\d+)/questions/(\d+)/save_grading_options', ('POST', self.grading_options)),
            (r'/courses/(\d+)/assignments/(\d+)/submission_batches', ('GET', self.batches)),
            (r'/courses/(\d+)/assignments/(\d+)/submission_batches', ('POST', self.upload)),
            (r'/courses/(\d+)/assignments/(\d+)/submission_batches/(\d+)', ('GET', self.one_batch)),
            (r'/courses/(\d+)/assignments/(\d+)/submissions', ('GET', self.submissions)),
            (r'/courses/(\d+)/assignments/(\d+)/submissions/(\d+)/identify', ('PATCH', self.identify)),
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
        a = self.state['assignments'][int(aid)]
        has = any(x['assignment'] == int(aid) for x in self.state['submissions'])
        return Resp(url, 200, page('Edit Outline', {
            'assignment': {'id': int(aid), 'id_regions': a.get('id_regions')},
            'outline': a['questions'], 'has_submissions_and_students': has}))

    def assignments_page(self, url, body, cid):
        return Resp(url, 200, page('Assignments'))

    def _new_id(self):
        self.state['next_id'] += 1
        return self.state['next_id'] - 1

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

    def create_assignment(self, url, body, cid):
        if not body.get('assignment[title]') or 'template_pdf' not in body:
            return Resp(url, 200, page('Create Assignment'))        # the form again
        aid = self._new_id()
        self.state['assignments'][aid] = {'course': int(cid), 'title': body['assignment[title]'],
                                          'questions': [], 'template': body['template_pdf'][0]}
        return Resp(url, 302, '', headers={'Location': f'https://www.gradescope.com/courses/{cid}/assignments/{aid}/outline/edit'})

    def delete_assignment(self, url, body, cid, aid):
        if body.get('_method') != 'delete':
            return Resp(url, 404)
        del self.state['assignments'][int(aid)]
        return Resp(url, 302, '', headers={'Location': f'/courses/{cid}/assignments'})

    def save_outline(self, url, body, cid, aid):
        a = self.state['assignments'][int(aid)]

        def build(q, parent):
            qid = q.get('id') or self._new_id()
            kids = q.get('children')
            out = {'id': qid, 'title': q['title'], 'weight': f"{float(q['weight']):.1f}",
                   'scoring_type': 'negative', 'parent_id': parent,
                   'type': 'QuestionGroup' if kids else 'FreeResponseQuestion',
                   'crop_rect_list': q['crop_rect_list'], 'children': None}
            if not q.get('id'):         # Gradescope gives every new question a "Correct" item
                self.state['items'].append({'id': self._new_id(), 'question_id': qid, 'description': 'Correct',
                                            'weight': '0.0', 'position': 0, 'group_id': None})
            if kids:
                out['children'] = [build(c, qid) for c in kids]
            return out
        a['questions'] = [build(q, None) for q in body['question_data']]
        a['id_regions'] = body['assignment']['identification_regions']
        return Resp(url, 200, json.dumps({'path': f'/courses/{cid}/assignments/{aid}/outline/edit'}))

    def _walk(self, qs):
        for q in qs:
            yield q
            yield from self._walk(q.get('children') or [])

    def grading_options(self, url, body, cid, qid):
        for a in self.state['assignments'].values():
            for q in self._walk(a['questions']):
                if q['id'] == int(qid):
                    q['scoring_type'] = body['question']['scoring_type']
        return Resp(url, 200, '{}')

    def _batch_json(self, b):
        n = sum(1 for x in self.state['submissions'] if x['batch_id'] == b['id'])
        return {'id': b['id'], 'page_count': 4, 'complete_status': 'ready',
                'status': 'was_automatically_split', 'num_active_submissions': n,
                'updated_at': '2026-10-01T10:00:00.000-04:00',
                'pdf_attachment': {'url': f"https://s3.example/uploads/{b['filename']}?X-Amz=1"}}

    def batches(self, url, body, cid, aid):
        bs = [self._batch_json(b) for b in self.state['batches'] if b['assignment'] == int(aid)]
        return Resp(url, 200, json.dumps({'batches': bs, 'roster': self.state['roster_users']}))

    def one_batch(self, url, body, cid, aid, bid):
        b = next(b for b in self.state['batches'] if b['id'] == int(bid))
        return Resp(url, 200, json.dumps(self._batch_json(b)))

    def upload(self, url, body, cid, aid):
        b = {'id': self._new_id(), 'assignment': int(aid), 'filename': body['file'][0]}
        self.state['batches'].append(b)
        for _ in range(2):              # every fake scan splits into two sheets
            self.state['submissions'].append({'id': self._new_id(), 'assignment': int(aid),
                                              'batch_id': b['id'], 'user_ids': []})
        return Resp(url, 200, json.dumps(self._batch_json(b)))

    def submissions(self, url, body, cid, aid):
        page_no = int((urlparse(url).query.split('page=') + ['1'])[1].split('&')[0])
        subs = [x for x in self.state['submissions'] if x['assignment'] == int(aid)] if page_no == 1 else []
        det = {str(x['id']): {'id': x['id'], 'batch_id': x['batch_id'], 'active_user_ids': x['user_ids'],
                              'ownership_created_automatically': False, 'graded': False,
                              'grading_progress': 0.0} for x in subs}
        return Resp(url, 200, json.dumps({'submissions': list(det.values()), 'detailed_submissions': det}))

    def identify(self, url, body, cid, aid, sid):
        for x in self.state['submissions']:
            if x['id'] == int(sid):
                x['user_ids'] = [int(body['assignment_submission[user_id]'])]
        return Resp(url, 200, '{}')

    def writes(self):
        return [c for c in self.calls if c[0] != 'GET']
