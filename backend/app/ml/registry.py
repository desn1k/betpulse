"""Model registry service (governance §16).

Populated after each training run (``upsert_run``) and re-evaluated nightly
(``apply_champion_selection``). Every governance change snapshots the FULL
registry state first, so ``rollback_to_snapshot`` restores it atomically.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.registry_lock import lock_registry
from app.models.model_registry import ModelRegistry, ModelRegistrySnapshot, ModelStatus
from app.services.audit import record_event

CHAMPION_PROMOTED = "model.champion.promoted"
CHAMPION_DEMOTED = "model.champion.demoted"
REGISTRY_ROLLBACK = "model.registry.rollback"


class SnapshotHasManyChampions(Exception):
    """The snapshot names more than one champion (taken before the database
    enforced a single one); it cannot be applied as is."""


@dataclass(slots=True)
class MethodMetrics:
    accuracy_pct: float
    brier: float
    log_loss: float
    roi_vs_closing: float
    sample_count: int
    # The model version these metrics belong to (None: the method's latest row).
    version: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


async def upsert_run(
    session: AsyncSession,
    *,
    method: str,
    version: str,
    mlflow_run_id: str | None,
    sample_count: int,
    min_samples: int,
) -> None:
    stmt = (
        pg_insert(ModelRegistry)
        .values(
            method=method,
            version=version,
            mlflow_run_id=mlflow_run_id,
            sample_count=sample_count,
            min_samples=min_samples,
            status=ModelStatus.challenger,
            last_trained_at=_now(),
            display_weight=Decimal("0"),
        )
        .on_conflict_do_update(
            constraint="uq_registry_method_version",
            set_={
                "mlflow_run_id": mlflow_run_id,
                "sample_count": sample_count,
                "last_trained_at": _now(),
            },
        )
    )
    await session.execute(stmt)
    await session.flush()


def _row_to_dict(row: ModelRegistry) -> dict[str, Any]:
    return {
        "method": row.method,
        "version": row.version,
        "mlflow_run_id": row.mlflow_run_id,
        "accuracy_pct": None if row.accuracy_pct is None else float(row.accuracy_pct),
        "brier": None if row.brier is None else float(row.brier),
        "log_loss": None if row.log_loss is None else float(row.log_loss),
        "roi_vs_closing": None if row.roi_vs_closing is None else float(row.roi_vs_closing),
        "sample_count": row.sample_count,
        "status": row.status.value,
        "is_enabled": row.is_enabled,
        "is_visible": row.is_visible,
        "display_weight": float(row.display_weight),
        "min_samples": row.min_samples,
        "notes": row.notes,
    }


async def snapshot_registry(
    session: AsyncSession, *, reason: str, actor: str | None = None
) -> ModelRegistrySnapshot:
    rows = (await session.execute(select(ModelRegistry))).scalars().all()
    snapshot = ModelRegistrySnapshot(
        reason=reason, actor=actor, payload=[_row_to_dict(r) for r in rows]
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def demote_champions(
    session: AsyncSession, rows: Sequence[ModelRegistry], *, keep: ModelRegistry | None = None
) -> list[ModelRegistry]:
    """Demote every champion row but ``keep`` and flush, so the old champion is
    gone from the database **before** a new one is promoted (the single-champion
    index would reject the reverse order; the ORM orders UPDATEs by key, not by
    assignment, so the flush is what guarantees it). Returns the demoted rows."""
    demoted = [r for r in rows if r.status == ModelStatus.champion and r is not keep]
    for row in demoted:
        row.status = ModelStatus.challenger
    if demoted:
        await session.flush()
    return demoted


async def rollback_to_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> None:
    """Restore the full registry state captured in a snapshot, atomically.

    Takes the registry lock (waits at most ``registry_lock_timeout_ms``). Every
    current champion row the snapshot does not name as champion is demoted first,
    including a version trained after the snapshot."""
    snapshot = await session.get(ModelRegistrySnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError("snapshot not found")
    await lock_registry(session)

    current = (await session.execute(select(ModelRegistry))).scalars().all()
    by_key = {(r.method, r.version): r for r in current}
    champions = [
        by_key[(i["method"], i["version"])]
        for i in snapshot.payload
        if i["status"] == ModelStatus.champion.value and (i["method"], i["version"]) in by_key
    ]
    if len(champions) > 1:
        raise SnapshotHasManyChampions
    # After this, the only possible champion is the snapshot's own (none if the
    # snapshot had none: a row created later must not keep the title either).
    await demote_champions(session, current, keep=champions[0] if champions else None)
    for item in snapshot.payload:
        row = by_key.get((item["method"], item["version"]))
        if row is None:
            continue
        row.status = ModelStatus(item["status"])
        row.is_enabled = item["is_enabled"]
        row.is_visible = item["is_visible"]
        row.display_weight = Decimal(str(item["display_weight"]))
        row.accuracy_pct = (
            None if item["accuracy_pct"] is None else Decimal(str(item["accuracy_pct"]))
        )
    await record_event(session, action=REGISTRY_ROLLBACK, target=str(snapshot_id))
    await session.flush()


def latest_row_per_method(rows: list[ModelRegistry]) -> dict[str, ModelRegistry]:
    """Each method's current registry row: the most recently trained version
    (ties broken by version). Older versions stay for history/rollback but are
    never shown or scored alongside the current one."""
    epoch = datetime.min.replace(tzinfo=UTC)
    out: dict[str, ModelRegistry] = {}
    for row in rows:
        current = out.get(row.method)
        if current is None or (row.last_trained_at or epoch, row.version) > (
            current.last_trained_at or epoch,
            current.version,
        ):
            out[row.method] = row
    return out


async def current_registry_rows(session: AsyncSession) -> list[ModelRegistry]:
    """Current row per method, plus any champion row (a champion is current by
    definition, even if a newer challenger version exists)."""
    rows = list((await session.execute(select(ModelRegistry))).scalars().all())
    current = latest_row_per_method(rows)
    champions = [r for r in rows if r.status == ModelStatus.champion]
    for row in champions:
        current[row.method] = row
    return list(current.values())


def _rows_for(
    all_rows: list[ModelRegistry], metrics_by_method: dict[str, MethodMetrics]
) -> dict[str, ModelRegistry]:
    """The registry row each method's metrics belong to: the evaluated version
    when the metrics name one, else the method's most recently trained row."""
    latest = latest_row_per_method(all_rows)
    out: dict[str, ModelRegistry] = {}
    for method, metrics in metrics_by_method.items():
        if metrics.version is None:
            if method in latest:
                out[method] = latest[method]
            continue
        match = next(
            (r for r in all_rows if r.method == method and r.version == metrics.version), None
        )
        if match is not None:
            out[method] = match
    return out


