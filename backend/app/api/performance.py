"""Public model-performance endpoint (spec §5 trust surface).

Serves rolling out-of-sample metrics straight from ``model_registry`` — never
recomputed on request. No authentication. If no evaluation has run yet it returns
a clear ``no_evaluation_yet`` status rather than empty arrays or zeros.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.models.model_registry import ModelRegistry, ModelStatus

router = APIRouter(tags=["performance"])


@router.get("/performance")
async def performance(session: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(ModelRegistry)
                .where(ModelRegistry.is_visible.is_(True))
                .order_by(ModelRegistry.accuracy_pct.desc().nullslast())
            )
        )
        .scalars()
        .all()
    )

    evaluated = [r.last_evaluated_at for r in rows if r.last_evaluated_at is not None]
    if not rows or not evaluated:
        return {"status": "no_evaluation_yet"}

    champion = next((r.method for r in rows if r.status == ModelStatus.champion), None)
    return {
        "status": "ok",
        "evaluated_at": max(evaluated).isoformat(),
        "champion": champion,
        "methods": [
            {
                "method": r.method,
                "status": r.status.value,
                "accuracy_pct": None if r.accuracy_pct is None else float(r.accuracy_pct),
                "brier": None if r.brier is None else float(r.brier),
                "log_loss": None if r.log_loss is None else float(r.log_loss),
                "roi_vs_closing": None if r.roi_vs_closing is None else float(r.roi_vs_closing),
                "sample_count": r.sample_count,
                "display_weight": float(r.display_weight),
                "is_champion": r.status == ModelStatus.champion,
            }
            for r in rows
        ],
    }
