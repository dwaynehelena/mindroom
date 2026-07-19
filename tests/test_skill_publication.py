"""Tests for immutable trusted-skill persistence and publication."""

# ruff: noqa: ANN001, ANN201, ANN202, D103, EM101, TRY003

from __future__ import annotations

import pytest
import pytest_asyncio

from mindroom.skill_publication import SkillPublicationError, TrustedSkillPublisher, TrustedSkillStore
from mindroom.skill_registry import (
    SandboxEvidence,
    ScanPolicy,
    SkillTrustRegistry,
    manifest_digest,
    translate_openclaw_skill,
)

pytestmark = pytest.mark.asyncio


def _signed():
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = translate_openclaw_skill(
        {"id": "calendar.read", "version": "1", "command": "bin/read"},
    )
    registry.register(manifest)
    registry.scan(manifest.skill_id, manifest.version, ScanPolicy(frozenset(), frozenset(), frozenset()))
    registry.record_sandbox(
        manifest.skill_id,
        manifest.version,
        SandboxEvidence("runner", manifest_digest(manifest), True, True, True, 2),
    )
    return registry, registry.sign(manifest.skill_id, manifest.version)


@pytest_asyncio.fixture
async def store(tmp_path):
    value = TrustedSkillStore(tmp_path / "skills.db")
    await value.open()
    yield value
    await value.close()


async def test_persists_only_verified_signed_evidence(store) -> None:
    registry, entry = _signed()
    await store.add_verified(entry, registry)
    assert await store.get("calendar.read", "1") == entry
    unsigned_registry = SkillTrustRegistry(b"u" * 32)
    with pytest.raises(SkillPublicationError, match="verified signed"):
        await store.add_verified(unsigned_registry.register(entry.manifest), unsigned_registry)


async def test_two_runtime_canary_and_stable_receipts(store) -> None:
    registry, entry = _signed()
    await store.add_verified(entry, registry)

    async def publish(_entry, key):
        return f"receipt:{key}"

    async def rollback(_entry, _receipt):
        raise AssertionError("rollback should not run")

    publisher = TrustedSkillPublisher(
        store,
        dict.fromkeys(("openclaw", "hermes"), publish),
        dict.fromkeys(("openclaw", "hermes"), rollback),
        dict.fromkeys(("openclaw", "hermes"), publish),
        dict.fromkeys(("openclaw", "hermes"), rollback),
    )
    assert {status for status, _receipt in (await publisher.canary("calendar.read", "1")).values()} == {"canary"}
    assert {status for status, _receipt in (await publisher.stabilize("calendar.read", "1")).values()} == {"stable"}


async def test_partial_failure_rolls_back_and_blocks_stable(store) -> None:
    registry, entry = _signed()
    await store.add_verified(entry, registry)
    rolled_back = []

    async def openclaw(_entry, _key):
        return "openclaw-receipt"

    async def hermes(_entry, _key):
        raise RuntimeError("private runtime failure")

    async def rollback(_entry, receipt):
        rolled_back.append(receipt)

    publisher = TrustedSkillPublisher(
        store,
        {"openclaw": openclaw, "hermes": hermes},
        dict.fromkeys(("openclaw", "hermes"), rollback),
        dict.fromkeys(("openclaw", "hermes"), openclaw),
        dict.fromkeys(("openclaw", "hermes"), rollback),
    )
    with pytest.raises(SkillPublicationError, match="rolled back"):
        await publisher.canary("calendar.read", "1")
    assert rolled_back == ["openclaw-receipt"]
    assert (await store.receipts("calendar.read", "1"))["openclaw"][0] == "rolled_back"
    with pytest.raises(SkillPublicationError, match="requires"):
        await publisher.stabilize("calendar.read", "1")


async def test_partial_stable_installation_rolls_back_to_canary(store) -> None:
    registry, entry = _signed()
    await store.add_verified(entry, registry)
    stable_rollbacks = []

    async def canary(_entry, key):
        return f"canary:{key}"

    async def stable(entry, key):
        if ":hermes:" in key:
            raise RuntimeError("private install detail")
        return f"stable:{entry.manifest.skill_id}"

    async def rollback(_entry, receipt):
        stable_rollbacks.append(receipt)

    async def unused_rollback(_entry, _receipt):
        raise AssertionError("canary rollback should not run")

    publisher = TrustedSkillPublisher(
        store,
        dict.fromkeys(("openclaw", "hermes"), canary),
        dict.fromkeys(("openclaw", "hermes"), unused_rollback),
        dict.fromkeys(("openclaw", "hermes"), stable),
        dict.fromkeys(("openclaw", "hermes"), rollback),
    )
    canary_receipts = await publisher.canary("calendar.read", "1")
    with pytest.raises(SkillPublicationError, match="rolled back"):
        await publisher.stabilize("calendar.read", "1")
    assert stable_rollbacks == ["stable:calendar.read"]
    assert await store.receipts("calendar.read", "1") == canary_receipts


async def test_stable_installation_resumes_mixed_receipts_without_rolling_back_prior_success(store) -> None:
    registry, entry = _signed()
    await store.add_verified(entry, registry)

    async def publish(_entry, key):
        return f"receipt:{key}"

    async def rollback(_entry, _receipt):
        raise AssertionError("a prior stable installation must not be rolled back")

    publisher = TrustedSkillPublisher(
        store,
        dict.fromkeys(("openclaw", "hermes"), publish),
        dict.fromkeys(("openclaw", "hermes"), rollback),
        dict.fromkeys(("openclaw", "hermes"), publish),
        dict.fromkeys(("openclaw", "hermes"), rollback),
    )
    await publisher.canary("calendar.read", "1")
    await store.settle(
        "calendar.read",
        "1",
        "openclaw",
        status="stable",
        receipt="prior-stable-receipt",
        expected=("canary",),
    )
    receipts = await publisher.stabilize("calendar.read", "1")
    assert {status for status, _receipt in receipts.values()} == {"stable"}
    assert receipts["openclaw"][1] == "prior-stable-receipt"


async def test_interrupted_publication_becomes_uncertain(store) -> None:
    registry, entry = _signed()
    await store.add_verified(entry, registry)
    await store.begin("calendar.read", "1", "openclaw")
    assert await store.recover_uncertain() == 1
    assert (await store.receipts("calendar.read", "1"))["openclaw"] == ("uncertain", None)
