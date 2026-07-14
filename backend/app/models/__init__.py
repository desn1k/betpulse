"""ORM models. Importing this package registers every model on ``Base.metadata``."""

from app.models.audit_log import AuditLog
from app.models.email_verification_token import EmailVerificationToken
from app.models.fixture import Fixture, FixtureStats, FixtureStatus, Shot
from app.models.market import Odds
from app.models.model_registry import ModelRegistry, ModelRegistrySnapshot, ModelStatus
from app.models.prediction import ModelRun, Prediction, PredictionLive
from app.models.rating import RatingElo, RatingGlicko
from app.models.reference import (
    League,
    ProviderAccount,
    ProviderLeagueAlias,
    ProviderRole,
    ProviderTeamAlias,
    Team,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole

__all__ = [
    "AuditLog",
    "EmailVerificationToken",
    "Fixture",
    "FixtureStats",
    "FixtureStatus",
    "League",
    "ModelRegistry",
    "ModelRegistrySnapshot",
    "ModelRun",
    "ModelStatus",
    "Odds",
    "Prediction",
    "PredictionLive",
    "ProviderAccount",
    "ProviderLeagueAlias",
    "ProviderRole",
    "ProviderTeamAlias",
    "RatingElo",
    "RatingGlicko",
    "RefreshToken",
    "Shot",
    "Team",
    "User",
    "UserRole",
]
