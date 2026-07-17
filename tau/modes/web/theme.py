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
    --accent-solid: var(--accent);
    --accent-solid-hover: var(--accent-hover);
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
    /* --accent is deliberately light in dark mode for text/borders on a dark
       background, but that's too low-contrast as a solid fill behind a white
       icon (the send button). Keep a separate, more saturated pair for
       solid-fill controls only. */
    --accent-solid: #2563eb;
    --accent-solid-hover: #3b82f6;
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

/* pi-web's slim 4px scrollbar (globals.css), applied to every native scroll
   surface. Quasar's own q-scrollarea component draws its own thumb/track
   instead of a native scrollbar (styled separately below), so this alone
   doesn't cover ui.scroll_area() — it covers ui.code()'s internal <pre>
   overflow and any other plain-CSS-overflow surface. */
::-webkit-scrollbar {
    width: 4px;
    height: 4px;
}
::-webkit-scrollbar-track {
    background: transparent;
}
::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 2px;
}
::-webkit-scrollbar-thumb:hover {
    background: var(--text-dim);
}
/* Quasar's q-scrollarea thumb defaults to a squat, solid-gray block that
   reads as a stray square floating at the top of the track — slim it down
   to match the native scrollbar above. */
.q-scrollarea__thumb {
    width: 4px !important;
    background: var(--border) !important;
    opacity: 1 !important;
    border-radius: 2px;
}
.q-scrollarea__thumb:hover {
    background: var(--text-dim) !important;
}

pre, code {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
}

/* Matches pi-web's AppShell top bar: fixed 36px height, panel background,
   full-bleed edge-to-edge (see chat.py — it's rendered outside the page's
   padded column specifically so it can reach both edges). */
.tau-topbar {
    height: 36px;
    min-height: 36px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}
/* Quasar's "text-primary" rule apparently matches a compound selector too
   (invisible to introspection — it lives in a stylesheet the browser
   treats as cross-origin) and ties or beats a 2-class scoped selector on
   source order. Repeating the class (.tau-topbar-tab.tau-topbar-tab) is a
   standard trick to add a full extra specificity point without resorting
   to an ID or guessing at !important stacking. */
.tau-topbar .tau-topbar-tab.tau-topbar-tab {
    height: 36px !important;
    min-height: 36px !important;
    border-radius: 0 !important;
    border-right: 1px solid var(--border) !important;
    color: var(--text-muted) !important;
    font-size: 11px !important;
    padding: 0 12px !important;
    transition: color 0.1s, background 0.1s;
}
.tau-topbar .tau-topbar-tab.tau-topbar-tab .q-icon {
    font-size: 14px;
    color: var(--text-muted) !important;
}
.tau-topbar .tau-topbar-tab.tau-topbar-tab:hover {
    background: var(--bg-hover) !important;
    color: var(--text) !important;
}
.tau-topbar .tau-topbar-tab.tau-topbar-tab:hover .q-icon {
    color: var(--text) !important;
}
/* pi-web's leftmost top-bar icon buttons (sidebar-toggle, theme toggle):
   flat 36px square, right border as the only divider, icon-only — distinct
   from .tau-topbar-tab which also carries a text label. */
.tau-topbar .tau-topbar-icon-btn {
    width: 36px !important;
    height: 36px !important;
    min-height: 36px !important;
    min-width: 36px !important;
    border-radius: 0 !important;
    border-right: 1px solid var(--border) !important;
    color: var(--text-muted) !important;
    padding: 0 !important;
    transition: color 0.12s;
}
.tau-topbar .tau-topbar-icon-btn .q-icon {
    font-size: 16px;
    color: var(--text-muted) !important;
}
.tau-topbar .tau-topbar-icon-btn:hover {
    color: var(--text) !important;
}
.tau-topbar .tau-topbar-icon-btn:hover .q-icon {
    color: var(--text) !important;
}
/* Rightmost variant (file-panel toggle): border divides on the left instead
   of the right, and the icon is mirrored so its "collapse" arrow points
   toward the panel it controls (right) instead of left like the sidebar's. */
