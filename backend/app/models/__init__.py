"""ORM models. Importing this package registers every model on ``Base.metadata``."""

from app.models.audit_log import AuditLog
from app.models.backtester import BacktestFeature, Strategy
from app.models.email_verification_token import EmailVerificationToken
from app.models.fixture import Fixture, FixtureStats, FixtureStatus, Shot
from app.models.live import LiveUpdate, PushChannel, PushSubscription
from app.models.llm import LlmAnalysis, LlmConfig
from app.models.market import Odds
from app.models.model_registry import ModelRegistry, ModelRegistrySnapshot, ModelStatus
from app.models.prediction import ModelRun, Prediction, PredictionLive
from app.models.promo import (
    PromoBatch,
    PromoBatchStatus,
    PromoCode,
    PromoCodeStatus,
    PromoCodeType,
    PromoRedemption,
    PromoRedemptionStatus,
)
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
from app.models.tier import Subscription, SubscriptionSource, Tier
from app.models.user import User, UserRole, UserTier

__all__ = [
    "AuditLog",
    "BacktestFeature",
    "EmailVerificationToken",
    "Fixture",
    "FixtureStats",
    "FixtureStatus",
    "League",
    "LiveUpdate",
    "LlmAnalysis",
    "LlmConfig",
    "ModelRegistry",
    "ModelRegistrySnapshot",
    "ModelRun",
    "ModelStatus",
    "Odds",
    "Prediction",
    "PredictionLive",
    "PromoBatch",
    "PromoBatchStatus",
    "PromoCode",
    "PromoCodeStatus",
    "PromoCodeType",
    "PromoRedemption",
    "PromoRedemptionStatus",
    "ProviderAccount",
    "ProviderLeagueAlias",
    "ProviderRole",
    "ProviderTeamAlias",
    "PushChannel",
    "PushSubscription",
    "RatingElo",
    "RatingGlicko",
    "RefreshToken",
    "Shot",
    "Strategy",
    "Subscription",
    "SubscriptionSource",
    "Team",
    "Tier",
    "User",
    "UserRole",
    "UserTier",
]
