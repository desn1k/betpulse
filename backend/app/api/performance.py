"""Public model-performance endpoint (spec §5 trust surface).

Serves the rolling metrics stored in ``model_registry`` — never recomputed on
request. No authentication. If no evaluation has run yet it returns a clear
``no_evaluation_yet`` status rather than empty arrays or zeros.

Every response states how the numbers were obtained: ``evaluation_protocol`` =
``prequential_historical`` (each prediction made from matches strictly before
its kickoff, scored on past matches) and ``verified_out_of_sample`` = false —
they are a retrospective check, not a verified claim about future accuracy.
Only each method's current version is shown.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.ml.evaluation import EVALUATION_PROTOCOL
from app.ml.registry import current_registry_rows
from app.models.model_registry import ModelStatus

router = APIRouter(tags=["performance"])


@router.get("/performance")
async def performance(session: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    rows = sorted(
        (r for r in await current_registry_rows(session) if r.is_visible),
        key=lambda r: (r.accuracy_pct is None, -(r.accuracy_pct or 0), r.method),
    )

    evaluated = [r.last_evaluated_at for r in rows if r.last_evaluated_at is not None]
    if not rows or not evaluated:
        return {
            "status": "no_evaluation_yet",
            "evaluation_protocol": EVALUATION_PROTOCOL,
            "verified_out_of_sample": False,
        }

    champion = next((r.method for r in rows if r.status == ModelStatus.champion), None)
    return {
        "status": "ok",
        "evaluation_protocol": EVALUATION_PROTOCOL,
        "verified_out_of_sample": False,
        "evaluated_at": max(evaluated).isoformat(),
        "champion": champion,
        "methods": [
            {
                "method": r.method,
                "version": r.version,
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
