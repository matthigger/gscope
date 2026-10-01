"""Signing in: take the Gradescope session cookie from a browser you are logged in with.

``gscope login`` reads the gradescope.com cookies out of a local browser profile
(Brave by default; Chrome, Chromium and Firefox too), checks them against
Gradescope, and saves them to ~/.config/gscope/cookie.  Reading the browser
needs the optional ``browser_cookie3`` package: ``pip install "gscope-cli[browser]"``.
Without it, or when the browser cannot be read, ``gscope login --paste`` takes the
Cookie header copied from DevTools.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Optional

from .session import DEFAULT_COOKIE_FILE, AuthError, Session

DOMAIN = 'gradescope.com'
BROWSERS = ('brave', 'chrome', 'chromium', 'firefox')

# Profiles browser_cookie3 does not look in (snap and flatpak installs); its own
# defaults are tried after these.
_EXTRA = {
    'brave': ['~/snap/brave/current/.config/BraveSoftware/Brave-Browser'],
    'chrome': ['~/.var/app/com.google.Chrome/config/google-chrome'],
    'chromium': ['~/snap/chromium/common/chromium', '~/.var/app/org.chromium.Chromium/config/chromium'],
    'firefox': ['~/snap/firefox/common/.mozilla/firefox', '~/.var/app/org.mozilla.firefox/.mozilla/firefox'],
}
_STANDARD = {
    'brave': ['~/.config/BraveSoftware/Brave-Browser'],
    'chrome': ['~/.config/google-chrome'],
    'chromium': ['~/.config/chromium'],
    'firefox': ['~/.mozilla/firefox'],
}


def cookie_files(browser: str) -> list[Path]:
    """The browser's cookie databases, most recently used first."""
    pattern = ['*/cookies.sqlite'] if browser == 'firefox' else \
              ['Default/Cookies', 'Default/Network/Cookies', 'Profile */Cookies', 'Profile */Network/Cookies']
    found = []
    for root in _EXTRA[browser] + _STANDARD[browser]:
        for p in pattern:
            found += [Path(f) for f in glob.glob(os.path.join(os.path.expanduser(root), p))]
    return sorted(set(found), key=lambda f: f.stat().st_mtime, reverse=True)


def from_browser(browser: str = 'brave', cookie_file=None) -> str:
    """The gradescope.com cookies in ``browser``, as a Cookie header string."""
    if browser not in BROWSERS:
        raise ValueError(f'browser must be one of {", ".join(BROWSERS)}')
    try:
        import browser_cookie3
    except ImportError:
        raise AuthError('reading browser cookies needs browser_cookie3: '
                        'pip install "gscope-cli[browser]", or use gscope login --paste') from None
    files = [Path(cookie_file).expanduser()] if cookie_file else cookie_files(browser)
    if not files:
        raise AuthError(f'no {browser} profile found; pass --cookie-db PATH to its cookie database')
    errors = []
    for f in files:
        try:
            jar = getattr(browser_cookie3, browser)(cookie_file=str(f), domain_name=DOMAIN)
        except Exception as e:          # locked database, keyring refused, ...
            errors.append(f'{f}: {e}')
            continue
        cookies = {c.name: c.value for c in jar if c.domain.lstrip('.').endswith(DOMAIN)}
        if 'signed_token' in cookies or '_gradescope_session' in cookies:
            return '; '.join(f'{k}={v}' for k, v in cookies.items())
        errors.append(f'{f}: no gradescope.com session (log in to Gradescope in {browser} first)')
    raise AuthError('could not get a Gradescope cookie from ' + browser + ':\n  ' + '\n  '.join(errors))


def check(cookie: str, **kw) -> int:
    """Raise AuthError unless the cookie is logged in and can open your newest
    course; returns how many courses it sees.

    Opening a course matters: a course can be set to allow only single sign-on
    logins, and then a session restored by "remember me" (or a password login)
    lists the course on the account page but gets a 401 inside it.
    """
    from . import course
    s = Session.from_cookie(cookie, **kw)
    cs = course.courses(s)
    if cs:
        try:
            s.page(f'/courses/{cs[0].id}')
        except AuthError as e:
            if 'Single Sign-On' in str(e) or 'LMS' in str(e):
                raise AuthError(f'{e}\nThis browser session did not come from a single sign-on login. '
                                'In the browser, log out of Gradescope, log back in with School '
                                'Credentials, then run gscope login again.') from None
            raise
    return len(cs)


def save(cookie: str, path=DEFAULT_COOKIE_FILE) -> Path:
    """Write the cookie readable by you only."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(cookie.strip() + '\n')
    os.chmod(path, 0o600)
    return path


def login(browser: Optional[str] = 'brave', cookie: Optional[str] = None, cookie_db=None,
          path=DEFAULT_COOKIE_FILE, **kw) -> tuple[Path, int]:
    """Get a cookie (``cookie`` as given, else from ``browser``), check it, save it."""
    cookie = cookie if cookie is not None else from_browser(browser, cookie_db)
    n = check(cookie, **kw)
    return save(cookie, path), n
