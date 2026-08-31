from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    scopes: frozenset[str]


async def principal(
    x_tenant_id: str = Header(...),
    x_user_id: str = Header(...),
    x_scopes: str = Header(default="runs:read runs:write agents:read"),
) -> Principal:
    # This is an intentionally narrow authentication hook. Deployments replace it with JWT/mTLS.
    if not x_tenant_id.strip() or not x_user_id.strip():
        raise HTTPException(status_code=401, detail="tenant and user identity are required")
    return Principal(x_tenant_id, x_user_id, frozenset(x_scopes.split()))


def require_scope(identity: Principal, scope: str) -> None:
    if scope not in identity.scopes:
        raise HTTPException(status_code=403, detail=f"missing scope {scope}")

