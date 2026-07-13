"""Auth request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole

# Password policy: a reasonable minimum length; hashing (Argon2id) caps cost so
# a generous maximum only guards against absurd inputs.
PasswordStr = Field(min_length=12, max_length=128)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = PasswordStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    totp_code: str | None = Field(default=None, min_length=6, max_length=6)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: UserRole
    is_active: bool
    is_verified: bool
    totp_enabled: bool
    must_change_password: bool
    created_at: datetime


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105  (label, not a secret)
    expires_in: int  # seconds
    user: UserOut


class TwoFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TwoFACodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = PasswordStr


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class MessageResponse(BaseModel):
    detail: str
