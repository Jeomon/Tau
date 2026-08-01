"""Configuration for a local Chromium process or remote CDP connection."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    cdp_url: str | None = None
    executable_path: Path | str | None = None
    user_data_dir: Path | str | None = None
    headless: bool = True
    initial_url: str = "about:blank"
    args: tuple[str, ...] = field(default_factory=tuple)
    env: Mapping[str, str] | None = None
    startup_timeout: float = 15.0
    shutdown_timeout: float = 5.0
    chromium_sandbox: bool = True
    downloads_path: Path | str | None = None
    permissions: tuple[str, ...] = field(default_factory=tuple)
    allowed_domains: tuple[str, ...] = field(default_factory=tuple)
    blocked_domains: tuple[str, ...] = field(default_factory=tuple)
    allow_local_network: bool = False
    allowed_url_schemes: tuple[str, ...] = ("http", "https", "about", "data")
    storage_state_path: Path | str | None = None
    auto_save_storage_state: bool = True
    storage_state_autosave_interval: float | None = None
    reconnect_attempts: int = 3
    reconnect_delay: float = 1.0
    reconnect_timeout: float = 10.0
    download_retention: float = 60.0
    interaction_retries: int = 2
    interaction_retry_delay: float = 0.05
    drag_step_delay: float = 0.01
    cross_origin_iframes: bool = True
    max_iframes: int = 100
    max_iframe_depth: int = 5
    min_iframe_size: float = 50.0
    dom_cache_ttl: float = 0.25
    dom_viewport_threshold: float | None = 1000.0
    # Let the page settle before a DOM capture so late/async-injected content is
    # included: wait at least dom_settle_min_wait, then until the network is
    # quiet (in-flight request count stable for dom_settle_network_idle), all
    # capped at dom_settle_max_wait. Set dom_settle_max_wait to 0 to disable.
    dom_settle_min_wait: float = 0.1
    dom_settle_network_idle: float = 0.3
    dom_settle_max_wait: float = 1.0
    paint_order_filtering: bool = True
    stealth: bool = False
    user_agent: str | None = None
    cdp_call_timeout: float | None = 60.0
    cdp_slow_call_timeout: float | None = 120.0
    record_har_path: Path | str | None = None
    keep_one_blank_tab_alive: bool = False

    def __post_init__(self) -> None:
        if self.startup_timeout <= 0:
            raise ValueError("startup_timeout must be greater than zero")
        if self.shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be greater than zero")
        if self.reconnect_attempts < 0:
            raise ValueError("reconnect_attempts cannot be negative")
        if self.reconnect_delay < 0:
            raise ValueError("reconnect_delay cannot be negative")
        if self.reconnect_timeout <= 0:
            raise ValueError("reconnect_timeout must be greater than zero")
        if self.download_retention < 0:
            raise ValueError("download_retention cannot be negative")
        if self.interaction_retries < 0:
            raise ValueError("interaction_retries cannot be negative")
        if self.interaction_retry_delay < 0:
            raise ValueError("interaction_retry_delay cannot be negative")
        if self.drag_step_delay < 0:
            raise ValueError("drag_step_delay cannot be negative")
        if self.max_iframes < 0:
            raise ValueError("max_iframes cannot be negative")
        if self.max_iframe_depth < 0:
            raise ValueError("max_iframe_depth cannot be negative")
        if self.min_iframe_size < 0:
            raise ValueError("min_iframe_size cannot be negative")
        if self.dom_cache_ttl < 0:
            raise ValueError("dom_cache_ttl cannot be negative")
        if self.dom_settle_min_wait < 0 or self.dom_settle_network_idle < 0 or self.dom_settle_max_wait < 0:
            raise ValueError("dom_settle_* waits cannot be negative")
        if self.dom_viewport_threshold is not None and self.dom_viewport_threshold < 0:
            raise ValueError("dom_viewport_threshold cannot be negative")
        if (
            self.storage_state_autosave_interval is not None
            and self.storage_state_autosave_interval <= 0
        ):
            raise ValueError("storage_state_autosave_interval must be greater than zero")
        if self.cdp_call_timeout is not None and self.cdp_call_timeout <= 0:
            raise ValueError("cdp_call_timeout must be greater than zero")
        if self.cdp_slow_call_timeout is not None and self.cdp_slow_call_timeout <= 0:
            raise ValueError("cdp_slow_call_timeout must be greater than zero")
        if not self.initial_url:
            raise ValueError("initial_url cannot be empty")
        if self.cdp_url is not None and not self.cdp_url.startswith(
            ("ws://", "wss://")
        ):
            raise ValueError("cdp_url must start with 'ws://' or 'wss://'")
        invalid_args = [arg for arg in self.args if not arg.startswith("--")]
        if invalid_args:
            raise ValueError(
                f"browser arguments must start with '--': {invalid_args!r}"
            )
        for pattern in (*self.allowed_domains, *self.blocked_domains):
            if "://" in pattern or "/" in pattern:
                raise ValueError(
                    f"domain patterns must not contain a scheme or path: {pattern!r}"
                )
        if not self.allowed_url_schemes:
            raise ValueError("allowed_url_schemes cannot be empty")

        if self.executable_path is not None:
            object.__setattr__(
                self, "executable_path", Path(self.executable_path).expanduser()
            )
        if self.user_data_dir is not None:
            object.__setattr__(
                self, "user_data_dir", Path(self.user_data_dir).expanduser()
            )
        if self.downloads_path is not None:
            object.__setattr__(
                self, "downloads_path", Path(self.downloads_path).expanduser()
            )
        if self.storage_state_path is not None:
            object.__setattr__(
                self,
                "storage_state_path",
                Path(self.storage_state_path).expanduser(),
            )

    def process_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        return env
