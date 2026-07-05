from fastapi import APIRouter, Request

from ai_service.pipeline import ask
from app.schemas import ChatRequest, ChatResponse
from app.utils.response import success_response

router = APIRouter()


@router.get("/health")
async def health():
    return success_response(data={"status": "ok"})


@router.post("/chat/employee")
async def chat_employee(body: ChatRequest, request: Request):
    # Also covers manager — roles.TABLE_SCOPE gives manager the same
    # catalog-only access as employee today. Split into its own
    # endpoint later if manager scope ever diverges.
    #
    # No local try/except: ai_service.pipeline.ask() is designed to
    # never raise — every failure inside it becomes a refused=True
    # result with a human-facing message. Anything that still escapes
    # is a bug, and the app-level unhandled_exception_handler in
    # app.main turns it into the same envelope without leaking str(e).
    result = await ask(
        body.query,
        chain=request.app.state.chain,
        schema_text=request.app.state.schema,
        role="employee",
    )
    data = ChatResponse(answer=result.answer, refused=result.refused)
    return success_response(data=data.model_dump())


@router.post("/chat")
async def chat_admin(body: ChatRequest, request: Request):
    result = await ask(
        body.query,
        chain=request.app.state.chain,
        schema_text=request.app.state.schema,
        role="it_admin",
    )
    data = ChatResponse(answer=result.answer, refused=result.refused)
    return success_response(data=data.model_dump())
