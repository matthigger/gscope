"""gscope login: cookies from a browser profile (browser_cookie3 stubbed), checks, saving."""
import io
import sys
import types
from http.cookiejar import Cookie, CookieJar

import pytest

from gscope import AuthError, Session, login
from gscope.cli import main

from fake_gs import FakeGradescope


def cookie(name, value, domain='www.gradescope.com'):
    return Cookie(0, name, value, None, False, domain, True, domain.startswith('.'), '/', True,
                  True, None, False, None, None, {})


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_bc3(monkeypatch):
    """A browser_cookie3 stand-in whose brave() returns these cookies."""
    jar_cookies = [cookie('signed_token', 'tok'), cookie('_gradescope_session', 'sess'),
                   cookie('other', 'x', '.example.com')]
    calls = []

    def brave(cookie_file=None, domain_name=''):
        calls.append(cookie_file)
        j = CookieJar()
        for c in jar_cookies:
            j.set_cookie(c)
        return j
    mod = types.SimpleNamespace(brave=brave, calls=calls, cookies=jar_cookies)
    monkeypatch.setitem(sys.modules, 'browser_cookie3', mod)
    return mod


def make_db(home, rel, mtime):
    import os
    f = home / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b'')
    os.utime(f, (mtime, mtime))
    return f


def test_cookie_files_finds_snap_brave_newest_first(home):
    old = make_db(home, '.config/BraveSoftware/Brave-Browser/Default/Cookies', 1000)
    new = make_db(home, 'snap/brave/current/.config/BraveSoftware/Brave-Browser/Default/Cookies', 2000)
    assert login.cookie_files('brave') == [new, old]


def test_from_browser_keeps_only_gradescope_cookies(home, fake_bc3):
    db = make_db(home, 'snap/brave/current/.config/BraveSoftware/Brave-Browser/Default/Cookies', 1)
    assert login.from_browser('brave') == 'signed_token=tok; _gradescope_session=sess'
    assert fake_bc3.calls == [str(db)]


def test_from_browser_without_a_session_says_log_in(home, fake_bc3):
    make_db(home, '.config/BraveSoftware/Brave-Browser/Default/Cookies', 1)
    fake_bc3.cookies[:] = [cookie('_cfuvid', 'x', '.guides.gradescope.com')]
    with pytest.raises(AuthError, match='log in to Gradescope in brave'):
        login.from_browser('brave')


def test_from_browser_no_profile(home, fake_bc3):
    with pytest.raises(AuthError, match='no brave profile'):
        login.from_browser('brave')


def test_from_browser_without_the_package(monkeypatch):
    monkeypatch.setitem(sys.modules, 'browser_cookie3', None)
    with pytest.raises(AuthError, match=r'gscope-cli\[browser\]'):
        login.from_browser('brave')


def test_check_refuses_a_non_sso_session_for_an_sso_only_course():
    gs = FakeGradescope(sso=False)
    gs.state['sso_only'] = {11}
    with pytest.raises(AuthError, match='School Credentials'):
        login.check('signed_token=tok', http=gs)
    assert login.check('signed_token=tok', http=FakeGradescope()) == 1


def test_check_refuses_an_expired_cookie():
    with pytest.raises(AuthError):
        login.check('signed_token=old', http=FakeGradescope(logged_in=False))


def test_save_is_private(tmp_path):
    p = login.save('a=1\n', tmp_path / 'sub' / 'cookie')
    assert p.read_text() == 'a=1\n' and (p.stat().st_mode & 0o777) == 0o600


def test_cli_login_paste(tmp_path, monkeypatch, capsys):
    gs = FakeGradescope()
    real = Session.from_cookie.__func__
    monkeypatch.setattr(Session, 'from_cookie', classmethod(lambda cls, c, **kw: real(cls, c, http=gs)))
    monkeypatch.setattr(sys, 'stdin', io.StringIO('signed_token=pasted\n'))
    out = tmp_path / 'cookie'
    assert main(['login', '--paste', '-o', str(out)]) == 0
    assert out.read_text() == 'signed_token=pasted\n'
    assert '1 courses visible' in capsys.readouterr().out


def test_cli_points_expired_users_to_login(monkeypatch, capsys):
    monkeypatch.setattr(Session, 'from_env', classmethod(lambda cls, *a, **k: Session(http=FakeGradescope(logged_in=False))))
    assert main(['courses']) == 1
    assert 'gscope login' in capsys.readouterr().err
