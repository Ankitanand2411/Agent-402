from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from config import settings
from services.pricing import price_to_units


class ToolCreate(BaseModel):
    name: str
    description: str
    price: str
    targetUrl: Optional[str] = None
    parameters: Optional[dict] = None
    type: Literal["proxy", "code"] = "proxy"
    code: Optional[str] = None
    walletAddress: Optional[str] = None

    @field_validator("price")
    @classmethod
    def price_must_be_representable(cls, v: str) -> str:
        """Reject prices that are not numbers or need more than TOKEN_DECIMALS places."""
        price_to_units(v, settings.TOKEN_DECIMALS)  # raises ValueError -> 422
        return v.strip()


class ToolInDB(ToolCreate):
    trusted: bool = False
    status: Literal["pending", "approved", "rejected"] = "pending"
    createdAt: datetime = Field(default_factory=datetime.utcnow)


class GeminiChatRequest(BaseModel):
    history: Optional[list] = []
    message: Any = None
    tools: Optional[list] = []
