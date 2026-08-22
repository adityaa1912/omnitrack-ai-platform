"""Pydantic models for auth API requests/responses."""

from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class LoginRequest(BaseModel):
    """Request body for POST /auth/login."""
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=1024)


class TokenResponse(BaseModel):
    """Response body for token endpoints."""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    """Request body for user creation (admin only)."""
    username: str = Field(..., min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    password: str = Field(..., min_length=8, max_length=1024)
    role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")


class UserUpdate(BaseModel):
    """Request body for user updates."""
    email: Optional[str] = None
    role: Optional[str] = Field(None, pattern="^(admin|operator|viewer)$")
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    """Response body for user queries."""
    id: int
    username: str
    email: Optional[str]
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class APIKeyCreate(BaseModel):
    """Request body for API key creation."""
    name: str = Field(..., min_length=1, max_length=255)
    expires_in_days: Optional[int] = Field(None, ge=1)


class APIKeyResponse(BaseModel):
    """Response body for API key (only shown once on creation)."""
    id: int
    name: str
    key: Optional[str] = None
    is_active: bool
    created_at: datetime
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(BaseModel):
    """Response body for audit log entries."""
    id: int
    username: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    status: str
    ip_address: Optional[str]
    details: Optional[dict]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
