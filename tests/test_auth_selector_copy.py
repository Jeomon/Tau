"""Headings and row labels for the /login and /logout pickers.

``OAuthSelector`` derived its heading from ``mode`` alone, so all three /login
screens — pick an auth method, then pick a provider — printed the same
"Configure provider:" regardless of what they were actually asking for.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from tau.inference.provider.types import OAuthProvider
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


class _FakeOAuthProvider(OAuthProvider):
    """Concrete stand-in — open_oauth_provider_selector filters on isinstance."""

    def api(self, *args, **kwargs): ...
    async def login(self, *args, **kwargs): ...
    async def logout(self, *args, **kwargs): ...
    async def refresh_token(self, *args, **kwargs): ...
    def validate(self, *args, **kwargs):
        return True


class TestEscapeGoesBack:
    """Escape unwinds one step, the way it leaves a /settings submenu.

    Escape on a provider step used to abandon /login entirely, so a mis-picked
    auth method cost the whole flow.
    """

    def _screen(self) -> tuple[SimpleNamespace, dict]:
        """A ctx whose layout records the screen currently showing."""
        showing: dict = {}

        def open_selector(mode, items, on_select, on_cancel, title=None) -> None:
            showing.update(title=title, on_select=on_select, on_cancel=on_cancel)

        def open_prompt(label, on_commit, on_cancel, secret=False) -> None:
            showing.update(title=label, on_select=on_commit, on_cancel=on_cancel)

        ctx = SimpleNamespace(
            layout=SimpleNamespace(open_oauth_selector=open_selector, open_prompt=open_prompt),
            notify=Mock(),
            runtime=SimpleNamespace(),
            on_palette_refresh=None,
        )
        return ctx, showing

    def _patches(self, providers=_PROVIDERS, oauth_registry=()):
        auth_manager = SimpleNamespace(list=lambda: [], reload=lambda: None)
        registry = SimpleNamespace(list=lambda: list(oauth_registry))
        return (
            patch.object(auth_cmd, "_all_providers", return_value=providers),
            patch("tau.inference.api.text.service.TextLLM._auth_manager", auth_manager),
            patch("tau.inference.api.text.service.TextLLM._providers", registry),
        )

    def test_escape_on_the_account_step_returns_to_the_method_step(self) -> None:
        ctx, showing = self._screen()
        provider = _FakeOAuthProvider(id="copilot", name="GitHub Copilot")
        a, b, c = self._patches(oauth_registry=[provider])

        with a, b, c:
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]
            showing["on_select"]("oauth")
            assert showing["title"] == "Select an account to sign in with:"

            showing["on_cancel"]()

            assert showing["title"] == "Select authentication method:"
        ctx.notify.assert_not_called()

    def test_escape_on_the_api_key_step_returns_to_the_method_step(self) -> None:
        ctx, showing = self._screen()
        a, b, c = self._patches()

        with a, b, c:
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]
            showing["on_select"]("api_key")
            assert showing["title"] == "Select a provider for the API key:"

            showing["on_cancel"]()

            assert showing["title"] == "Select authentication method:"
        ctx.notify.assert_not_called()

    def test_escape_at_the_key_prompt_returns_to_the_provider_list(self) -> None:
        ctx, showing = self._screen()
        a, b, c = self._patches()

        with a, b, c:
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]
            showing["on_select"]("api_key")
            showing["on_select"]("anthropic")
            assert showing["title"].startswith("API key for Anthropic")

            showing["on_cancel"]()

            assert showing["title"] == "Select a provider for the API key:"
        ctx.notify.assert_not_called()

    def test_escape_all_the_way_out_still_cancels(self) -> None:
        ctx, showing = self._screen()
        a, b, c = self._patches()

        with a, b, c:
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]
            showing["on_select"]("api_key")
            showing["on_cancel"]()  # back to the method step
            showing["on_cancel"]()  # and out

        ctx.notify.assert_called_once_with("Login cancelled.")

    def test_escape_cancels_when_the_method_step_never_showed(self) -> None:
        """With one auth style the provider list is the top level, not a substep."""
        ctx, showing = self._screen()
        a, b, c = self._patches(providers=[("anthropic", "Anthropic", False, True)])

        with a, b, c:
            auth_cmd.open_login_selector(ctx)  # type: ignore[arg-type]
            assert showing["title"] == "Select a provider for the API key:"

            showing["on_cancel"]()

        ctx.notify.assert_called_once_with("Login cancelled.")

    def test_logout_escape_cancels(self) -> None:
        """/logout is a single screen, so Escape has nowhere to go back to."""
        ctx, showing = self._screen()
        auth_manager = SimpleNamespace(list=lambda: ["copilot"], reload=lambda: None)

        with (
            patch.object(auth_cmd, "_all_providers", return_value=_PROVIDERS),
            patch("tau.inference.api.text.service.TextLLM._auth_manager", auth_manager),
        ):
            auth_cmd.open_logout_selector(ctx)  # type: ignore[arg-type]
            showing["on_cancel"]()

        ctx.notify.assert_called_once_with("Logout cancelled.")
