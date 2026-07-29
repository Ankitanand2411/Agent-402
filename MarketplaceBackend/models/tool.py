from pydantic import BaseModel, Field
from typing import Optional, Literal, Any
from datetime import datetime


class ToolCreate(BaseModel):
    name: str
    description: str
    price: str
    targetUrl: Optional[str] = None
    parameters: Optional[dict] = None
    type: Literal["proxy", "code"] = "proxy"
    code: Optional[str] = None
    walletAddress: Optional[str] = None


class ToolInDB(ToolCreate):
    trusted: bool = False
    status: Literal["pending", "approved", "rejected"] = "pending"
    createdAt: datetime = Field(default_factory=datetime.utcnow)


class GeminiChatRequest(BaseModel):
    history: Optional[list] = []
    message: Any = None
    tools: Optional[list] = []


class ToolApproveRequest(BaseModel):
    pass  # no body needed


class ToolCallRequest(BaseModel):
    class Config:
        extra = "allow"
