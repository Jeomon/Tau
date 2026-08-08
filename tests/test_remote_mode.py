"""Tests for --mode remote: path resolution, limits, and the package surface.

The end-to-end socket behaviour lives in test_remote_server.py; this covers the
wiring that turns a runtime into a served socket, plus the lazy re-exports that
docs/remote.md tells callers to import.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.console.cli import _MODES
from tau.modes.remote.mode import resolve_socket_path
from tau.remote.server import RemoteServer


def _runtime(session_id: str | None = "abc123") -> SimpleNamespace:
    return SimpleNamespace(session_manager=SimpleNamespace(session_id=session_id))


class TestSocketPathResolution:
    def test_explicit_path_wins(self) -> None:
        resolved = resolve_socket_path(_runtime(), "/tmp/custom.sock")  # type: ignore[arg-type]

        assert resolved == Path("/tmp/custom.sock")

    def test_tilde_is_expanded(self) -> None:
        """click hands the string through verbatim; the shell may not have."""
        resolved = resolve_socket_path(_runtime(), "~/x.sock")  # type: ignore[arg-type]

        assert "~" not in str(resolved)
        assert resolved.is_absolute()

    def test_default_is_named_for_the_session(self) -> None:
        resolved = resolve_socket_path(_runtime("sess-1"), None)  # type: ignore[arg-type]

        assert resolved.name == "sess-1.sock"
        assert resolved.parent.name == "remote"

    def test_a_session_without_an_id_still_resolves(self) -> None:
        """An ephemeral run has no session id, and must still be servable."""
        resolved = resolve_socket_path(_runtime(None), None)  # type: ignore[arg-type]

        assert resolved.name == "session.sock"

    def test_the_default_path_fits_in_sun_path(self) -> None:
        """The limit that makes this path deliberately shallow."""
        resolved = resolve_socket_path(_runtime("06a772af-8a4d-7749-8000-2fe75d3be020"), None)  # type: ignore[arg-type]

        limit = 104 if sys.platform == "darwin" else 108
        assert len(str(resolved).encode()) < limit


@pytest.mark.asyncio
class TestPathLengthGuard:
    async def test_an_overlong_path_is_refused_with_a_clear_error(self, tmp_path: Path) -> None:
        """Binding one fails with a bare EINVAL, which names nothing."""
        server = RemoteServer(_runtime(), tmp_path / ("d" * 120) / "x.sock")  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="over this platform's"):
            await server.start()

    async def test_nothing_is_created_for_a_doomed_path(self, tmp_path: Path) -> None:
        """The check runs before mkdir, so a failure leaves no directory."""
        doomed = tmp_path / ("d" * 120) / "x.sock"
        server = RemoteServer(_runtime(), doomed)  # type: ignore[arg-type]

        with pytest.raises(ValueError):
            await server.start()

        assert not doomed.parent.exists()


class TestCliWiring:
    def test_remote_is_a_selectable_mode(self) -> None:
        assert "remote" in _MODES

    def test_the_socket_flag_exists(self) -> None:
        from tau.console.cli import cli

        names = {param.name for param in cli.params}
        assert "socket_path" in names


class TestLazyPackageExports:
    """``from tau.remote import RemoteServer`` is what the docs tell people to
    write, and it resolves through a module ``__getattr__`` so that importing
    the framing helpers does not drag in the RPC dispatcher. Every other test
    imports the submodules directly, so nothing else exercises it."""

    @pytest.mark.parametrize(
        "name", ["RemoteServer", "SocketInUseError", "RemoteClient", "RemoteDisconnected"]
    )
    def test_socket_halves_resolve_lazily(self, name: str) -> None:
        import tau.remote as remote

        assert getattr(remote, name).__name__ == name

    def test_eager_names_are_still_importable(self) -> None:
        from tau.remote import PROTOCOL_VERSION, encode_frame

        assert callable(encode_frame)
        assert isinstance(PROTOCOL_VERSION, int)

    def test_an_unknown_attribute_raises(self) -> None:
        import tau.remote as remote

        with pytest.raises(AttributeError, match="no attribute"):
            _ = remote.NoSuchThing

    def test_everything_in_all_resolves(self) -> None:
        """A name in __all__ that cannot be fetched is a broken promise."""
        import tau.remote as remote

        for name in remote.__all__:
            assert getattr(remote, name) is not None
