"""``python -m app.cli vapid-keys`` prints a fresh Web Push key pair as the two
.env lines the push sender and the browser subscription expect
(docs/DEPLOY_VPS.md, secrets)."""

from __future__ import annotations

import base64
import json

import pytest
from app.cli import main
from app.services.live.push import build_vapid_jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _keys(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    assert main(["vapid-keys"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [
        "WEBPUSH_VAPID_PRIVATE_KEY",
        "WEBPUSH_VAPID_PUBLIC_KEY",
    ]
    return dict(line.split("=", 1) for line in lines)


def test_prints_a_matching_key_pair_the_sender_can_sign_with(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = _keys(capsys)
    private, public = keys["WEBPUSH_VAPID_PRIVATE_KEY"], keys["WEBPUSH_VAPID_PUBLIC_KEY"]
    assert "=" not in private and "=" not in public  # unpadded base64url
    assert len(_b64url(private)) == 32
    point = _b64url(public)
    assert len(point) == 65 and point[0] == 4  # uncompressed P-256 point

    jwt = build_vapid_jwt(private, subject="mailto:ops@example.test", audience="https://push.test")
    header, claims, signature = jwt.split(".")
    assert json.loads(_b64url(header))["alg"] == "ES256"
    raw = _b64url(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    verifier.verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_every_run_is_a_new_pair(capsys: pytest.CaptureFixture[str]) -> None:
    assert _keys(capsys) != _keys(capsys)
