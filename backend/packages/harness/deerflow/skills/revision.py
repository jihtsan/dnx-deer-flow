"""Run-scoped GLOBAL Skill catalog revision consumed by runtime caches."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

GLOBAL_SKILL_CATALOG_REVISION_CONTEXT_KEY = "global_skill_catalog_revision"
_revision: ContextVar[str] = ContextVar("deerflow_global_skill_catalog_revision", default="0")


def current_global_skill_catalog_revision() -> str:
    return _revision.get()


@contextmanager
def bind_global_skill_catalog_revision(revision: str) -> Iterator[None]:
    if not revision or len(revision) > 200:
        raise ValueError("GLOBAL Skill catalog revision is invalid")
    token = _revision.set(revision)
    try:
        yield
    finally:
        _revision.reset(token)
