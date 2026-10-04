__version__ = '0.1.0'
alpha = False

try:
    from _build_info import (
        commit, commit_full, commit_date, commit_subject, dirty, build_time,
    )
except ImportError:
    commit = commit_full = commit_date = commit_subject = None
    dirty = build_time = None


def describe() -> str:
    """形如 '0.1.0+abc1234' / '0.1.0+abc1234.dirty' / '0.1.0'。"""
    if commit:
        return f"{__version__}+{commit}{'.dirty' if dirty else ''}"
    return __version__
