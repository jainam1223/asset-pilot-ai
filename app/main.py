from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from ai_service.db import close_pool, warm_pool
from ai_service.errors import MALFORMED_REQUEST_MESSAGE
from ai_service.providers import build_provider_chain
from ai_service.schema import build_role_schemas, load_schema
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.chain = build_provider_chain()
    # Sliced once per role here instead of per request — the sliced
    # output never changes for the life of the process (see
    # build_role_schemas' docstring).
    app.state.schemas = build_role_schemas(load_schema())
    await warm_pool()
    yield
    await close_pool()


app = FastAPI(title="IT Asset Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(RequestValidationError)
async def malformed_request_handler(request: Request, exc: RequestValidationError):
    # Covers both unparseable JSON bodies and missing/invalid fields
    # (e.g. no "query" key). Without this, FastAPI's default handler
    # returns its raw parser detail (field paths, "Expecting ','
    # delimiter", etc.) straight to the client — an internal detail
    # leak, same class of problem as leaking a stack trace or SQL error.
    #
    # status_code=200, not 422: every other refusal in this API (scope,
    # destructive, empty question, off-topic) is a normal 200 with
    # refused=true — a non-2xx status here is the one thing that could
    # still surface "422 Unprocessable Entity" as raw HTTP status text
    # in some client, even though the JSON body itself was already clean.
    logger.warning(f"request_validation_failed: {exc}")
    return JSONResponse(
        status_code=200,
        content={"answer": MALFORMED_REQUEST_MESSAGE, "refused": True},
    )
