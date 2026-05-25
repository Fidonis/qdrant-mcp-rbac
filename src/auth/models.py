"""Pydantic models used by the auth layer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QdrantAccessLevel = Literal["r", "rw", "m"]
DocPolicyDefault = Literal["allow", "deny"]
DocConditionMode = Literal["allow", "deny"]


class OIDCClaims(BaseModel):
    """Subset of OIDC token claims relevant to authorization decisions."""

    sub: str
    email: str | None = None
    preferred_username: str | None = None
    realm_roles: list[str] = Field(default_factory=list)
    client_roles: list[str] = Field(default_factory=list)

    @property
    def all_roles(self) -> list[str]:
        return [*self.realm_roles, *self.client_roles]


class DocCondition(BaseModel):
    """One clause inside a :class:`DocPolicy`.

    A condition targets a single payload ``field`` and lists the ``values``
    that should be matched. The ``mode`` selects whether matching documents
    are exposed (``allow``) or hidden (``deny``).
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    mode: DocConditionMode
    values: list[str] = Field(min_length=1)


class DocPolicy(BaseModel):
    """Document-level access policy attached to a role grant.

    The policy is evaluated server-side: a Qdrant payload filter is built
    from it and injected as a ``must`` clause into every read on the
    associated collection.
    """

    model_config = ConfigDict(extra="forbid")

    default: DocPolicyDefault
    conditions: list[DocCondition] = Field(default_factory=list)


class CollectionAccess(BaseModel):
    """Effective access on a single Qdrant collection."""

    collection: str
    access: QdrantAccessLevel
    doc_policy: DocPolicy | None = None


class AclEntry(BaseModel):
    """A single role-grant stored as a point in the ACL collection.

    For ``access == 'm'`` the ``collection`` field is informational only — the
    JWT builder collapses such a grant to a global manage token. Convention:
    use ``collection = '*'`` for global grants.

    ``doc_policy`` is optional. When present, reads on this collection are
    filtered to the documents the policy admits.
    """

    model_config = ConfigDict(extra="ignore")

    role: str = Field(min_length=1)
    collection: str = Field(min_length=1)
    access: QdrantAccessLevel
    doc_policy: DocPolicy | None = None


class QdrantToken(BaseModel):
    """Signed Qdrant JWT plus the access rules it represents."""

    token: str
    access_rules: list[CollectionAccess]
    has_global_manage: bool = False


_RANK = {"r": 1, "rw": 2, "m": 3}


def access_rank(level: QdrantAccessLevel) -> int:
    """Return a numeric rank so access levels can be compared."""
    return _RANK[level]


def access_satisfies(granted: QdrantAccessLevel, required: QdrantAccessLevel) -> bool:
    """True if `granted` is at least as permissive as `required`."""
    return access_rank(granted) >= access_rank(required)