.tau-topbar .tau-topbar-icon-btn-end {
    border-right: none !important;
    border-left: 1px solid var(--border) !important;
}
.tau-topbar .tau-topbar-icon-btn-end .q-icon {
    transform: scaleX(-1);
}
.tau-topbar-stats {
    height: 100%;
    font-variant-numeric: tabular-nums;
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
    width: fit-content;
    overflow-x: auto;
    max-width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border: 1px solid var(--border);
    border-radius: 6px;
}
.nicegui-markdown th, .nicegui-markdown td {
    /* Something upstream (NiceGUI/Quasar's base table styling) already gives
       cells a border on all four sides. Zero out top/left explicitly so it
       doesn't stack with the table's own outer border there — right/bottom
       are overridden to a real 1px below, which incidentally fixes the same
       doubling on those two sides too. */
    border-top: none;
    border-left: none;
    border-bottom: 1px solid var(--border);
    border-right: 1px solid var(--border);
    padding: 6px 10px;
}
.nicegui-markdown th:last-child, .nicegui-markdown td:last-child {
    border-right: none;
}
.nicegui-markdown tr:last-child td {
    border-bottom: none;
}
.nicegui-markdown th {
    background: var(--bg-panel);
}
.nicegui-markdown tr:first-child th:first-child {
    border-top-left-radius: 5px;
}
.nicegui-markdown tr:first-child th:last-child {
    border-top-right-radius: 5px;
}
.nicegui-markdown tr:last-child td:first-child {
    border-bottom-left-radius: 5px;
}
.nicegui-markdown tr:last-child td:last-child {
    border-bottom-right-radius: 5px;
}

/* border-right is toggled inline alongside width (see SessionSidebar.toggle)
   rather than left always-on here — a border can't shrink below its own
   thickness even under box-sizing:border-box, so a bare "width: 0" still
   renders a 1px sliver unless the border itself is removed too (matches
   pi-web's .sidebar-container.sidebar-closed { border-right: none }). */
.tau-sidebar-outer {
    flex-shrink: 0;
    transition: width 0.2s ease, min-width 0.2s ease;
}
.tau-sidebar {
    background: var(--bg-panel);
}

/* border-left is toggled inline alongside width (see FileExplorerPanel.toggle)
   rather than left always-on here — same reasoning as .tau-sidebar-outer. */
.tau-file-panel {
    background: var(--bg-panel);
    overflow: hidden;
    flex-shrink: 0;
    transition: width 0.2s ease;
}
/* Drag-to-resize handle, a thin strip inside the panel's own left edge
   (not straddling it) so overflow:hidden above doesn't clip it. */
.tau-file-resize-handle {
    position: absolute;
    top: 0;
    left: 0;
    width: 5px;
    height: 100%;
    cursor: col-resize;
    z-index: 10;
    background: transparent;
    transition: background 0.15s;
}
.tau-file-resize-handle:hover {
    background: color-mix(in srgb, var(--accent) 40%, transparent);
}
/* JS sets width on every mousemove for a 1:1 cursor tracking feel — the
   panel's own width transition (above) would otherwise animate toward each
   intermediate value and lag visibly behind the cursor while dragging. */
.tau-file-panel-resizing {
    transition: none !important;
}
.tau-file-panel-resizing .tau-file-resize-handle {
    background: var(--accent);
}
.tau-file-viewer {
    border-top: 1px solid var(--border);
}
.tau-tab-bar {
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}
.tau-file-tab {
    border-right: 1px solid var(--border);
    background: transparent;
}
.tau-file-tab:hover {
    background: var(--bg-hover);
}
.tau-file-tab.tau-active {
    background: var(--bg);
    border-bottom: 2px solid var(--accent);
}
.tau-file-tab-close {
    color: var(--text-dim);
    border-radius: 4px;
    padding: 2px;
}
.tau-file-tab-close:hover {
    color: var(--text);
    background: var(--bg-selected);
}
/* ui.code() defaults to a card look (tinted background, rounded corners,
   drop shadow) borrowed from its markdown wrapper — inside the file
   preview panel that reads as a second nested box around the content, so
   strip it back to flush plaintext that just sits on the panel background. */
