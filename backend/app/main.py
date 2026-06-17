"""
main.py
-------
Application entry point and factory.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routes.auth import router as auth_router
from app.routes.organization import router as organization_router
from app.routes.workspaces import router as workspaces_router
from app.routes.research import router as research_router
from app.routes.watchlist import router as watchlist_router
from app.routes.admin import router as admin_router
from app.routes.analyze import router as analyze_router
from app.routes.documents import router as documents_router
from app.routes.chat import router as chat_router
from app.models.research_report import ResearchReport
from app.database.session import Base, engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan.

    On startup: if the document knowledge base collection is empty, ingest the
    bundled earnings reports into ChromaDB. This is wrapped to be non-fatal —
    a knowledge-base failure must never prevent the API from starting.
    """
    try:
        from app.services.document_service import ensure_ingested_on_startup

        ensure_ingested_on_startup()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Startup document ingestion failed (non-fatal): %s", exc)
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.APP_ENV == "development" else None,
        redoc_url="/redoc" if settings.APP_ENV == "development" else None,
        lifespan=lifespan,
    )

    cors_kwargs: dict = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "allow_origins": ["*"],
    }
    if settings.APP_ENV == "development":
        # Vite may bind to 5173, 5174, etc. — allow any local dev origin.
        cors_kwargs["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    else:
        cors_kwargs["allow_origins"] = settings.ALLOWED_ORIGINS

    application.add_middleware(CORSMiddleware, **cors_kwargs)
    Base.metadata.create_all(bind=engine)

    # Phase 1
    application.include_router(auth_router)
    application.include_router(organization_router)

    # Phase 2
    application.include_router(workspaces_router)
    application.include_router(research_router)
    application.include_router(watchlist_router)
    application.include_router(admin_router)

    # Phase 3A
    application.include_router(analyze_router)

    # Phase 3C: Document Knowledge Base
    application.include_router(documents_router)

    # Chat: Research Assistant
    application.include_router(chat_router)

    @application.get("/health", tags=["Health"], include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "version": settings.APP_VERSION}

    return application


app = create_app()
