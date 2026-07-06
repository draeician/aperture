"""Shared fixtures: policy and submission factories."""

from __future__ import annotations

import pytest

from aperture.items import Provenance, SourceClass, Submission
from aperture.policy import Policy


@pytest.fixture
def policy_factory():
    def _make(**overrides):
        return Policy(**overrides)

    return _make


@pytest.fixture
def default_policy(policy_factory):
    return policy_factory()


@pytest.fixture
def submission_factory():
    def _make(source_class=SourceClass.scratch, content="hello", provenance=None, **overrides):
        if provenance is None:
            provenance = Provenance(submitted_by="test")
        return Submission(
            source_class=source_class,
            content=content,
            provenance=provenance,
            **overrides,
        )

    return _make
