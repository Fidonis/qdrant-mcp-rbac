"""Unit tests for the document-level filter primitives."""
from __future__ import annotations

import pytest
from qdrant_client.models import FieldCondition, Filter, MatchAny

from auth.doc_filter import (
    DENY_ALL,
    build_doc_filter,
    combine_with_user_filter,
    merge_doc_policies,
)
from auth.models import DocCondition, DocPolicy


def _allow(field: str, values: list[str]) -> DocCondition:
    return DocCondition(field=field, mode="allow", values=values)


def _deny(field: str, values: list[str]) -> DocCondition:
    return DocCondition(field=field, mode="deny", values=values)


# -------------------------- build_doc_filter --------------------------


def test_no_policy_yields_no_filter() -> None:
    assert build_doc_filter(None) is None


def test_default_allow_without_conditions_is_no_filter() -> None:
    assert build_doc_filter(DocPolicy(default="allow")) is None


def test_default_allow_with_deny_conditions_builds_must_not() -> None:
    policy = DocPolicy(
        default="allow",
        conditions=[_deny("acl_tags", ["confidential", "board-only"])],
    )
    result = build_doc_filter(policy)
    assert isinstance(result, Filter)
    assert result.must is None
    assert result.must_not is not None and len(result.must_not) == 1
    cond = result.must_not[0]
    assert isinstance(cond, FieldCondition)
    assert cond.key == "acl_tags"
    assert isinstance(cond.match, MatchAny)
    assert cond.match.any == ["confidential", "board-only"]


def test_default_allow_with_deny_wildcard_is_deny_all() -> None:
    policy = DocPolicy(
        default="allow",
        conditions=[_deny("acl_tags", ["*"])],
    )
    assert build_doc_filter(policy) is DENY_ALL


def test_default_deny_without_conditions_is_deny_all() -> None:
    assert build_doc_filter(DocPolicy(default="deny")) is DENY_ALL


def test_default_deny_with_allow_wildcard_is_no_filter() -> None:
    policy = DocPolicy(default="deny", conditions=[_allow("acl_tags", ["*"])])
    assert build_doc_filter(policy) is None


def test_default_deny_single_allow_builds_must() -> None:
    policy = DocPolicy(
        default="deny",
        conditions=[_allow("acl_tags", ["public", "preview"])],
    )
    result = build_doc_filter(policy)
    assert isinstance(result, Filter)
    assert result.must is not None and len(result.must) == 1
    assert result.should is None
    cond = result.must[0]
    assert isinstance(cond, FieldCondition)
    assert cond.key == "acl_tags"
    assert isinstance(cond.match, MatchAny)
    assert cond.match.any == ["public", "preview"]


def test_default_deny_multiple_allow_uses_should() -> None:
    policy = DocPolicy(
        default="deny",
        conditions=[
            _allow("acl_tags", ["public"]),
            _allow("source", ["annual-report.md"]),
        ],
    )
    result = build_doc_filter(policy)
    assert isinstance(result, Filter)
    assert result.must is None
    assert result.should is not None and len(result.should) == 2
    keys = {c.key for c in result.should if isinstance(c, FieldCondition)}
    assert keys == {"acl_tags", "source"}


def test_default_allow_with_multiple_deny_unions_them() -> None:
    policy = DocPolicy(
        default="allow",
        conditions=[
            _deny("acl_tags", ["confidential"]),
            _deny("source", ["secret.md"]),
        ],
    )
    result = build_doc_filter(policy)
    assert isinstance(result, Filter)
    assert result.must_not is not None and len(result.must_not) == 2


def test_irrelevant_conditions_ignored() -> None:
    # default=allow: only deny conditions matter; an allow condition is
    # silently ignored as it cannot ever shrink the visible set further.
    policy = DocPolicy(
        default="allow",
        conditions=[_allow("acl_tags", ["public"])],
    )
    assert build_doc_filter(policy) is None

    # Mirror case for default=deny.
    policy = DocPolicy(
        default="deny",
        conditions=[_deny("acl_tags", ["confidential"])],
    )
    assert build_doc_filter(policy) is DENY_ALL


# -------------------------- merge_doc_policies --------------------------


def test_merge_empty_returns_none() -> None:
    assert merge_doc_policies([]) is None


