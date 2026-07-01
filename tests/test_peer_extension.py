from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from examples.extensions.peer.service import Peer, PeerNameConflictError
from examples.extensions.peer.types import PeerConfig


def test_auto_start_uses_unique_name_when_default_is_active(tmp_path) -> None:
    async def exercise() -> None:
        context = SimpleNamespace(cwd=tmp_path, model_id="test-model")
        first = Peer(PeerConfig(root=tmp_path, default_name="tau"))
        second = Peer(PeerConfig(root=tmp_path, default_name="tau"))

        try:
            await first.start(context)
            await second.start(context)

            assert first.name == "tau"
            assert second.name.startswith("tau-")
            assert second.joined
        finally:
            await second.stop()
            await first.stop()

    asyncio.run(exercise())


def test_explicit_join_keeps_name_collision_strict(tmp_path) -> None:
    async def exercise() -> None:
        context = SimpleNamespace(cwd=tmp_path, model_id="test-model")
        first = Peer(PeerConfig(root=tmp_path, auto_join=False))
        second = Peer(PeerConfig(root=tmp_path, auto_join=False))

        try:
            await first.join("tau", context)
            with pytest.raises(PeerNameConflictError, match="already active"):
                await second.join("tau", context)
        finally:
            await second.stop()
            await first.stop()

    asyncio.run(exercise())