def _softmax_weights(accuracies: dict[str, float]) -> dict[str, float]:
    if not accuracies:
        return {}
    mx = max(accuracies.values())
    exps = {m: math.exp((a - mx) / 10.0) for m, a in accuracies.items()}
    total = sum(exps.values())
    return {m: round(100.0 * e / total, 2) for m, e in exps.items()}


def _finite(m: MethodMetrics) -> bool:
    return all(math.isfinite(v) for v in (m.brier, m.log_loss, m.accuracy_pct, m.roi_vs_closing))


def _decimal(value: float, digits: int) -> Decimal | None:
    return Decimal(str(round(value, digits))) if math.isfinite(value) else None


async def apply_champion_selection(
    session: AsyncSession,
    metrics_by_method: dict[str, MethodMetrics],
    *,
    weight_mode: str = "auto",
    min_samples: int = 300,
    min_brier_improvement: float = 0.002,
    actor: str = "system",
) -> str | None:
    """Store the evaluated metrics on each method's evaluated version, apply the
    champion rule, and set consensus weights. Idempotent: no change → no
    snapshot and no audit entry. Returns the champion method (or None).

    The rule (``metrics_by_method`` must come from one evaluation that scored
    every method on the same fixtures, see :func:`app.ml.evaluation.
    compute_rolling_metrics`):

    1. **eligible** = registry row present and enabled, every metric finite
       (a failed evaluation never competes), ``sample_count >= min_samples``;
    2. the best eligible method has the lowest **Brier**, then log loss, then
       name (``accuracy_pct`` is display-only);
    3. it replaces a current, eligible champion only if its Brier is at least
       ``min_brier_improvement`` lower — smaller gaps are noise;
    4. the incumbent is identified by **method**: an older champion version is
       represented by its method's evaluated (newest) version, which takes over
       the champion status when the method keeps the title;
    5. with no champion, or a champion whose method is not eligible in this
       evaluation, the best eligible method becomes champion;
    6. with no eligible method nothing changes.
    """
    all_rows = list((await session.execute(select(ModelRegistry))).scalars().all())
    rows = _rows_for(all_rows, metrics_by_method)
    for method, m in metrics_by_method.items():
        row = rows.get(method)
        if row is None:
            continue
        row.accuracy_pct = _decimal(m.accuracy_pct, 2)
        row.brier = _decimal(m.brier, 6)
        row.log_loss = _decimal(m.log_loss, 6)
        row.roi_vs_closing = _decimal(m.roi_vs_closing, 4)
        row.sample_count = m.sample_count
        row.last_evaluated_at = _now()

    eligible = {
        method: m
        for method, m in metrics_by_method.items()
        if rows.get(method) is not None
        and rows[method].is_enabled
        and _finite(m)
        and m.sample_count >= min_samples
    }
    if not eligible:
        await session.flush()
        return None

    best = min(eligible, key=lambda k: (eligible[k].brier, eligible[k].log_loss, k))
    # Any champion row counts — including another version of the same method.
    champion_rows = [r for r in all_rows if r.status == ModelStatus.champion]
    # The incumbent is the champion's *method*: after a retrain the champion
    # row is usually an older version while the evaluation scored the newest
    # one, and the margin must still protect it.
    incumbent = next((r.method for r in champion_rows if r.method in eligible), None)
    winner = best
    if incumbent is not None and incumbent != best:
        margin = eligible[incumbent].brier - eligible[best].brier
        if margin < min_brier_improvement:
            winner = incumbent  # not a meaningful improvement

    if champion_rows != [rows[winner]]:
        await snapshot_registry(session, reason="champion_reeval", actor=actor)
        for row in await demote_champions(session, champion_rows, keep=rows[winner]):
            await record_event(session, action=CHAMPION_DEMOTED, target=row.method)
        rows[winner].status = ModelStatus.champion
        await record_event(
            session,
            action=CHAMPION_PROMOTED,
            target=winner,
            meta={
                "version": rows[winner].version,
                "brier": eligible[winner].brier,
                "sample_count": eligible[winner].sample_count,
            },
        )

    if weight_mode == "auto":
        weights = _softmax_weights({m: metrics_by_method[m].accuracy_pct for m in eligible})
        weighted = {id(row) for row in rows.values()}
        for row in all_rows:
            # Older versions of an evaluated method carry no weight.
            if id(row) in weighted:
                row.display_weight = Decimal(str(weights.get(row.method, 0.0)))
            elif row.method in rows:
                row.display_weight = Decimal("0")

    await session.flush()
    return winner
