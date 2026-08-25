"""FastAPI dependency injection for authentication and authorization."""

from typing import Optional, Annotated
from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel

from backend.auth.security import decode_jwt, Role, role_satisfies
from backend.settings import get_settings
from backend.observability import correlation as _correlation


class TokenPayload:
    """Decoded JWT token claims."""

    def __init__(self, sub: str, role: str, type: str, jti: Optional[str] = None):
        self.user_id = int(sub)
        self.role = role
        self.type = type  # "access" or "refresh"
        self.jti = jti


class CurrentUser(BaseModel):
    """Currently authenticated user from JWT or API key."""

    user_id: int
    username: str
    email: Optional[str] = None
    role: str
    via_api_key: bool = False


# Placeholder get_db dependency; overridden in main.py after service is ready.
# Using a callable that is replaced at app startup, allowing auth routes to use
# the service's scoped_session without a circular import.
class _GetDb:
    """Callable dependency for DB session; replaced by main.py at startup."""

    _provider = None

    def __call__(self):
        if self._provider is None:
            raise RuntimeError("get_db not configured — call configure_get_db()")
        return self._provider()


_get_db_impl = _GetDb()


def get_db():
    """FastAPI dependency: current thread database session from the service Session."""
    return _get_db_impl()


def configure_get_db(provider) -> None:
    """Wire the DB session provider from the InferenceService's scoped_session."""
    _get_db_impl._provider = provider


async def _get_token_from_request(request: Request) -> Optional[str]:
    """Extract JWT token from Authorization header or query param."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.query_params.get("access_token")


async def get_current_user(
    token: Optional[str] = Depends(_get_token_from_request),
    db=Depends(get_db),
) -> CurrentUser:
    """Dependency: extract and validate the current user from JWT or API key.

    Raises HTTPException 401 if token is missing/invalid/expired.
    """
    from backend.models import User, Session as SessionModel

    settings = get_settings()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT authentication not configured",
        )

    payload = decode_jwt(token, settings.jwt_secret)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id: int = int(payload.get("sub"))
        token_type: str = payload.get("type", "access")
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )

    if token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not an access token",
        )

    user = db.query(User).filter(User.id == user_id, User.is_active).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    if settings.session_management_enabled:
        jti = payload.get("jti")
        if jti:
            session = db.query(SessionModel).filter(
                SessionModel.token_jti == jti,
                SessionModel.is_active,
                SessionModel.expires_at > datetime.utcnow(),
            ).first()
            if not session:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session revoked or expired",
                )

    _correlation.bind_user_id(user.id)
    return CurrentUser(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        via_api_key=False,
    )


async def get_current_user_optional(
    request: Request,
    db=Depends(get_db),
) -> Optional[CurrentUser]:
    """Dependency: extract current user if token present, else None."""
    token = await _get_token_from_request(request)
    if not token:
        return None
    try:
        return await get_current_user(token=token, db=db)
    except HTTPException:
        return None


def require_role(required_role: str):
    """Dependency factory: enforce a minimum role level (ADMIN > OPERATOR > VIEWER)."""
    async def check_role(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if not role_satisfies(user.role, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions (requires {required_role})",
            )
        return user
    return check_role


def require_any_role(*roles: str):
    """Dependency factory: enforce one of several roles."""
    async def check_roles(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if not any(role_satisfies(user.role, r) for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user
    return check_roles


async def get_api_key_user(
    api_key: str,
    db=Depends(get_db),
) -> CurrentUser:
    """Validate API key and return the associated user.

    Called from WebSocket handlers to authenticate before accepting the connection.
    """
    from backend.models import User, APIKey
    from backend.auth.security import verify_api_key_hash

    candidates = db.query(APIKey).filter(
        APIKey.is_active,
        (APIKey.expires_at == None) | (APIKey.expires_at > datetime.utcnow()),
    ).all()

    matched = None
    for candidate in candidates:
        if verify_api_key_hash(api_key, candidate.key_hash):
            matched = candidate
            break

    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    user = db.query(User).filter(User.id == matched.user_id, User.is_active).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    matched.last_used_at = datetime.utcnow()
    db.commit()

    return CurrentUser(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        via_api_key=True,
    )
