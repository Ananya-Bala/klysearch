"""
models/__init__.py
------------------
Import ORDER matters for FK resolution:
    Organization → User → Workspace → ResearchQuery
    WatchlistItem, ResearchReport depend on User + Organization
"""

# Phase 1
from app.models.organization import Organization
from app.models.user import User, UserRole

# Phase 2
from app.models.workspace import Workspace
from app.models.research_query import ResearchQuery, QueryStatus
from app.models.watchlist import WatchlistItem

# Phase 3A
from app.models.research_report import ResearchReport, ReportStatus

__all__ = [
    # Phase 1
    "Organization", "User", "UserRole",
    # Phase 2
    "Workspace", "ResearchQuery", "QueryStatus", "WatchlistItem",
    # Phase 3A
    "ResearchReport", "ReportStatus",
]
