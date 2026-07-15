"""In-play Dixon-Coles: conditioning on current score + elapsed minute."""

from __future__ import annotations

from app.ml.base import Outcome
from app.ml.dixon_coles import in_play_one_x_two, in_play_score_matrix


def test_remaining_matrix_sums_to_one() -> None:
    matrix = in_play_score_matrix(1.6, 1.1, -0.05, minute=45)
    assert abs(matrix.sum() - 1.0) < 1e-6


def test_probs_sum_to_one() -> None:
    probs = in_play_one_x_two(1.6, 1.1, -0.05, home_score=1, away_score=0, minute=30)
    assert abs(sum(probs.values()) - 1.0) < 1e-6


def test_final_whistle_is_deterministic() -> None:
    # At minute 90 there are no goals left; the outcome is the current score.
    probs = in_play_one_x_two(1.6, 1.1, -0.05, home_score=2, away_score=1, minute=90)
    assert probs[Outcome.home] == 1.0
    assert probs[Outcome.draw] == 0.0
    assert probs[Outcome.away] == 0.0


def test_leading_late_beats_leading_early() -> None:
    # A one-goal lead is worth more with less time left for the opponent.
    early = in_play_one_x_two(1.5, 1.5, -0.05, home_score=1, away_score=0, minute=10)
    late = in_play_one_x_two(1.5, 1.5, -0.05, home_score=1, away_score=0, minute=80)
    assert late[Outcome.home] > early[Outcome.home]


def test_trailing_side_probability_shrinks_over_time() -> None:
    early = in_play_one_x_two(1.4, 1.4, -0.05, home_score=0, away_score=1, minute=15)
    late = in_play_one_x_two(1.4, 1.4, -0.05, home_score=0, away_score=1, minute=85)
    assert late[Outcome.home] < early[Outcome.home]
