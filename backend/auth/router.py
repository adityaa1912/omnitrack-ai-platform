"""Authentication REST API routes."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.auth.dependencies import (
    get_db,
    get_current_user,
    CurrentUser,
    require_role,
)
from backend.auth.models import (
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserUpdate,
    UserResponse,
    APIKeyCreate,
    APIKeyResponse,
    AuditLogResponse,
)
from backend.auth.services import AuthService
from backend.auth.rate_limit import rate_limit_auth
from backend.settings import get_settings


router = APIRouter(prefix="/auth", tags=["authentication"])


def _get_client_info(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Extract client IP and user agent from request."""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    return ip, user_agent


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate user and return JWT tokens."""
    settings = get_settings()
    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not configured",
        )

    service = AuthService(db)
    ip, user_agent = _get_client_info(request)

    try:
        access_token, refresh_token, jti = service.login(body.username, body.password, ip, user_agent)
    except ValueError as e:
        service.log_audit_event(
            user_id=None,
            username=body.username,
            action="login",
            resource_type="auth",
            resource_id=None,
            status="failure",
            ip_address=ip,
            user_agent=user_agent,
            details={"reason": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    service.log_audit_event(
        user_id=None,
        username=body.username,
        action="login",
        resource_type="auth",
        resource_id=None,
        status="success",
        ip_address=ip,
        user_agent=user_agent,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def refresh_token(request: Request, refresh_token: str, db: Session = Depends(get_db)):
    """Refresh an access token using a refresh token."""
    settings = get_settings()
    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not configured",
        )

    service = AuthService(db)
    ip, user_agent = _get_client_info(request)

    try:
        access_token, new_refresh, jti = service.refresh_token(refresh_token, ip, user_agent)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post("/logout")
async def logout(request: Request, jti: Optional[str] = None, db: Session = Depends(get_db)):
    """Logout by revoking the session."""
    service = AuthService(db)
    success = service.logout(jti) if jti else True

    ip, user_agent = _get_client_info(request)
    service.log_audit_event(
        user_id=None,
        username=None,
        action="logout",
        resource_type="auth",
        resource_id=jti,
        status="success" if success else "failure",
        ip_address=ip,
        user_agent=user_agent,
    )

    return {"status": "logged_out"}


@router.post("/register", response_model=UserResponse, dependencies=[Depends(rate_limit_auth)])
async def register(request: Request, body: UserCreate, db: Session = Depends(get_db)):
    """Register a new user (self-registration disabled in production by default)."""
    settings = get_settings()
    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not configured",
        )

    # In production, registration should be admin-only
    if settings.environment not in ("development", "test"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is disabled",
        )

    service = AuthService(db)
    ip, user_agent = _get_client_info(request)

    try:
        user = service.create_user(body.username, body.email, body.password, body.role)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    service.log_audit_event(
        user_id=user.id,
        username=user.username,
        action="register",
        resource_type="user",
        resource_id=str(user.id),
        status="success",
        ip_address=ip,
        user_agent=user_agent,
    )

    return UserResponse.model_validate(user)


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: Request,
    body: UserCreate,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_role("admin")),
):
    """Create a new user (admin only)."""
    service = AuthService(db)

    try:
        user = service.create_user(body.username, body.email, body.password, body.role, created_by=admin.user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    service.log_audit_event(
        user_id=admin.user_id,
        username=admin.username,
        action="create_user",
        resource_type="user",
        resource_id=str(user.id),
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )

    return UserResponse.model_validate(user)


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_role("admin")),
):
    """List all users (admin only)."""
    from backend.models import User

    users = db.query(User).order_by(User.created_at.desc()).all()
    return [UserResponse.model_validate(u) for u in users]


@router.get("/users/me", response_model=UserResponse)
async def get_current_user_info(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get current user info."""
    from backend.models import User
    user = db.query(User).filter(User.id == current_user.user_id).first()
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_role("admin")),
):
    """Update user (admin only)."""
    service = AuthService(db)

    try:
        user = service.update_user(user_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    service.log_audit_event(
        user_id=admin.user_id,
        username=admin.username,
        action="update_user",
        resource_type="user",
        resource_id=str(user.id),
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )

    return UserResponse.model_validate(user)


@router.post("/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    request: Request,
    body: APIKeyCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new API key for the current user."""
    service = AuthService(db)

    plain_key, api_key = service.create_api_key(current_user.user_id, body.name, body.expires_in_days)

    service.log_audit_event(
        user_id=current_user.user_id,
        username=current_user.username,
        action="create_api_key",
        resource_type="api_key",
        resource_id=str(api_key.id),
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )

    return APIKeyResponse(
        id=api_key.id,
        key=plain_key,
        name=api_key.name,
        is_active=api_key.is_active,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
    )


@router.get("/api-keys", response_model=List[APIKeyResponse])
async def list_api_keys(
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all API keys for the current user."""
    service = AuthService(db)
    keys = service.get_user_api_keys(current_user.user_id)

    return [
        APIKeyResponse(
            id=k.id,
            key=None,  # Never expose the key again
            name=k.name,
            is_active=k.is_active,
            created_at=k.created_at,
            expires_at=k.expires_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    request: Request,
    key_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Revoke an API key."""
    service = AuthService(db)

    success = service.revoke_api_key(key_id, current_user.user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    service.log_audit_event(
        user_id=current_user.user_id,
        username=current_user.username,
        action="revoke_api_key",
        resource_type="api_key",
        resource_id=str(key_id),
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )

    return {"status": "revoked"}


@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def list_audit_logs(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 100,
    admin: CurrentUser = Depends(require_role("admin")),
):
    """List audit logs (admin only)."""
    from backend.models import AuditLog

    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [AuditLogResponse.model_validate(l) for l in logs]