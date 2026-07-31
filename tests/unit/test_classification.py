"""Classification ingress guard tests (PM0.B)."""

from __future__ import annotations

import pytest

from product_factory.domain.errors import UnsafeOperationError
from product_factory.policy.classification import assert_ingress_allowed, classify_text


def test_clean_text_allowed() -> None:
    decision = classify_text("public documentation about APIs")
    assert decision.outcome == "allow"
    assert decision.label == "internal"


def test_private_key_blocked() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    with pytest.raises(UnsafeOperationError):
        assert_ingress_allowed(text, source="test")


def test_aws_key_blocked() -> None:
    with pytest.raises(UnsafeOperationError):
        assert_ingress_allowed("AKIAIOSFODNN7EXAMPLE", source="test")
