"""docs/DEPLOY_VPS.md stays true to the repository it runs.

The runbook is followed command by command on a server, so a renamed script,
an .env key that no longer exists or a CLI command that was removed would
break the day. These tests pin what can be checked mechanically: every script,
.env key and CLI command it names exists; every section reference resolves;
every item of HANDOFF's "First VPS launch" list is covered; path B lists the
legal preconditions; the day checklist points only at real steps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "DEPLOY_VPS.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)


def test_the_runbook_exists() -> None:
    assert DOC.is_file()


def test_every_script_it_names_exists() -> None:
    named = set(re.findall(r"scripts/[\w./-]+?\.sh", _doc()))
    assert named, "expected the runbook to name scripts"
    missing = sorted(s for s in named if not (REPO / s).is_file())
    assert missing == []


def test_every_env_key_it_sets_is_in_env_example() -> None:
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    known = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", example, flags=re.M))
    keys: set[str] = set()
    for block in _code_blocks(_doc()):
        keys |= set(re.findall(r"^([A-Z][A-Z0-9_]+)=", block, flags=re.M))
    # Shell variables of the runbook's own commands, not .env keys.
    keys -= {"IMAGE_TAG", "RELEASE_DIGESTS", "LLM_HOST", "MSYS2_ARG_CONV_EXCL"}
    assert keys, "expected .env lines in the runbook"
    assert sorted(keys - known) == []


@pytest.mark.parametrize(
    ("module", "source"),
    [("app.cli", "backend/app/cli.py"), ("app.bootstrap", "backend/app/bootstrap.py")],
)
def test_every_cli_command_it_runs_exists(module: str, source: str) -> None:
    commands = set(re.findall(rf"python -m {re.escape(module)} ([a-z0-9-]+)", _doc()))
    defined = set(
        re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', (REPO / source).read_text(encoding="utf-8"))
    )
    defined |= {"bootstrap-history", "verify-history"}  # added in a loop in app.cli
    assert sorted(commands - defined) == []


def test_every_section_reference_resolves() -> None:
    text = _doc()
    headings = set(re.findall(r"^#{2,3} (\d+(?:\.\d+)?)[. ]", text, flags=re.M))
    refs = set(re.findall(r"§(\d+(?:\.\d+)?)", text))
    assert refs, "expected section references"
    assert sorted(refs - headings) == []


# HANDOFF §9i "First VPS launch: items no rehearsal could verify" -> what the
# runbook must contain for each.
FIRST_VPS_ITEMS = {
    "ACME on the real domain": ["curl -I https://", "CADDY_TRIAL_TLS"],
    "`.env` permissions": ["chmod 600 .env", "stat -c '%a %U' .env"],
    "Listening sockets": ["ss -tlnp"],
    "GHCR login": ["docker login ghcr.io", "read:packages"],
    "MLflow over an SSH tunnel": ["ssh -L 5001:127.0.0.1:5001", "alpine/socat"],
    "Web Push to Mozilla": ["Firefox", "updates.push.services.mozilla.com"],
    "Automatic rollback, provoked once": ["docker stop betpulse-api-1", "exit 2"],
    "Real client addresses for IPv4 and IPv6": [
        "scripts/diagnose-client-ip.sh",
        "IPv6 real client seen: yes",
    ],
    "Refresh replay subnet binding": ["subnet_mismatch", "auth.token.refresh_replayed"],
    "Rollback to a previous release by digest": ["scripts/rollback.sh"],
}


def test_handoff_still_lists_those_items() -> None:
    handoff = (REPO / "HANDOFF.md").read_text(encoding="utf-8")
    section = handoff.split("### First VPS launch: items no rehearsal could verify", 1)[1]
    section = section.split("\n## ", 1)[0]
    for title in FIRST_VPS_ITEMS:
        assert f"**{title}" in section, title


@pytest.mark.parametrize(("item", "needles"), list(FIRST_VPS_ITEMS.items()))
def test_every_first_vps_item_is_covered(item: str, needles: list[str]) -> None:
    text = _doc()
    assert [n for n in needles if n not in text] == [], item


LEGAL_PRECONDITIONS = [
    "Роскомнадзор",
    "трансграничн",
    "Telegram",
    "FCM",
    "Apple",
    "Mozilla",
    "152-ФЗ",
    "config/legal.ts",
    "24 час",
    "согласи",
    "статус оператора",
    "уточнить у юриста",
]


def test_path_b_lists_the_legal_preconditions() -> None:
    text = _doc()
    path_b = text.split("## 9.", 1)[1].split("\n## ", 1)[0]
    assert [n for n in LEGAL_PRECONDITIONS if n.lower() not in path_b.lower()] == []
    assert "не является юридической консультацией" in path_b


def test_path_a_holds_only_the_operators_own_account() -> None:
    text = _doc()
    assert "только ваша собственная учётная запись" in text
    assert "персональные данные третьих лиц не обрабатываются" in text


def test_day_checklist_points_only_at_real_steps() -> None:
    text = _doc()
    checklist = text.split("## 10.", 1)[1]
    steps = re.findall(r"§(\d+\.\d+)", checklist)
    assert len(steps) >= 15, "the day checklist should walk every step of path A"
    headings = set(re.findall(r"^### (\d+\.\d+)[. ]", text, flags=re.M))
    assert sorted(set(steps) - headings) == []
