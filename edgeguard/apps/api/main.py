from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.routes import router as main_router
from apps.api.routes_config import router as config_router
from apps.api.routes_reconcile import router as reconcile_router
from apps.api.routes_retail import router as retail_router
from src.core.config import get_settings
from src.core.logger import configure_logging, logger
from src.pipeline.manager import get_pipeline_manager
from src.store.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    init_db(settings.db_url)

    manager = get_pipeline_manager(settings)
    if settings.run_pipeline_on_startup:
        logger.info("Starting pipeline runners from API startup")
        manager.start_all()

    try:
        yield
    finally:
        manager.stop_all()


app = FastAPI(title="EdgeGuard API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    settings = get_settings()
    if not settings.api_key:
        return await call_next(request)

    path = request.url.path
    if (
        path == "/health"
        or path == "/openapi.json"
        or path.startswith("/docs")
        or path.startswith("/redoc")
    ):
        return await call_next(request)

    if request.headers.get("x-api-key") != settings.api_key:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


app.include_router(main_router)
app.include_router(retail_router)
app.include_router(config_router)
app.include_router(reconcile_router)
