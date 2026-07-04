from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str
    role: str


class ChatResponse(BaseModel):
    answer: str
    refused: bool
