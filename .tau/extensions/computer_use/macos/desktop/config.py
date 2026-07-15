"""Bundle-ID allowlists used by the desktop and tree services."""

BROWSER_BUNDLE_IDS = {
    "com.apple.Safari",
    "com.google.Chrome",
    "org.mozilla.firefox",
    "com.microsoft.edgemac",
    "com.brave.Browser",
    "com.operasoftware.Opera",
    "com.vivaldi.Vivaldi",
    "company.thebrowser.Browser",  # Arc
}

EXCLUDED_BUNDLE_IDS = {
    "com.apple.finder",  # Finder (always running, often background)
}

# System UI apps to include in the accessibility tree (whitelist). Explicit
# bundle IDs instead of policy='Accessory' avoid traversing helper/agent
# processes (Chrome Helper, WallpaperAgent, ...) that add noise.
SYSTEM_UI_BUNDLE_IDS = {
    "com.apple.dock",
    "com.apple.controlcenter",
    "com.apple.systemuiserver",
    "com.apple.Spotlight",
}
