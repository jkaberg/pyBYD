"""Authentication token model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AuthToken(BaseModel):
    """Token returned after successful login.

    Parameters
    ----------
    user_id : str
        The authenticated user's ID.
    sign_token : str
        Token for signature key derivation.
    encry_token : str
        Token for encryption key derivation.
    raw : dict
        Full decoded token dict for access to additional fields.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    sign_token: str
    encry_token: str
    refresh_token: str = ""
    raw: dict[str, Any]
