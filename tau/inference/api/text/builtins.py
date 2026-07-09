# Lazy registry entries: each provider API is referenced by a "module:ClassName"
# path so its SDK (anthropic, openai, google.genai, mistralai, ...) is only
# imported when that provider is actually used. Loading every SDK at startup
# previously cost ~1.5s on cold start.
LLM_APIS: list[tuple[str, str]] = [
    ("openai_responses", "tau.inference.api.text.openai_responses:OpenAIResponsesAPI"),
    ("openai_completions", "tau.inference.api.text.openai_completions:OpenAICompletionsAPI"),
    (
        "openai_codex_responses",
        "tau.inference.api.text.openai_codex_responses:OpenAICodexResponsesAPI",
    ),
    ("anthropic_messages", "tau.inference.api.text.anthropic_messages:AnthropicMessagesAPI"),
    (
        "anthropic_claude_code",
        "tau.inference.api.text.anthropic_claude_code:AnthropicClaudeCodeAPI",
    ),
    ("github_copilot_chat", "tau.inference.api.text.github_copilot_chat:GitHubCopilotChatAPI"),
    ("gemini_generate", "tau.inference.api.text.gemini_generate:GeminiGenerateAPI"),
    ("mistral_chat", "tau.inference.api.text.mistral_chat:MistralChatAPI"),
    ("ollama_chat", "tau.inference.api.text.ollama_chat:OllamaChatAPI"),
    ("google_antigravity", "tau.inference.api.text.google_antigravity:GoogleAntigravityAPI"),
    ("google_vertex", "tau.inference.api.text.google_vertex:GoogleVertexAPI"),
    ("anthropic_vertex", "tau.inference.api.text.anthropic_vertex:AnthropicVertexAPI"),
    ("openai_vertex", "tau.inference.api.text.openai_vertex:OpenAIVertexAPI"),
    ("xai", "tau.inference.api.text.xai_responses:XAIAPIResponses"),
]

# Backward-compat alias
APIS = LLM_APIS
