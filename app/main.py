from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from ai_service.db import close_pool, warm_pool
from ai_service.providers import build_provider_chain
from ai_service.schema import load_schema
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.chain = build_provider_chain()
    app.state.schema = load_schema()
    await warm_pool()
    yield
    await close_pool()


app = FastAPI(title="IT Asset Chatbot", lifespan=lifespan)
app.include_router(router)
