"""
main.py
-------
Application entry point and factory.
"""

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
from app.models.research_report import ResearchReport
from app.database.session import Base, engine


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.APP_ENV == "development" else None,
        redoc_url="/redoc" if settings.APP_ENV == "development" else None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
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

    @application.get("/health", tags=["Health"], include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "version": settings.APP_VERSION}

    return application


app = create_app()
