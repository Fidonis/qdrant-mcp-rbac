"""Build short-lived Qdrant JWTs from validated OIDC claims."""
from __future__ import annotations

import logging
import time
from typing import Any

from jose import jwt

from .acl import AclResolver
from .doc_filter import merge_doc_policies
from .models import (
    CollectionAccess,
    DocPolicy,
    OIDCClaims,
    QdrantToken,
    access_rank,
)

logger = logging.getLogger(__name__)

# Qdrant validates JWTs against the static api_key as a shared secret, which
# only works with HS256. There is no plausible reason to parameterize this.
_QDRANT_JWT_ALG = "HS256"


def mint_service_token(secret: str, ttl_seconds: int) -> str:
    """Mint a short-lived global-manage Qdrant JWT for server-internal calls.

    Free function (rather than a builder method) so the ACL resolver can be
    wired with a ``functools.partial`` and doesn't need a back-reference to
    the :class:`QdrantJWTBuilder` instance.
    """
    payload = {
        "exp": int(time.time()) + ttl_seconds,
        "access": "m",
    }
    return jwt.encode(payload, secret, algorithm=_QDRANT_JWT_ALG)


class QdrantJWTBuilder:
    """Maps OIDC roles onto Qdrant access rules and signs the resulting JWT.

    Two routes to global-manage:
      1. The OIDC claims contain the configured ``admin_role`` (env-defined
         break-glass admin), or
      2. an ACL grant in the dedicated ACL collection has ``access == 'm'``.
    """

    def __init__(
        self,
        secret: str,
        admin_role: str,
        ttl_seconds: int,
        resolver: AclResolver,
    ) -> None:
        self._secret = secret
        self._admin_role = admin_role
        self._ttl = ttl_seconds
        self._resolver = resolver

    async def build(self, claims: OIDCClaims) -> QdrantToken:
        if self._admin_role and self._admin_role in claims.all_roles:
            logger.debug("Issuing global-manage token to admin sub=%s", claims.sub)
            return self._build_global_manage_token()

        mapping = await self._resolver.get_mapping()
        access_rules, has_global_manage = self._derive_access(claims, mapping)

        if has_global_manage:
            return self._build_global_manage_token()

        payload: dict[str, Any] = {
            "exp": int(time.time()) + self._ttl,
            "access": [
                {"collection": rule.collection, "access": rule.access}
                for rule in access_rules
            ],
        }
        token = jwt.encode(payload, self._secret, algorithm=_QDRANT_JWT_ALG)

        logger.debug(
            "Issued Qdrant JWT for sub=%s with %d access rules",
            claims.sub,
            len(access_rules),
        )

        return QdrantToken(
            token=token,
            access_rules=access_rules,
            has_global_manage=False,
        )

    def _build_global_manage_token(self) -> QdrantToken:
        payload = {
            "exp": int(time.time()) + self._ttl,
            "access": "m",
        }
        token = jwt.encode(payload, self._secret, algorithm=_QDRANT_JWT_ALG)
        return QdrantToken(token=token, access_rules=[], has_global_manage=True)

    def _derive_access(
        self,
        claims: OIDCClaims,
        mapping: dict[str, list[CollectionAccess]],
    ) -> tuple[list[CollectionAccess], bool]:
        """Reduce the user's roles to one effective access entry per collection.

        If any matching grant has ``access == 'm'`` we short-circuit to a global
        manage token.

        When the user holds multiple grants on the same collection, the
        effective ``access`` is the most permissive level and the effective
        ``doc_policy`` is the most-permissive merge of the individual policies
        (see :func:`auth.doc_filter.merge_doc_policies`).
        """
        per_collection: dict[str, list[CollectionAccess]] = {}
        for role in claims.all_roles:
            for grant in mapping.get(role, []):
                if grant.access == "m":
                    return [], True
                per_collection.setdefault(grant.collection, []).append(grant)

        rules: list[CollectionAccess] = []
        for collection, grants in per_collection.items():
            best = grants[0]
            for grant in grants[1:]:
                if access_rank(grant.access) > access_rank(best.access):
                    best = grant
            merged_policy = self._merge_policies(grants)
            rules.append(
                CollectionAccess(
                    collection=collection,
                    access=best.access,
                    doc_policy=merged_policy,
                )
            )
        return rules, False

    @staticmethod
    def _merge_policies(grants: list[CollectionAccess]) -> DocPolicy | None:
        return merge_doc_policies([g.doc_policy for g in grants])
