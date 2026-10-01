"""Authenticated access to Gradescope's web app.

Gradescope has no public API.  Instructor pages embed their state as JSON in
``data-react-props`` attributes, and the page's JavaScript writes back through
JSON routes guarded by a Rails CSRF token.  This module reads those pages and
makes those same requests.
"""
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

BASE_URL = 'https://www.gradescope.com'
USER_AGENT = 'Mozilla/5.0 (gscope; +https://github.com/matthigger/gscope)'
DEFAULT_COOKIE_FILE = Path('~/.config/gscope/cookie').expanduser()


class AuthError(RuntimeError):
    """The session is missing, expired, or lacks access to the page."""


class GradescopeError(RuntimeError):
    """A request Gradescope rejected, or a page that did not parse."""


_PROPS = re.compile(r'data-react-props="([^"]*)"')
_CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
_TITLE = re.compile(r'<title>([^<]*)</title>')


@dataclass
class Page:
    """One fetched HTML page."""
    url: str
    html: str
    _props: Optional[list] = field(default=None, repr=False)

    @property
    def title(self) -> str:
        m = _TITLE.search(self.html)
        return html.unescape(m.group(1)).strip() if m else ''

    @property
    def csrf(self) -> Optional[str]:
        m = _CSRF.search(self.html)
        return m.group(1) if m else None

    @property
    def props(self) -> list[dict]:
        """Every data-react-props blob on the page that parses as a JSON object."""
        if self._props is None:
            self._props = []
            for m in _PROPS.finditer(self.html):
                try:
                    d = json.loads(html.unescape(m.group(1)))
                except ValueError:
                    continue
                if isinstance(d, dict):
                    self._props.append(d)
        return self._props

    def find_props(self, *keys: str) -> dict:
        """The first props blob holding all of ``keys``."""
        for d in self.props:
            if all(k in d for k in keys):
                return d
        raise GradescopeError(f'{self.url}: no page data with keys {keys}')


def parse_cookie_string(text: str) -> dict[str, str]:
    """``name=value; name2=value2`` (a browser Cookie request header) to a dict."""
    out = {}
    for part in text.strip().split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out


class Session:
    """A logged-in Gradescope session.

    Make one with :meth:`from_cookie` (the Cookie header copied from a logged-in
    browser, which works with single sign-on accounts) or :meth:`login` (a
    Gradescope email and password).
    """

    def __init__(self, http: Optional[requests.Session] = None, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip('/')
        self.http = http if http is not None else requests.Session()
        if hasattr(self.http, 'headers'):
            self.http.headers['User-Agent'] = USER_AGENT
        self._csrf: Optional[str] = None

    # ---- construction -------------------------------------------------------
    @classmethod
    def from_cookie(cls, cookie: str, **kw) -> 'Session':
        s = cls(**kw)
        host = urlparse(s.base_url).hostname
        for k, v in parse_cookie_string(cookie).items():
            s.http.cookies.set(k, v, domain=host)
        return s

    @classmethod
    def from_cookie_file(cls, path, **kw) -> 'Session':
        p = Path(path).expanduser()
        try:
            text = p.read_text()
        except OSError as e:
            raise AuthError(f'cannot read cookie file {p}: {e.strerror}') from None
        return cls.from_cookie(text, **kw)

    @classmethod
    def from_env(cls, cookie_file=None, **kw) -> 'Session':
        """Cookie from, in order: ``cookie_file``, $GSCOPE_COOKIE (the header itself),
        $GSCOPE_COOKIE_FILE, then ~/.config/gscope/cookie."""
        if cookie_file:
            return cls.from_cookie_file(cookie_file, **kw)
        if os.environ.get('GSCOPE_COOKIE'):
            return cls.from_cookie(os.environ['GSCOPE_COOKIE'], **kw)
        path = os.environ.get('GSCOPE_COOKIE_FILE') or DEFAULT_COOKIE_FILE
        if Path(path).expanduser().exists():
            return cls.from_cookie_file(path, **kw)
        raise AuthError('no Gradescope cookie: pass --cookie FILE, set $GSCOPE_COOKIE or '
                        f'$GSCOPE_COOKIE_FILE, or save it to {DEFAULT_COOKIE_FILE}')

    @classmethod
    def login(cls, email: str, password: str, **kw) -> 'Session':
        """Password login, through the Rails form on the home page (field names as in
        nyuoss/gradescope-api).  Single sign-on accounts need a Gradescope password set
        first, or use :meth:`from_cookie`."""
        s = cls(**kw)
        home = s.request('GET', '/', check_auth=False).text
        form = re.search(r'<form[^>]*action="/login".*?</form>', home, re.S)
        m = re.search(r'name="authenticity_token" value="([^"]+)"', form.group(0) if form else home)
        if not m:
            raise GradescopeError('login page has no authenticity_token')
        r = s.request('POST', '/login', data={
            'utf8': '✓', 'authenticity_token': m.group(1),
            'session[email]': email, 'session[password]': password,
            'session[remember_me]': '0', 'commit': 'Log In',
            'session[remember_me_sso]': '0'}, check_auth=False)
        if not r.history or urlparse(r.url).path in ('/login', '/'):
            raise AuthError('login failed: check the email and password')
        return s

    # ---- requests -----------------------------------------------------------
    def url(self, path: str) -> str:
        return path if path.startswith('http') else self.base_url + path

    def request(self, method: str, path: str, check_auth: bool = True, **kw) -> requests.Response:
        r = self.http.request(method, self.url(path), **kw)
        if check_auth:
            landed = urlparse(r.url).path
            asked = urlparse(self.url(path)).path
            if r.status_code == 401 or (landed in ('/', '/login') and asked not in ('/', '/login')):
                raise AuthError(f'{method} {path}: not logged in, or the cookie expired')
        return r

    def page(self, path: str) -> Page:
        r = self.request('GET', path)
        if r.status_code != 200:
            raise GradescopeError(f'GET {path}: HTTP {r.status_code}')
        p = Page(r.url, r.text)
        if p.csrf:
            self._csrf = p.csrf
        return p

    def csrf(self, path: str = '/account') -> str:
        """The session's CSRF token, fetching a page for it if none is cached."""
        if not self._csrf:
            self.page(path)
        if not self._csrf:
            raise GradescopeError('no csrf-token found')
        return self._csrf

    def write(self, method: str, path: str, body: Any, referer: Optional[str] = None) -> Any:
        """A JSON write the way the web app sends it; returns the decoded reply (or None)."""
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': self.csrf(),
                   'Referer': self.url(referer or path)}
        data = None if body is None else json.dumps(body)
        r = self.request(method, path, data=data, headers=headers)
        if r.status_code not in (200, 201, 204):
            raise GradescopeError(f'{method} {path}: HTTP {r.status_code}: {r.text[:300]}')
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None
