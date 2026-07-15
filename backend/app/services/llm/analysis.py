"""LLM match analysis (spec §8).

The narrative **explains** the model outputs — it is never the source of the
probabilities (``not_a_probability_source`` is always true). Flow: cache lookup
by ``(fixture_id, model)`` respecting ``cache_ttl_seconds`` → daily token-budget
hard-stop (Redis key ``llm:budget:{YYYY-MM-DD}``) → one call to the configured
OpenAI-compatible endpoint → persist content + token usage + cost.

``generate_completion`` is the only function that touches the network; tests
monkeypatch it so no live key is needed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.ml.base import Method
from app.models.fixture import Fixture
from app.models.llm import LlmAnalysis, LlmConfig
from app.models.prediction import Prediction
from app.models.reference import League, Team

_LANGUAGE_NAME = {"ru": "Russian", "en": "English"}
_SYSTEM_PROMPT = (
    "You are a football analyst. In plain language, explain why the statistical "
    "models produced the probabilities below for this match. Interpret the numbers "
    "only — do NOT invent facts, injuries, or new probabilities. The models are the "
    "source of the probabilities, not you. Keep it to a short paragraph. "
    "Respond in {language}."
)


@dataclass
class AnalysisResult:
    status: str  # ok | budget_exhausted | disabled | no_data
    content: str | None = None
    model: str | None = None
    language: str = "en"
    cached: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    not_a_probability_source: bool = True
    resets_at: str | None = None


def _budget_key(now: datetime) -> str:
    return f"llm:budget:{now.astimezone(UTC):%Y-%m-%d}"


def _next_utc_midnight(now: datetime) -> datetime:
    return (now.astimezone(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


async def build_context(session: AsyncSession, fixture_id: uuid.UUID) -> str | None:
    """Structured feature+probability context (English). None when no consensus."""
    fx = await session.get(Fixture, fixture_id)
    if fx is None:
        return None
    league = await session.get(League, fx.league_id)
    home = await session.get(Team, fx.home_team_id)
    away = await session.get(Team, fx.away_team_id)
    if league is None or home is None or away is None:
        return None

    rows = (
        (
            await session.execute(
                select(Prediction)
                .where(Prediction.fixture_id == fixture_id, Prediction.market == "1x2")
                .order_by(Prediction.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[str, dict[str, float]] = defaultdict(dict)
    for p in rows:
        latest[p.method].setdefault(p.outcome, float(p.probability))

    consensus = latest.get(Method.consensus.value)
    if not consensus or not {"home", "draw", "away"} <= consensus.keys():
        return None
    market = latest.get(Method.market.value, {})

    lines = [
        f"Match: {home.name} vs {away.name} ({league.name}).",
        (
            f"Consensus 1X2 probabilities: home {consensus['home']:.0%}, "
            f"draw {consensus['draw']:.0%}, away {consensus['away']:.0%}."
        ),
    ]
    if "home" in market:
        edge = consensus["home"] - market["home"]
        lines.append(
            f"Market-implied home probability: {market['home']:.0%} "
            f"(consensus edge vs market: {edge:+.0%})."
        )
    for method, probs in latest.items():
        if method in (Method.consensus.value, Method.market.value):
            continue
        if {"home", "draw", "away"} <= probs.keys():
            lines.append(
                f"{method}: home {probs['home']:.0%}, draw {probs['draw']:.0%}, "
                f"away {probs['away']:.0%}."
            )
    return "\n".join(lines)


async def generate_completion(
    config: LlmConfig, *, system: str, user: str
) -> tuple[str, int, int]:
    """Call the configured OpenAI-compatible endpoint. Returns (content, in, out).
    Isolated so tests can monkeypatch it (no live API key needed)."""
    client = AsyncOpenAI(
        base_url=config.base_url or None,
        api_key=decrypt_secret(config.encrypted_key) if config.encrypted_key else "",
    )
    resp = await client.chat.completions.create(
        model=config.model,
        max_tokens=config.max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content or ""
    usage = resp.usage
    tokens_in = usage.prompt_tokens if usage else 0
    tokens_out = usage.completion_tokens if usage else 0
    return content, tokens_in, tokens_out


def _cost(config: LlmConfig, tokens_in: int, tokens_out: int) -> float:
    return round(
        tokens_in / 1000 * float(config.cost_per_1k_in)
        + tokens_out / 1000 * float(config.cost_per_1k_out),
        6,
    )


async def get_or_create_analysis(
    session: AsyncSession,
    redis: Redis,
    *,
    fixture_id: uuid.UUID,
    language: str,
    now: datetime | None = None,
) -> AnalysisResult:
    now = now or datetime.now(UTC)
    config = (
        await session.execute(select(LlmConfig).where(LlmConfig.singleton == "default"))
    ).scalar_one_or_none()
    if config is None or not config.is_enabled or not config.model:
        return AnalysisResult(status="disabled")

    # Cache: serve only a fresh entry (within cache_ttl_seconds).
    cached = (
        await session.execute(
            select(LlmAnalysis).where(
                LlmAnalysis.fixture_id == fixture_id, LlmAnalysis.model == config.model
            )
        )
    ).scalar_one_or_none()
    if cached is not None:
        age = (now - cached.created_at).total_seconds()
        if age < config.cache_ttl_seconds:
            return AnalysisResult(
                status="ok",
                content=cached.content,
                model=cached.model,
                language=cached.language,
                cached=True,
                tokens_in=cached.tokens_in,
                tokens_out=cached.tokens_out,
                cost=float(cached.cost),
            )

    # Budget hard-stop: refuse to start a new generation once the day is spent.
    used = int(await redis.get(_budget_key(now)) or 0)
    if used >= config.daily_token_budget:
        return AnalysisResult(
            status="budget_exhausted", resets_at=_next_utc_midnight(now).isoformat()
        )

    context = await build_context(session, fixture_id)
    if context is None:
        return AnalysisResult(status="no_data")

    lang_name = _LANGUAGE_NAME.get(language, "English")
    content, tokens_in, tokens_out = await generate_completion(
        config, system=_SYSTEM_PROMPT.format(language=lang_name), user=context
    )
    cost = _cost(config, tokens_in, tokens_out)

    # Account the tokens against today's budget (TTL to next UTC midnight).
    total = tokens_in + tokens_out
    new_used = int(await redis.incrby(_budget_key(now), total))
    if new_used == total:
        await redis.expireat(_budget_key(now), int(_next_utc_midnight(now).timestamp()))

    await session.execute(
        pg_insert(LlmAnalysis)
        .values(
            fixture_id=fixture_id,
            provider="openai_compatible",
            model=config.model,
            language=language,
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            created_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_llm_analysis_fixture_model",
            set_={
                "content": content,
                "language": language,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": cost,
                "created_at": now,
            },
        )
    )
    await session.flush()
    return AnalysisResult(
        status="ok",
        content=content,
        model=config.model,
        language=language,
        cached=False,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )
