from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from ai_service.errors import UNEXPECTED_MESSAGE
from ai_service.pipeline import ask
from app.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/chat/employee", response_model=ChatResponse)
async def chat_employee(body: ChatRequest, request: Request):
    # Also covers manager — roles.TABLE_SCOPE gives manager the same
    # catalog-only access as employee today. Split into its own
    # endpoint later if manager scope ever diverges.
    try:
        result = await ask(
            body.query,
            chain=request.app.state.chain,
            schema_text=request.app.state.schemas["employee"],
            role="employee",
        )
        return ChatResponse(answer=result.answer, refused=result.refused)
    except Exception as e:
        # ai_service.pipeline.ask() is designed to never raise — every
        # failure inside it becomes a refused=True result with a
        # human-facing message. Reaching this handler means something
        # broke outside that contract (a bug, a crash), so the response
        # still must not leak str(e) to the client.
        logger.error(f"chat_request_failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"answer": UNEXPECTED_MESSAGE, "refused": True},
        )


@router.post("/chat", response_model=ChatResponse)
async def chat_admin(body: ChatRequest, request: Request):
    try:
        result = await ask(
            body.query,
            chain=request.app.state.chain,
            schema_text=request.app.state.schemas["it_admin"],
            role="it_admin",
        )
        return ChatResponse(answer=result.answer, refused=result.refused)
    except Exception as e:
        # ai_service.pipeline.ask() is designed to never raise — every
        # failure inside it becomes a refused=True result with a
        # human-facing message. Reaching this handler means something
        # broke outside that contract (a bug, a crash), so the response
        # still must not leak str(e) to the client.
        logger.error(f"chat_request_failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"answer": UNEXPECTED_MESSAGE, "refused": True},
        )