.tau-file-panel .nicegui-code {
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
    padding: 0 !important;
}
.tau-file-panel .nicegui-code .nicegui-markdown pre {
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    margin: 0 !important;
}

.tau-minimap {
    width: 52px;
    flex-shrink: 0;
    overflow: hidden;
}
.tau-minimap-user {
    background: var(--accent);
    opacity: 0.6;
}
.tau-minimap-assistant {
    background: var(--text-dim);
}
.tau-minimap-user:hover, .tau-minimap-assistant:hover {
    opacity: 1;
}

.tau-settings-card {
    background: var(--bg) !important;
    color: var(--text) !important;
}
.tau-sidebar-header {
    border-bottom: 1px solid var(--border);
}
/* NiceGUI's scroll-area content defaults to align-items:flex-start, so rows
   shrink-to-fit their own content instead of stretching to the sidebar's
   width — a long session title then widens the whole list instead of
   ellipsis-truncating, and the sidebar scrolls horizontally. */
.tau-sidebar-scroll .q-scrollarea__content {
    align-items: stretch !important;
    max-width: 100%;
}
.tau-sidebar-footer {
    border-top: 1px solid var(--border);
    flex-shrink: 0;
}
.tau-sidebar-explorer {
    border-top: 1px solid var(--border);
    flex-shrink: 0;
}
.tau-explorer-header {
    color: var(--text-dim);
}
.tau-explorer-title {
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.tau-explorer-chevron {
    font-size: 16px !important;
    transition: transform 0.15s ease;
    transform: rotate(-90deg);
}
.tau-explorer-chevron-open {
    transform: rotate(0deg);
}
.tau-explorer-refresh {
    font-size: 15px !important;
    color: var(--text-dim);
    opacity: 0.75;
}
.tau-explorer-refresh:hover {
    opacity: 1;
    color: var(--text) !important;
}
/* pi-web's Explorer tree uses thin outlined, gray file/folder icons — match
   via the o_-prefixed Quasar icon variant plus this color override, since
   ui.tree() nodes don't expose a per-icon color prop. */
.tau-sidebar-explorer .q-tree__node-header {
    min-height: 26px;
    padding: 1px 4px;
}
.tau-sidebar-explorer .q-tree__node-header-content {
    color: var(--text-muted);
    font-size: 12px;
}
.tau-sidebar-explorer .q-tree__icon {
    color: var(--text-dim) !important;
    font-size: 16px !important;
}
.tau-sidebar-explorer .q-tree__arrow {
    color: var(--text-dim) !important;
}
/* Quasar's q-scrollarea__content carries a default 14px padding on every
   side — fine for the session list (rows already have their own edge
   padding baked in), but on the Explorer tree it stacked with the tree's
   own node spacing into a visibly oversized gap above the first row. */
.tau-sidebar-explorer .q-scrollarea__content {
    padding: 2px 4px !important;
}
.tau-footer-tab {
    height: 32px !important;
    min-height: 32px !important;
    padding: 0 12px !important;
    border-radius: 9px !important;
    color: var(--text-muted) !important;
    font-size: 12px !important;
}
.tau-footer-tab .q-icon {
    font-size: 16px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}
.tau-footer-tab .q-btn__content {
    gap: 6px;
    align-items: center;
}
.tau-footer-tab:hover {
    background: var(--bg-hover) !important;
    color: var(--text) !important;
}
.tau-model-tab .q-btn__content > span {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: inline-block;
}
.tau-model-menu {
    border-radius: 10px !important;
    box-shadow:
        0 2px 6px rgba(15, 23, 42, 0.06),
        0 12px 32px -8px rgba(15, 23, 42, 0.16) !important;
    border: 1px solid var(--border);
    background: var(--bg) !important;
}
/* Sticky so the search box stays reachable while a long, filtered model
   list scrolls underneath it instead of scrolling away with the rest. */
.tau-model-search-wrap {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
}
.tau-model-search-icon {
    font-size: 16px;
    color: var(--text-dim);
    flex-shrink: 0;
}
.tau-model-search .q-field__control,
.tau-model-search .q-field__native {
    font-size: 14px !important;
    color: var(--text) !important;
}
.tau-model-item {
    min-height: 38px;
    padding: 6px 12px;
    border-radius: 7px;
    margin: 1px 6px;
    width: calc(100% - 12px);
    transition: background 0.12s;
}
.tau-model-item:hover {
    background: var(--bg-hover) !important;
}
.tau-model-item-active {
    color: var(--accent) !important;
    font-weight: 500;
}
.tau-model-item-check {
    font-size: 15px !important;
    color: var(--accent) !important;
}
/* Slash-command / @-mention autocomplete. Same elevated-card treatment as
   .tau-model-menu (was a bare, flat, full-bleed list with no visual
   separation between the command/path and its description). */
.tau-suggestion-menu {
    border-radius: 10px !important;
    box-shadow:
        0 2px 6px rgba(15, 23, 42, 0.06),
        0 12px 32px -8px rgba(15, 23, 42, 0.16) !important;
    border: 1px solid var(--border);
    background: var(--bg) !important;
}
/* Highlights whichever row Up/Down is currently pointing at (Tab inserts
   it) — a plain background swap, not the accent-color treatment used for
   "currently selected" states elsewhere, since this is transient keyboard
   focus, not a persisted selection. */
.tau-suggestion-item {
    min-height: 32px;
    padding: 6px 12px;
    border-radius: 7px;
    margin: 1px 6px;
    width: calc(100% - 12px);
    transition: background 0.12s;
}
.tau-suggestion-item:hover {
    background: var(--bg-hover) !important;
}
.tau-suggestion-active {
    background: var(--bg-hover) !important;
}
.tau-suggestion-value {
    color: var(--text) !important;
}
.tau-suggestion-description {
    color: var(--text-dim) !important;
}
.tau-sidebar-footer-tab {
    height: 46px !important;
    min-height: 46px !important;
    padding: 4px 4px !important;
    border-radius: 9px !important;
    color: var(--text-muted) !important;
    font-size: 12px !important;
}
.tau-sidebar-footer-tab .q-btn__content {
    flex-direction: column;
    flex-wrap: nowrap;
    gap: 2px;
    line-height: 1.1;
}
.tau-sidebar-footer-tab .q-icon {
    font-size: 16px;
}
.tau-sidebar-footer-tab:hover {
    background: var(--bg-hover) !important;
    color: var(--text) !important;
}

.tau-icon-btn-32 {
    width: 32px !important;
    height: 32px !important;
    min-height: 32px !important;
}
.tau-project-path {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
    font-size: 11px;
    color: var(--text-muted);
    background: var(--bg-hover);
    border: 1px solid var(--border);
    border-radius: 7px;
}
/* Sticky so it stays reachable at the top of the scrolling session list
   instead of scrolling away with the rest (see SessionSidebar._render_content
   — it now lives inside the same ui.scroll_area as the rows, not a separate
   section above them). */
.tau-session-search-wrap {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
}
.tau-session-search {
    min-height: 20px !important;
    padding: 0 10px !important;
    color: var(--text);
    background: var(--bg-hover);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 13px !important;
}
.tau-session-search .q-field__control {
    height: 30px !important;
    min-height: 30px !important;
    /* The native input (22px) sits shorter than this control (30px) — by
       default Quasar's own layout leaves the gap entirely below it (top-
       aligned), not split evenly, so the placeholder/typed text reads as
       hugging the top edge instead of sitting centered on the line. Quasar
       nests an extra .q-field__control-container flex row between this and
       the native input, so centering has to apply to both levels. */
    align-items: center !important;
}
.tau-session-search .q-field__control-container {
    align-items: center !important;
}
/* Quasar's dense text field sets a hardcoded height (not just min-height)
   on the native input — 40px regardless of our min-height override above,
   which was the real floor on how short this box could get, not padding. */
.tau-session-search .q-field__native {
    padding: 0 !important;
    height: 22px !important;
    min-height: 22px !important;
    line-height: 22px !important;
}
.tau-session-search .q-field__native,
.tau-session-search .q-field__append {
    color: var(--text-muted) !important;
}

/* Matches pi-web's session row exactly (SessionSidebar.tsx): flat,
   full-bleed, no border-radius — a 2px accent left border is the only
   "selected" cue, not a card/tile treatment. */
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
/* Boxed 32x32 bordered icon buttons (pi-web's rename/delete affordance),
   not the borderless icon-only style used for the composer footer. Hidden
   until the row is hovered, matching pi-web's hover-conditional render. */
.tau-session-action-btn {
    width: 32px !important;
    height: 32px !important;
    min-height: 32px !important;
    border-radius: 7px !important;
    background: var(--bg-hover) !important;
    border: 1px solid var(--border) !important;
    opacity: 0;
    transition: opacity 0.1s, background 0.12s, border-color 0.12s;
}
.tau-session-row:hover .tau-session-action-btn {
    opacity: 1;
}
/* Quasar auto-assigns a "text-primary" class to flat icon buttons with no
   explicit color prop, and that rule targets the nested .q-icon directly —
   an explicit child-level color always wins over the button's own color
   regardless of specificity, so the icon needs its own override too (same
   fix as the composer's attach-file icon). pi-web's own pencil/trash glyphs
   are drawn at 14px inside the same 32px box (SessionSidebar.tsx) — Quasar's
   default icon font-size is 24px, which all but fills the box and leaves no
   padding, so it needs sizing down to match. */
.tau-session-action-btn .q-icon {
    color: var(--text-muted) !important;
    font-size: 14px !important;
}
.tau-session-action-btn:hover {
    background: var(--bg-selected) !important;
    border-color: color-mix(in srgb, var(--accent) 35%, transparent) !important;
}
.tau-session-action-btn:hover .q-icon {
    color: var(--accent) !important;
}
.tau-session-delete-btn:hover {
    background: rgba(239, 68, 68, 0.08) !important;
    border-color: rgba(239, 68, 68, 0.35) !important;
}
.tau-session-delete-btn:hover .q-icon {
    color: #ef4444 !important;
}
.tau-bubble-user {
    background: var(--user-bg);
    border: 1px solid var(--user-bg);
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
/* Matches pi-web's <textarea> exactly: minHeight 24, maxHeight 200,
   lineHeight 1.6, fontSize 14 (see ChatInput.tsx) — tau's previous 36px/168px
   values came from an earlier pass and never lined up with the reference. */
.tau-composer-input .q-field__control {
    min-height: 20px !important;
}
.tau-composer-input .q-field__native {
    min-height: 20px !important;
    max-height: 200px !important;
    overflow-y: auto !important;
    resize: none !important;
    scrollbar-width: thin;
}
.tau-send-button {
    width: 40px !important;
    height: 40px !important;
    min-height: 40px !important;
    color: #fff !important;
    box-shadow: 0 1px 3px rgba(37, 99, 235, 0.22) !important;
    transition: background 0.12s, box-shadow 0.12s, opacity 0.12s;
    margin-right: 3px;
}
.tau-attach-upload {
    width: 36px !important;
    min-width: 36px !important;
    height: 36px !important;
    min-height: 36px !important;
    max-height: 36px !important;
    border-radius: 8px !important;
    overflow: hidden;
    flex-shrink: 0;
    margin-bottom: 2px;
}
.tau-attach-upload .q-uploader__header {
    background: transparent !important;
    box-shadow: none !important;
    padding: 0 !important;
    min-height: 36px !important;
}
.tau-attach-upload .q-uploader__header-content {
    padding: 0 !important;
}
.tau-attach-upload .q-uploader__title,
.tau-attach-upload .q-uploader__subtitle,
.tau-attach-upload .q-uploader__list {
    display: none !important;
}
.tau-attach-upload .q-btn {
    width: 36px !important;
    height: 36px !important;
    min-height: 36px !important;
    color: var(--text-muted) !important;
    background: transparent !important;
    border-radius: 8px !important;
}
.tau-attach-upload .q-btn:hover {
    background: var(--bg-hover) !important;
}
/* Quasar's default uploader icon (add_box) is a bold filled glyph that reads
   heavier than the thin outlined icons used everywhere else in the footer.
   Swap it for a lighter paperclip via the Material Icons ligature trick
   instead of reaching into QUploader's header slot (which would mean
   reimplementing its file-picker wiring by hand). */
.tau-attach-upload .q-btn .q-icon {
    font-size: 0 !important;
    color: transparent !important;
    position: relative;
    width: 20px;
    height: 20px;
}
.tau-attach-upload .q-btn .q-icon::before {
    content: "attach_file";
    font-family: "Material Icons";
    font-size: 19px;
    line-height: 1;
    color: var(--text-muted);
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}
.tau-attach-upload .q-btn:hover .q-icon::before {
    color: var(--text);
}
.tau-attachment-chip {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 12px;
}
.tau-send-button-idle {
    background: var(--accent-solid) !important;
}
.tau-send-button-idle:hover {
    background: var(--accent-solid-hover) !important;
}
.tau-send-button-disabled,
.tau-send-button-disabled.q-btn--disabled {
    background: var(--bg-selected) !important;
    color: var(--text-dim) !important;
    opacity: 1 !important;
    box-shadow: none !important;
}
.tau-send-button-running {
    background: #dc2626 !important;
    box-shadow: 0 1px 3px rgba(220, 38, 38, 0.24) !important;
}
.tau-send-button-running:hover {
    background: #b91c1c !important;
}

.tau-thinking-block {
    border-radius: 6px;
    font-size: 13px;
    border: 1px solid var(--border);
    background: var(--bg-panel);
}
.tau-thinking-block .q-item {
    padding: 6px 10px;
    min-height: 0;
}
.tau-thinking-block .q-item__section {
    color: var(--text-muted);
    font-size: 12px;
}

/* Matches pi-web's ToolCallBlock: collapsed-by-default header with a bold
   colored verb + gray monospace preview, args/result only rendered once
   expanded (see message_view.py::render_tool_call_block — a hand-rolled
   toggle since ui.expansion() only supports a plain-string header, and
   this needs two independently-colored spans in the same line). */
.tau-tool-block {
    border-radius: 7px;
    overflow: hidden;
    font-size: 12px;
}
.tau-tool-ok {
    border: 1px solid rgba(34, 197, 94, 0.25);
    background: rgba(34, 197, 94, 0.04);
}
.tau-tool-error {
    border: 1px solid rgba(248, 113, 113, 0.45);
    background: rgba(248, 113, 113, 0.05);
}
.tau-tool-header {
    color: var(--text-muted);
}
.tau-tool-name {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
    font-weight: 600;
    font-size: 11px;
    flex-shrink: 0;
}
.tau-tool-preview {
    font-family: "JetBrains Mono", "Fira Code", Consolas, ui-monospace, monospace;
    font-size: 11px;
    color: var(--text-dim);
}
.tau-tool-chevron {
    font-size: 16px !important;
    color: var(--text-dim) !important;
    flex-shrink: 0;
    transition: transform 0.15s;
}
.tau-tool-chevron-open {
    transform: rotate(180deg);
}
.tau-tool-details {
    border-top: 1px solid rgba(34, 197, 94, 0.2);
}
.tau-tool-error .tau-tool-details {
    border-top-color: rgba(248, 113, 113, 0.25);
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
