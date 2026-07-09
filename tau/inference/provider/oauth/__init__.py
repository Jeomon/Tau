from typing import TYPE_CHECKING

from tau.inference.provider.oauth.pkce import generate_pkce
from tau.inference.provider.oauth.types import (
    OAuthAuthInfo,
    OAuthCredential,
    OAuthLoginCallbacks,
    OAuthPrompt,
)
from tau.inference.provider.types import OAuthProvider

if TYPE_CHECKING:
    from tau.inference.provider.oauth.anthropic_claude_code import AnthropicClaudeCodeOAuthProvider
    from tau.inference.provider.oauth.github_copilot import (
        GitHubCopilotOAuthProvider,
        get_copilot_base_url,
    )
    from tau.inference.provider.oauth.google_antigravity import GoogleAntigravityOAuthProvider
    from tau.inference.provider.oauth.openai_codex import OpenAICodexOAuthProvider
    from tau.inference.provider.oauth.xai_grok import XAIGrokOAuthProvider

__all__ = [
    "OAuthProvider",
    "OAuthCredential",
    "OAuthLoginCallbacks",
    "OAuthAuthInfo",
    "OAuthPrompt",
    "generate_pkce",
    "OpenAICodexOAuthProvider",
    "AnthropicClaudeCodeOAuthProvider",
    "GitHubCopilotOAuthProvider",
    "get_copilot_base_url",
    "GoogleAntigravityOAuthProvider",
    "XAIGrokOAuthProvider",
]


def __getattr__(name: str):
    if name == "OpenAICodexOAuthProvider":
        from tau.inference.provider.oauth.openai_codex import OpenAICodexOAuthProvider

        return OpenAICodexOAuthProvider
    if name == "AnthropicClaudeCodeOAuthProvider":
        from tau.inference.provider.oauth.anthropic_claude_code import (
            AnthropicClaudeCodeOAuthProvider,
        )

        return AnthropicClaudeCodeOAuthProvider
    if name in ("GitHubCopilotOAuthProvider", "get_copilot_base_url"):
        from tau.inference.provider.oauth.github_copilot import (
            GitHubCopilotOAuthProvider,
            get_copilot_base_url,
        )  # noqa: F401

        globals()["GitHubCopilotOAuthProvider"] = GitHubCopilotOAuthProvider
        globals()["get_copilot_base_url"] = get_copilot_base_url
        return globals()[name]
    if name == "GoogleAntigravityOAuthProvider":
        from tau.inference.provider.oauth.google_antigravity import GoogleAntigravityOAuthProvider

        return GoogleAntigravityOAuthProvider
    if name == "XAIGrokOAuthProvider":
        from tau.inference.provider.oauth.xai_grok import XAIGrokOAuthProvider

        return XAIGrokOAuthProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
