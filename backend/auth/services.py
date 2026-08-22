"""Authentication and authorization business logic."""

import secrets
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.models import User, APIKey, Session as SessionModel, AuditLog
from backend.auth.security import (
    hash_password,
    verify_password,
    encode_jwt,
    decode_jwt,
    generate_api_key,
    hash_api_key,
    generate_secret_token,
    Role,
)
from backend.settings import get_settings


class AuthService:
    """Service layer for authentication operations."""

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Verify credentials and return user if valid."""
        user = self.db.query(User).filter(User.username == username, User.is_active).first()
        if not user:
            return None
        if verify_password(password, user.password_hash):
            user.last_login_at = datetime.utcnow()
            self.db.commit()
            return user
        return None

    def create_access_token(self, user: User, jti: Optional[str] = None) -> str:
        """Generate JWT access token for a user."""
        payload = {
            "sub": str(user.id),
            "role": user.role,
            "type": "access",
            "iat": int(time.time()),
            "exp": int(time.time()) + self.settings.jwt_access_token_expire_minutes * 60,
        }
        if jti:
            payload["jti"] = jti
        return encode_jwt(payload, self.settings.jwt_secret)

    def create_refresh_token(self, user: User, jti: str) -> str:
        """Generate JWT refresh token for a user."""
        payload = {
            "sub": str(user.id),
            "role": user.role,
            "type": "refresh",
            "jti": jti,
            "iat": int(time.time()),
            "exp": int(time.time()) + self.settings.jwt_refresh_token_expire_days * 86400,
        }
        return encode_jwt(payload, self.settings.jwt_secret)

    def create_session(self, user_id: int, ip_address: Optional[str], user_agent: Optional[str]) -> str:
        """Create a new session record and return its JTI (JWT ID)."""
        jti = generate_secret_token()
        expires_at = datetime.utcnow() + timedelta(days=self.settings.jwt_refresh_token_expire_days)
        session = SessionModel(
            user_id=user_id,
            token_jti=jti,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(session)
        self.db.commit()
        return jti

    def revoke_session(self, jti: str) -> bool:
        """Revoke a session by its JTI."""
        session = self.db.query(SessionModel).filter(SessionModel.token_jti == jti, SessionModel.is_active).first()
        if not session:
            return False
        session.is_active = False
        self.db.commit()
        return True

    def revoke_all_user_sessions(self, user_id: int) -> int:
        """Revoke all active sessions for a user."""
        sessions = self.db.query(SessionModel).filter(
            SessionModel.user_id == user_id,
            SessionModel.is_active,
        ).all()
        count = 0
        for session in sessions:
            session.is_active = False
            count += 1
        if count:
            self.db.commit()
        return count

    def login(self, username: str, password: str, ip_address: Optional[str], user_agent: Optional[str]) -> Tuple[str, str, Optional[str]]:
        """Perform login and return (access_token, refresh_token, session_jti)."""
        user = self.authenticate(username, password)
        if not user:
            raise ValueError("Invalid credentials")

        jti = None
        if self.settings.session_management_enabled:
            jti = self.create_session(user.id, ip_address, user_agent)

        access_token = self.create_access_token(user, jti)
        refresh_token = self.create_refresh_token(user, jti) if jti else None

        return access_token, refresh_token, jti

    def refresh_token(self, refresh_token: str, ip_address: Optional[str], user_agent: Optional[str]) -> Tuple[str, str, Optional[str]]:
        """Validate refresh token and issue new access token."""
        payload = decode_jwt(refresh_token, self.settings.jwt_secret)
        if not payload or payload.get("type") != "refresh":
            raise ValueError("Invalid refresh token")

        user_id = int(payload["sub"])
        old_jti = payload.get("jti")

        user = self.db.query(User).filter(User.id == user_id, User.is_active).first()
        if not user:
            raise ValueError("User not found or inactive")

        if self.settings.session_management_enabled:
            if not old_jti:
                raise ValueError("Missing session identifier")

            # Verify old session
            session = self.db.query(SessionModel).filter(
                SessionModel.token_jti == old_jti,
                SessionModel.user_id == user_id,
                SessionModel.is_active,
                SessionModel.expires_at > datetime.utcnow(),
            ).first()
            if not session:
                raise ValueError("Session revoked or expired")

            # Revoke old session and create new one
            session.is_active = False
            jti = self.create_session(user.id, ip_address, user_agent)
        else:
            jti = old_jti

        access_token = self.create_access_token(user, jti)
        new_refresh_token = self.create_refresh_token(user, jti) if jti else None

        return access_token, new_refresh_token, jti

    def logout(self, jti: str) -> bool:
        """Logout by revoking a session."""
        if not self.settings.session_management_enabled:
            return True  # Stateless JWT - nothing to revoke
        return self.revoke_session(jti)

    def create_user(self, username: str, email: Optional[str], password: str, role: str, created_by: Optional[int] = None) -> User:
        """Create a new user account."""
        if role not in ("admin", "operator", "viewer"):
            raise ValueError("Invalid role")

        existing = self.db.query(User).filter((User.username == username) | (User.email == email)).first()
        if existing:
            raise ValueError("Username or email already exists")

        password_hash = hash_password(password, iterations=self.settings.password_hash_iterations)
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
        )
        self.db.add(user)
        try:
            self.db.commit()
            self.db.refresh(user)
        except IntegrityError:
            self.db.rollback()
            raise ValueError("Database constraint violation")

        return user

    def update_user(self, user_id: int, **kwargs) -> User:
        """Update user attributes."""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        allowed = {"email", "role", "is_active"}
        for key, value in kwargs.items():
            if key in allowed:
                setattr(user, key, value)

        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return user

    def create_api_key(self, user_id: int, name: str, expires_in_days: Optional[int] = None) -> Tuple[str, APIKey]:
        """Create a new API key for a user."""
        user = self.db.query(User).filter(User.id == user_id, User.is_active).first()
        if not user:
            raise ValueError("User not found or inactive")

        # Generate API key
        plain_key = generate_api_key()
        key_hash = hash_api_key(plain_key)

        # Calculate expiry
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

        api_key = APIKey(
            user_id=user_id,
            key_hash=key_hash,
            name=name,
            expires_at=expires_at,
        )
        self.db.add(api_key)
        self.db.commit()
        self.db.refresh(api_key)

        return plain_key, api_key

    def revoke_api_key(self, key_id: int, user_id: Optional[int] = None) -> bool:
        """Revoke an API key."""
        query = self.db.query(APIKey).filter(APIKey.id == key_id)
        if user_id is not None:
            query = query.filter(APIKey.user_id == user_id)
        api_key = query.first()
        if not api_key:
            return False
        api_key.is_active = False
        self.db.commit()
        return True

    def get_user_api_keys(self, user_id: int) -> List[APIKey]:
        """Get all API keys for a user (excluding hashes)."""
        return self.db.query(APIKey).filter(APIKey.user_id == user_id).order_by(APIKey.created_at.desc()).all()

    def log_audit_event(
        self,
        user_id: Optional[int],
        username: Optional[str],
        action: str,
        resource_type: Optional[str],
        resource_id: Optional[str],
        status: str,
        ip_address: Optional[str],
        user_agent: Optional[str],
        details: Optional[dict] = None,
    ) -> AuditLog:
        """Record an audit log entry."""
        log = AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log
