from app.services.auth_service import signup, login, AuthError
from app.services.organization_service import get_organization_by_id, get_users_in_organization
from app.services.workspace_service import (
    create_workspace, list_workspaces, get_workspace,
    update_workspace, delete_workspace, WorkspaceError,
)
from app.services.research_service import (
    submit_query, get_query_history, get_query_by_id, ResearchError,
)
from app.services.watchlist_service import (
    add_to_watchlist, get_watchlist, remove_from_watchlist, WatchlistError,
)
from app.services.admin_service import (
    get_org_activity, get_user_activity, AdminError,
)

# Phase 3A
from app.services.market_data_service import fetch_market_data, MarketSnapshot
from app.services.news_service import fetch_news
from app.services.sentiment_service import analyze_news_sentiment
from app.services.ai_research_service import generate_research_report, AIResearchError
from app.services.analysis_service import (
    run_analysis, get_report_by_id, list_reports, AnalysisError,
)

__all__ = [
    # Phase 1
    "signup", "login", "AuthError",
    "get_organization_by_id", "get_users_in_organization",
    # Phase 2
    "create_workspace", "list_workspaces", "get_workspace",
    "update_workspace", "delete_workspace", "WorkspaceError",
    "submit_query", "get_query_history", "get_query_by_id", "ResearchError",
    "add_to_watchlist", "get_watchlist", "remove_from_watchlist", "WatchlistError",
    "get_org_activity", "get_user_activity", "AdminError",
    # Phase 3A
    "fetch_market_data", "MarketSnapshot",
    "fetch_news",
    "analyze_news_sentiment",
    "generate_research_report", "AIResearchError",
    "run_analysis", "get_report_by_id", "list_reports", "AnalysisError",
]