def test_merge_single_returns_it() -> None:
    policy = DocPolicy(default="allow", conditions=[_deny("acl_tags", ["x"])])
    assert merge_doc_policies([policy]) is policy


def test_merge_any_none_wins() -> None:
    # A grant without a doc_policy is fully permissive — the merge must
    # respect that and produce no policy at all.
    assert (
        merge_doc_policies(
            [None, DocPolicy(default="deny", conditions=[_allow("acl_tags", ["x"])])]
        )
        is None
    )


def test_merge_allow_beats_deny() -> None:
    allow_p = DocPolicy(
        default="allow", conditions=[_deny("acl_tags", ["confidential"])]
    )
    deny_p = DocPolicy(default="deny", conditions=[_allow("acl_tags", ["public"])])
    merged = merge_doc_policies([allow_p, deny_p])
    assert merged is not None
    assert merged.default == "allow"
    # The deny-default grant's allow conditions are dropped; only the
    # allow-default grant's deny conditions survive.
    assert [(c.field, c.mode, list(c.values)) for c in merged.conditions] == [
        ("acl_tags", "deny", ["confidential"])
    ]


def test_merge_unrestricted_allow_beats_restricted_allow() -> None:
    # An allow-default grant without any deny conditions admits everything;
    # under most-permissive merge it MUST win over a more restrictive peer.
    open_p = DocPolicy(default="allow")
    restricted_p = DocPolicy(
        default="allow", conditions=[_deny("acl_tags", ["confidential"])]
    )
    merged = merge_doc_policies([open_p, restricted_p])
    assert merged is not None
    assert merged.default == "allow"
    assert merged.conditions == []


def test_merge_two_allow_policies_unions_deny() -> None:
    a = DocPolicy(default="allow", conditions=[_deny("acl_tags", ["confidential"])])
    b = DocPolicy(default="allow", conditions=[_deny("source", ["secret.md"])])
    merged = merge_doc_policies([a, b])
    assert merged is not None
    assert merged.default == "allow"
    assert {(c.field, tuple(c.values)) for c in merged.conditions} == {
        ("acl_tags", ("confidential",)),
        ("source", ("secret.md",)),
    }


def test_merge_two_deny_policies_unions_allow() -> None:
    a = DocPolicy(default="deny", conditions=[_allow("acl_tags", ["public"])])
    b = DocPolicy(default="deny", conditions=[_allow("source", ["annual-report.md"])])
    merged = merge_doc_policies([a, b])
    assert merged is not None
    assert merged.default == "deny"
    assert {(c.field, tuple(c.values)) for c in merged.conditions} == {
        ("acl_tags", ("public",)),
        ("source", ("annual-report.md",)),
    }


# -------------------------- combine_with_user_filter --------------------------


def test_combine_no_filters() -> None:
    assert combine_with_user_filter(None, None) is None


def test_combine_user_only() -> None:
    user = {"must": [{"key": "year", "match": {"value": 2026}}]}
    assert combine_with_user_filter(None, user) == user


def test_combine_doc_only() -> None:
    doc = Filter(must_not=[FieldCondition(key="acl_tags", match=MatchAny(any=["x"]))])
    merged = combine_with_user_filter(doc, None)
    assert merged is not None
    assert "must_not" in merged
    assert merged["must_not"][0]["key"] == "acl_tags"


def test_combine_both_nests_under_must() -> None:
    user = {"must": [{"key": "year", "match": {"value": 2026}}]}
    doc = Filter(must_not=[FieldCondition(key="acl_tags", match=MatchAny(any=["x"]))])
    merged = combine_with_user_filter(doc, user)
    assert merged is not None
    assert list(merged.keys()) == ["must"]
    assert len(merged["must"]) == 2
    assert merged["must"][0] is user


# -------------------------- model validation --------------------------


def test_doc_condition_rejects_empty_values() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        DocCondition(field="acl_tags", mode="allow", values=[])


def test_doc_policy_round_trip_via_dict() -> None:
    payload = {
        "default": "deny",
        "conditions": [
            {"field": "acl_tags", "mode": "allow", "values": ["public"]},
        ],
    }
    policy = DocPolicy(**payload)
    assert policy.default == "deny"
    assert policy.model_dump() == payload
