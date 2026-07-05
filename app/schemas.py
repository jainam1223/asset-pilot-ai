from pydantic import BaseModel


class ChatRequest(BaseModel):
    # No role field: the endpoint itself (/chat/it-admin vs /chat/employee)
    # determines the role. A client can never claim a role in the body —
    # that was a privilege-escalation hole (send role=it_admin, get admin
    # access). Which endpoint you hit is the only thing that decides scope.
    query: str


class ChatResponse(BaseModel):
    answer: str
    refused: bool
