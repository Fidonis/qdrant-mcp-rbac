"""Build and merge Qdrant payload filters from :class:`DocPolicy` objects.

The filter produced here is injected as an additional ``must`` clause into
every read operation on the target collection. It is enforced server-side
and is invisible to the caller.

The module exposes three primitives:

* :func:`build_doc_filter` — turn one (possibly merged) policy into a
  Qdrant ``Filter`` or one of the two terminal outcomes:

  - ``None``  — no filter generated, all documents pass through.
  - :data:`DENY_ALL` — empty result set; the caller short-circuits the
    Qdrant call and returns no results.

* :func:`merge_doc_policies` — combine the policies attached to multiple
  grants on the same collection using most-permissive semantics
  (``default: "allow"`` wins; allow-conditions unioned; deny-conditions
  unioned).

* :func:`combine_with_user_filter` — fold the doc filter into the
  caller-supplied ``query_filter`` so both apply with ``must`` semantics.
"""
from __future__ import annotations

from typing import Any

from qdrant_client.models import FieldCondition, Filter, MatchAny

from .models import DocCondition, DocPolicy

WILDCARD = "*"


class _DenyAll:
    """Singleton sentinel meaning 'every document is filtered out'.

    Returned by :func:`build_doc_filter` when the policy admits no
    documents — the tool should return an empty result set without
    issuing the Qdrant query.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "DENY_ALL"


DENY_ALL = _DenyAll()

DocFilter = Filter | None | _DenyAll


def _field_cond(condition: DocCondition) -> FieldCondition:
    return FieldCondition(
        key=condition.field,
        match=MatchAny(any=list(condition.values)),
    )


def build_doc_filter(policy: DocPolicy | None) -> DocFilter:
    """Translate a :class:`DocPolicy` into a Qdrant ``Filter``.

    Returns ``None`` for "no filter, all documents visible" and
    :data:`DENY_ALL` for "empty result set". The caller MUST treat
    :data:`DENY_ALL` specially — passing it to Qdrant is meaningless.
    """
    if policy is None:
        return None

    if policy.default == "allow":
        deny_conds = [c for c in policy.conditions if c.mode == "deny"]
        if not deny_conds:
            return None
        if any(WILDCARD in c.values for c in deny_conds):
            return DENY_ALL
        return Filter(must_not=[_field_cond(c) for c in deny_conds])

    # default == "deny": only allow conditions can expose documents.
    allow_conds = [c for c in policy.conditions if c.mode == "allow"]
    if not allow_conds:
        return DENY_ALL
    if any(WILDCARD in c.values for c in allow_conds):
        return None
    field_conds = [_field_cond(c) for c in allow_conds]
    if len(field_conds) == 1:
        return Filter(must=field_conds)
    # Multiple allow conditions are unioned: a document passes if it matches
    # at least one of them.
    return Filter(should=field_conds)


def merge_doc_policies(policies: list[DocPolicy | None]) -> DocPolicy | None:
    """Merge per-grant policies for the same collection.

    Rules (most-permissive):

    * If any grant carries no policy (``None``), the merged result is
      ``None`` — that grant alone admits every document.
    * If any grant has ``default: "allow"``, the merged result is
      ``default: "allow"`` with the union of all such grants' deny
      conditions. Default-deny grants are ignored in this case.
    * Otherwise all grants are ``default: "deny"`` and the merged
      result is ``default: "deny"`` with the union of all allow
      conditions.
    """
    if not policies:
        return None
    if any(p is None for p in policies):
        return None

    concrete: list[DocPolicy] = [p for p in policies if p is not None]
    if len(concrete) == 1:
        return concrete[0]

    allow_default = [p for p in concrete if p.default == "allow"]
    if allow_default:
        merged_denies: list[DocCondition] = []
        for p in allow_default:
            for c in p.conditions:
                if c.mode == "deny":
                    merged_denies.append(c)
            # A default-allow grant with no deny conditions admits everything;
            # under most-permissive merge that wins.
            if not any(c.mode == "deny" for c in p.conditions):
                return DocPolicy(default="allow", conditions=[])
        return DocPolicy(default="allow", conditions=merged_denies)

    merged_allows: list[DocCondition] = []
    for p in concrete:
        for c in p.conditions:
            if c.mode == "allow":
                merged_allows.append(c)
    return DocPolicy(default="deny", conditions=merged_allows)


def combine_with_user_filter(
    doc_filter: Filter | None,
    user_filter: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Combine an ACL doc filter with a caller-supplied filter dict.

    The two filters are joined with ``must`` semantics: a point is
    returned only if it satisfies both. Either side may be absent.

    Returns the merged filter as a plain dict suitable for Qdrant; the
    underlying call site reconstructs a :class:`Filter` from it.
    """
    if doc_filter is None and user_filter is None:
        return None
    if doc_filter is None:
        return user_filter
    doc_dict = doc_filter.model_dump(exclude_none=True)
    if user_filter is None:
        return doc_dict
    # Nest both as siblings in a top-level `must` so each side keeps its
    # own clause semantics (must / must_not / should) intact.
    return {"must": [user_filter, doc_dict]}
