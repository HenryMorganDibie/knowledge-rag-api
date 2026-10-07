"""ACL tests: the filter must fail closed and be present on every chunk query."""

import pytest

from retrieval import hybrid_retriever as hr
from retrieval.hybrid_retriever import _acl_clause


def test_no_groups_is_public_only():
    for groups in (None, []):
        clause, params = _acl_clause(groups)
        assert "= '[]'::jsonb" in clause
        assert "?|" not in clause
        assert params == {}


def test_groups_allow_public_or_overlap():
    clause, params = _acl_clause(["engineering", "it-ops"])
    assert "?|" in clause and "= '[]'::jsonb" in clause
    assert params == {"acl_groups": ["engineering", "it-ops"]}


class _FakeResult:
    def mappings(self):
        return self

    def all(self):
        return []


class _FakeDB:
    """Captures the SQL text and params instead of hitting a database."""

    def __init__(self):
        self.sql = None
        self.params = None

    async def execute(self, sql, params):
        self.sql, self.params = str(sql), params
        return _FakeResult()


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [None, [], ["engineering"]])
async def test_vector_search_always_filters_by_acl(groups):
    db = _FakeDB()
    await hr._vector_search(db, [0.1, 0.2], 5, groups)
    assert "acl_groups" in db.sql


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [None, [], ["engineering"]])
async def test_bm25_search_always_filters_by_acl(groups):
    db = _FakeDB()
    await hr._bm25_search(db, "sso", 5, groups)
    assert "acl_groups" in db.sql
