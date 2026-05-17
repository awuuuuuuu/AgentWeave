from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    # 部门邀请码（可选）：填写后自动归入对应部门
    invite_code: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("密码必须包含至少一个大写字母")
        if not re.search(r"\d", v):
            raise ValueError("密码必须包含至少一个数字")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    org_id: str | None
    org_name: str | None = None  # 从 ORM 关联的 Organization.name 派生
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _extract_org_name(cls, data: Any) -> Any:
        # 读 __dict__ 而非属性访问，避免在 async 上下文触发 SQLAlchemy 懒加载
        if not isinstance(data, dict):
            org = data.__dict__.get("org")
            if org is not None:
                data.__dict__.setdefault("org_name", org.name)
        return data
