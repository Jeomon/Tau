"""Headings and row labels for the /login and /logout pickers.

``OAuthSelector`` derived its heading from ``mode`` alone, so all three /login
screens — pick an auth method, then pick a provider — printed the same
"Configure provider:" regardless of what they were actually asking for.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from tau.modes.interactive.commands import auth as auth_cmd
from tau.modes.interactive.components.oauth_selector import OAuthProviderItem, OAuthSelector
from tau.tui.utils import strip_ansi

# (id, name, is_oauth, needs_key) — the shape _all_providers() returns.
_PROVIDERS = [
    ("copilot", "GitHub Copilot", True, True),
    ("anthropic", "Anthropic", False, True),
]


def _rendered(selector: OAuthSelector, width: int = 78) -> list[str]:
    return [strip_ansi(line).rstrip() for line in selector.render(width)]


def _selector(**kwargs) -> OAuthSelector:
    kwargs.setdefault("mode", "login")
    kwargs.setdefault("providers", [OAuthProviderItem(id="p", name="Provider")])
    kwargs.setdefault("on_select", Mock())
    kwargs.setdefault("on_cancel", Mock())
    return OAuthSelector(**kwargs)


class TestHeading:
    def test_a_caller_supplied_title_is_used(self) -> None:
        selector = _selector(title="Select authentication method:")

        assert _rendered(selector)[0] == "  Select authentication method:"

    def test_login_falls_back_to_the_mode_wording(self) -> None:
        """Callers that name no title keep the old heading."""
        assert _rendered(_selector(mode="login"))[0] == "  Configure provider:"

    def test_logout_falls_back_to_the_mode_wording(self) -> None:
        assert _rendered(_selector(mode="logout"))[0] == "  Logout from provider:"


class TestFlowCopy:
    """Each screen names what it is asking for, captured at the call site."""

    def _ctx(self) -> tuple[SimpleNamespace, dict]:
        opened: dict = {}
        layout = SimpleNamespace(
            open_oauth_selector=lambda mode, items, on_select, on_cancel, title=None: opened.update(
                mode=mode, items=items, title=title
            ),
            open_prompt=Mock(),
        )
        ctx = SimpleNamespace(
            layout=layout,
            notify=Mock(),
            runtime=SimpleNamespace(),
            on_palette_refresh=None,
        )
        return ctx, opened

    def test_login_step_one_asks_for_an_authentication_method(self) -> None:
        ctx, opened = self._ctx()

        with patch.object(auth_cmd, "_all_providers", return_value=_PROVIDERS):
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]

        assert opened["title"] == "Select authentication method:"
        assert [item.name for item in opened["items"]] == [
            "Sign in with an account",
            "Sign in with an API key",
        ]

    def test_api_key_step_asks_which_provider_the_key_is_for(self) -> None:
        ctx, opened = self._ctx()
        auth_manager = SimpleNamespace(list=lambda: [])

        with (
            patch.object(auth_cmd, "_all_providers", return_value=_PROVIDERS),
            patch("tau.inference.api.text.service.TextLLM._auth_manager", auth_manager),
        ):
            auth_cmd.open_api_key_provider_selector(ctx)  # type: ignore[arg-type]

        assert opened["title"] == "Select a provider for the API key:"
        assert [item.name for item in opened["items"]] == ["Anthropic"]

    def test_logout_asks_which_account_to_sign_out_of(self) -> None:
        ctx, opened = self._ctx()
        auth_manager = SimpleNamespace(reload=lambda: None, list=lambda: ["copilot"])

        with (
            patch.object(auth_cmd, "_all_providers", return_value=_PROVIDERS),
            patch("tau.inference.api.text.service.TextLLM._auth_manager", auth_manager),
        ):
            auth_cmd.open_logout_selector(ctx)  # type: ignore[arg-type]

        assert opened["mode"] == "logout"
        assert opened["title"] == "Select an account to sign out of:"
        assert [item.name for item in opened["items"]] == ["GitHub Copilot"]

    def test_single_auth_style_skips_the_method_screen(self) -> None:
        """With no OAuth provider available there is nothing to choose between."""
        ctx, opened = self._ctx()
        auth_manager = SimpleNamespace(list=lambda: [])
        api_only = [("anthropic", "Anthropic", False, True)]

        with (
            patch.object(auth_cmd, "_all_providers", return_value=api_only),
            patch("tau.inference.api.text.service.TextLLM._auth_manager", auth_manager),
        ):
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]

        assert opened["title"] == "Select a provider for the API key:"
