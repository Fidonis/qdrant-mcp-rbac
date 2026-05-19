"""Pydantic models used by the auth layer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QdrantAccessLevel = Literal["r", "rw", "m"]


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


class CollectionAccess(BaseModel):
    """Effective access on a single Qdrant collection."""

    collection: str
    access: QdrantAccessLevel


class AclEntry(BaseModel):
    """A single role-grant stored as a point in the ACL collection.

    For ``access == 'm'`` the ``collection`` field is informational only — the
    JWT builder collapses such a grant to a global manage token. Convention:
    use ``collection = '*'`` for global grants.
    """

    model_config = ConfigDict(extra="ignore")

    role: str = Field(min_length=1)
    collection: str = Field(min_length=1)
    access: QdrantAccessLevel


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
