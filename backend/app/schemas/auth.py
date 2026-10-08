"""Auth request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from app.core.config import get_settings
from app.models.user import UserRole
from app.schemas.base import RequestModel

# Password policy: a reasonable minimum length; hashing (Argon2id) caps cost so
# a generous maximum only guards against absurd inputs.
PasswordStr = Field(min_length=12, max_length=128)


class RegisterRequest(RequestModel):
    email: EmailStr
    password: str = PasswordStr


class LoginRequest(RequestModel):
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def two_factor_required(self) -> bool:
        """Whether the server requires TOTP before this account's protected routes
        (admins, when ``ADMIN_2FA_REQUIRED``), so the client guard follows the
        server instead of guessing the setting (F10)."""
        return self.role == UserRole.admin and get_settings().admin_2fa_required


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105  (label, not a secret)
    expires_in: int  # seconds
    user: UserOut


class TwoFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TwoFACodeRequest(RequestModel):
    code: str = Field(min_length=6, max_length=6)


class ChangePasswordRequest(RequestModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = PasswordStr


class VerifyEmailRequest(RequestModel):
    token: str = Field(min_length=16, max_length=128)


class MessageResponse(BaseModel):
    detail: str
