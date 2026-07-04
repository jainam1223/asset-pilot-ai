from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from ai_service.pipeline import ask
from app.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    try:
        result = await ask(
            body.query,
            chain=request.app.state.chain,
            schema_text=request.app.state.schema,
            role=body.role,
        )
        return ChatResponse(answer=result.answer, refused=result.refused)
    except Exception as e:
        logger.error(f"chat_request_failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
