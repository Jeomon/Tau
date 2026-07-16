"""Shared visual theme for Tau's browser UI.

Color tokens and typography mirror the pi-web project (github.com/agegr/pi-web):
a flat, minimal light/dark palette with subtle borders instead of shadows.
"""

from __future__ import annotations

CSS = """
:root {
    --bg: #ffffff;
    --bg-panel: #f5f5f5;
    --bg-hover: #eeeeee;
    --bg-selected: #e8e8e8;
    --border: #e0e0e0;
    --text: #1a1a1a;
    --text-muted: #6b7280;
    --text-dim: #9ca3af;
    --accent: #2563eb;
    --accent-hover: #1d4ed8;
    --user-bg: #eff6ff;
}

/* Dark mode is toggled by NiceGUI's ui.dark_mode(), which adds .body--dark
   to <body> (Quasar's Dark plugin) — not a prefers-color-scheme query, so a
   manual toggle and the OS preference don't fight each other. */
body.body--dark {
    --bg: #1a1a1a;
    --bg-panel: #242424;
    --bg-hover: #2e2e2e;
    --bg-selected: #383838;
    --border: #3a3a3a;
    --text: #e8e8e8;
    --text-muted: #9ca3af;
    --text-dim: #6b7280;
    --accent: #60a5fa;
    --accent-hover: #93c5fd;
    --user-bg: #1e293b;
}

html, body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
}

/* NiceGUI's page container ships with 1rem padding/gap by default, which shows
   up as a white border around the whole app since our layout wants edge-to-edge. */
.nicegui-content {
    padding: 0 !important;
    gap: 0 !important;
}

pre, code {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
}

.tau-topbar {
    border-bottom: 1px solid var(--border);
}

.nicegui-markdown pre {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    overflow-x: auto;
    max-width: 100%;
}
.nicegui-markdown :not(pre) > code {
    background: var(--bg-panel);
    border-radius: 4px;
    padding: 1px 5px;
    overflow-wrap: anywhere;
}
.nicegui-markdown table {
    display: block;
    overflow-x: auto;
    max-width: 100%;
    border-collapse: collapse;
}
.nicegui-markdown th, .nicegui-markdown td {
    border: 1px solid var(--border);
    padding: 6px 10px;
}
.nicegui-markdown th {
    background: var(--bg-panel);
}

.tau-sidebar {
    background: var(--bg-panel);
    border-right: 1px solid var(--border);
}
.tau-sidebar-header {
    border-bottom: 1px solid var(--border);
}
.tau-project-path {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
    font-size: 11px;
    color: var(--text-muted);
    background: var(--bg-hover);
    border: 1px solid var(--border);
    border-radius: 7px;
}

.tau-session-row {
    border-left: 2px solid transparent;
    cursor: pointer;
    transition: background 0.1s;
}
.tau-session-row:hover {
    background: var(--bg-hover);
}
.tau-session-row.tau-active {
    background: var(--bg-selected);
    border-left-color: var(--accent);
}
.tau-session-delete-btn {
    color: var(--text-dim) !important;
    opacity: 0;
    transition: opacity 0.1s, color 0.12s;
}
.tau-session-row:hover .tau-session-delete-btn {
    opacity: 1;
}
.tau-session-delete-btn:hover {
    color: #ef4444 !important;
}
.tau-bubble-user {
    background: var(--user-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
}
.tau-bubble-assistant {
    background: transparent;
    border: none;
}

.tau-composer {
    background: var(--bg);
    border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
    border-radius: 14px;
    box-shadow:
        0 1px 2px rgba(15, 23, 42, 0.04),
        0 8px 24px -12px rgba(15, 23, 42, 0.1);
}
.tau-composer textarea {
    font-size: 14px !important;
    line-height: 1.6 !important;
}

.tau-thinking-block, .tau-tool-block {
    border-radius: 6px;
    font-size: 13px;
}
.tau-thinking-block {
    border: 1px solid var(--border);
    background: var(--bg-panel);
}
.tau-thinking-block .q-item, .tau-tool-block .q-item {
    padding: 6px 10px;
    min-height: 0;
}
.tau-thinking-block .q-item__section, .tau-tool-block .q-item__section {
    color: var(--text-muted);
    font-size: 12px;
}
.tau-tool-block {
    border-radius: 7px;
}
.tau-tool-ok {
    border: 1px solid rgba(34, 197, 94, 0.25);
    background: rgba(34, 197, 94, 0.04);
}
.tau-tool-error {
    border: 1px solid rgba(248, 113, 113, 0.45);
    background: rgba(248, 113, 113, 0.05);
}
.tau-tool-block .q-item__section {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
}

.tau-msg-meta {
    min-height: 22px;
    opacity: 0;
    transition: opacity 0.12s;
}
.tau-msg-row:hover .tau-msg-meta {
    opacity: 1;
}
.tau-msg-copy-btn {
    color: var(--text-dim) !important;
}
.tau-msg-copy-btn:hover {
    color: var(--accent) !important;
}
"""
