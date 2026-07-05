from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    # No role field: the endpoint itself (/chat/it-admin vs /chat/employee)
    # determines the role. A client can never claim a role in the body —
    # that was a privilege-escalation hole (send role=it_admin, get admin
    # access). Which endpoint you hit is the only thing that decides scope.
    #
    # max_length caps how much text gets forwarded into the LLM prompt —
    # a real question never needs more than this; it's a cheap guard
    # against someone pasting in a huge blob of text.
    query: str = Field(max_length=500)


class ChatResponse(BaseModel):
    answer: str
    refused: bool
