from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from ai_service.db import close_pool, warm_pool
from ai_service.providers import build_provider_chain
from ai_service.schema import load_schema
from app.exception_handlers import register_exception_handlers
from app.routes import router
from app.utils.request_context import RequestContextMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.chain = build_provider_chain()
    app.state.schema = load_schema()
    await warm_pool()
    yield
    await close_pool()


app = FastAPI(title="IT Asset Chatbot", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(router)
register_exception_handlers(app)
