"""Unofficial instructor-side Gradescope client.

    from gscope import Session, assignment, rubric
    s = Session.from_cookie_file('~/.config/gscope/cookie')
    a = assignment.load(s, course_id, assignment_id)
"""
__version__ = '0.3.0'

from .session import AuthError, GradescopeError, Page, Session  # noqa: E402,F401
from . import assignment, course, rubric, spec  # noqa: E402,F401
