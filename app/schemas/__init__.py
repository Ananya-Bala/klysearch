from app.schemas.user import SignupRequest, LoginRequest, TokenResponse, UserPublic
from app.schemas.organization import OrganizationPublic, OrganizationDetail
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate, WorkspacePublic
from app.schemas.research_query import QuerySubmit, QueryPublic
from app.schemas.watchlist import WatchlistItemCreate, WatchlistItemPublic
from app.schemas.admin import ActivityRecord

# Phase 3A
from app.schemas.research_report import (
    AnalyzeRequest,
    ReportPublic,
    ReportOutput,
    ExecutiveSummary,
    FundamentalData,
    TechnicalData,
    NewsSentiment,
    AnalystConsensus,
    ScenarioAnalysis,
    RiskAnalysis,
    TimingAnalysis,
    AIReport,
)

__all__ = [
    # Phase 1
    "SignupRequest", "LoginRequest", "TokenResponse", "UserPublic",
    "OrganizationPublic", "OrganizationDetail",
    # Phase 2
    "WorkspaceCreate", "WorkspaceUpdate", "WorkspacePublic",
    "QuerySubmit", "QueryPublic",
    "WatchlistItemCreate", "WatchlistItemPublic",
    "ActivityRecord",
    # Phase 3A
    "AnalyzeRequest", "ReportPublic", "ReportOutput",
    "ExecutiveSummary", "FundamentalData", "TechnicalData",
    "NewsSentiment", "AnalystConsensus", "ScenarioAnalysis",
    "RiskAnalysis", "TimingAnalysis", "AIReport",
]
