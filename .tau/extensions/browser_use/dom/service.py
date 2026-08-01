"""Interactive-element grounding.

Detects the interactive (clickable / typeable) elements on a page from a CDP
``DOMSnapshot`` and the accessibility tree, keyed for the agent to act on. The
detection flow is: parse each document's flat node list, require every kept
element to resolve to an accessible name, collapse nested duplicates by name
dominance, and drop occluded elements with a live ``elementFromPoint`` coverage
check. Same-process and cross-origin iframes and open shadow DOM are folded into
one top-viewport coordinate space, and the survivors are also nested into an
interactive tree.

``DOM(browser)`` emits this project's ``Element``/``Bounds``/
``AccessibilityNode``/``DOMState`` types (see ``types.py``), exposes the
``capture()``/``invalidate()``/``latest()`` API the browser service calls, and
drives Chrome through this project's CDP client and ``evaluate`` helpers.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from .types import (
    AccessibilityNode,
    Bounds,
    DOMState,
    DOMTreeNode,
    Element,
)

if TYPE_CHECKING:
    from ..browser.service import Browser
    from ..browser.state import ViewportState

# Computed styles captured with the snapshot, in this fixed order (see the
# index aliases below).
COMPUTED_STYLES = ["display", "visibility", "opacity", "cursor", "overflow-y", "position"]
_D, _V, _O, _C, _OY, _P = range(6)

INTERACTIVE_ROLES = frozenset({
    "button", "link", "checkbox", "radio", "textbox", "combobox", "listbox",
    "menuitem", "menuitemcheckbox", "menuitemradio", "option", "tab", "treeitem",
    "slider", "spinbutton", "searchbox", "switch", "gridcell",
    "columnheader", "rowheader",
    "tooltip", "tree", "tabpanel", "progressbar", "scrollbar",
})

INTERACTIVE_TAGS = frozenset({
    "a", "button", "input", "select", "textarea", "option",
    "summary", "menu", "menuitem",
    "embed", "canvas", "object",
})

INLINE_ELEMENTS = frozenset({
    "span", "em", "strong", "b", "i", "small", "abbr", "code",
    "mark", "sub", "sup", "cite", "q", "u", "s", "del", "ins",
    "time", "kbd", "var", "samp", "a",
})

EXCLUDED_TAGS = frozenset({
    "style", "script", "noscript", "link", "meta",
    "head", "br", "hr",
})

_SEARCH_INDICATORS = (
    "search", "magnify", "glass", "lookup", "find", "query",
    "search-icon", "search-btn", "search-button", "searchbox",
)


def _has_search_indicator(attrs: dict[str, str]) -> bool:
    """A search affordance signalled by class/id/data-* keywords — catches
    nameless search-icon buttons that carry no tag/role of their own."""
    if any(w in attrs.get("class", "").lower() for w in _SEARCH_INDICATORS):
        return True
    if any(w in attrs.get("id", "").lower() for w in _SEARCH_INDICATORS):
        return True
    return any(
        name.startswith("data-") and any(w in value.lower() for w in _SEARCH_INDICATORS)
        for name, value in attrs.items()
    )


SAFE_ATTRIBUTES = frozenset({
    "id", "name", "role", "type",
    "value", "placeholder", "alt", "title",
    "aria-label", "aria-placeholder", "aria-autocomplete",
    "checked", "selected", "expanded", "pressed",
    "disabled", "required", "invalid",
    "aria-checked", "aria-selected", "aria-expanded",
    "aria-pressed", "aria-disabled", "aria-hidden",
    "data-state",
    "pattern", "min", "max", "minlength", "maxlength",
    "step", "accept", "multiple", "inputmode", "autocomplete",
    "aria-valuemin", "aria-valuemax", "aria-valuenow",
    "data-date-format", "data-datepicker",
    "contenteditable", "haspopup", "multiselectable",
    "data-testid",
    "onclick", "href", "tabindex",
    "data-tooltip", "data-id", "data-qa", "data-cy",
    "class",
})

# Batch occlusion check: hit-test each element's centre and walk up
# the hit path looking for a node whose tag and top-left (within 4px) match the
# candidate — an element not found in its own centre's hit path is occluded.
# Extended for shadow DOM: ``document.elementFromPoint`` returns the shadow
# *host*, not the content, so ``deepEFP`` pierces open shadow roots, and the
# upward walk crosses shadow boundaries via ``getRootNode().host``. ``ELEMENTS``
# is replaced with a JSON array of {tag, cx, cy, left, top} in viewport pixels.
_CHECK_COVERAGE_JS = """(function(els){
    function deepEFP(x,y){
        var el=document.elementFromPoint(x,y);
        if(!el) return null;
        while(el.shadowRoot){
            var inner=el.shadowRoot.elementFromPoint(x,y);
            if(!inner||inner===el) break;
            el=inner;
        }
        return el;
    }
    return els.map(function(e){
        var cur=deepEFP(e.cx,e.cy);
        if(!cur) return false;
        while(cur){
            if(cur.tagName&&cur.tagName.toLowerCase()===e.tag){
                var r=cur.getBoundingClientRect();
                if(Math.abs(Math.round(r.left)-e.left)<=4&&Math.abs(Math.round(r.top)-e.top)<=4)
                    return true;
            }
            var root=cur.getRootNode();
            cur=cur.parentElement||(root instanceof ShadowRoot?root.host:null);
        }
        return false;
    });
})(ELEMENTS)"""


class DOM:
    """Collect and ground the interactive elements for one browser."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self._cache: dict[tuple[Any, ...], tuple[float, DOMState]] = {}
        self._previous: dict[str, DOMState] = {}

    def latest(self, session_id: str) -> DOMState | None:
        return self._previous.get(session_id)

    def invalidate(
        self,
        session_id: str | None = None,
        *,
        reset_previous: bool = False,
    ) -> None:
        # A child-frame mutation can affect a cached top-level aggregate, so any
        # invalidation clears the whole capture cache.
        self._cache.clear()
        if reset_previous:
            if session_id is None:
                self._previous.clear()
            else:
                self._previous.pop(session_id, None)

    async def capture(
        self,
        session_id: str,
        viewport: ViewportState,
        *,
        include_dom: bool = True,
        include_accessibility: bool = True,
        force: bool = False,
    ) -> DOMState:
        key = (
            session_id,
            viewport.width,
            viewport.height,
            viewport.page_x,
            viewport.page_y,
            include_dom,
            include_accessibility,
        )
        cached = self._cache.get(key)
        if (
            not force
            and cached is not None
            and time.monotonic() - cached[0] <= self.browser.settings.dom_cache_ttl
        ):
            return cached[1]

        if not include_dom:
            state = DOMState()
            self._cache[key] = (time.monotonic(), state)
            return state

        if self.browser.client is None:
            raise RuntimeError("browser is not connected")

        await self._settle(session_id)

        dpr = float(viewport.device_pixel_ratio or 1.0)
        elements, ax_nodes, main_frame_id, snapshot, parents = await self._capture_frame(
            session_id,
            (int(viewport.width), int(viewport.height)),
            dpr,
            base_x=0.0,
            base_y=0.0,
            top_scroll=(float(viewport.page_x or 0), float(viewport.page_y or 0)),
            include_accessibility=include_accessibility,
            depth=0,
            remaining=[self.browser.settings.max_iframes],
            visited={session_id},
        )

        # Live occlusion check: drop interactive elements that are
        # actually covered on the rendered page. elementFromPoint does not
        # pierce iframes, so this only applies to main-frame elements — iframe
        # content (same- or cross-origin) passes through unchecked.
        elements = await self._coverage_filter(session_id, elements, main_frame_id)
        # Collapse stacked wrapper/inner duplicates that name-dominance missed.
        elements = _collapse_contained_duplicates(elements, parents)

        state = DOMState(
            elements=tuple(elements),
            accessibility=tuple(ax_nodes),
            snapshot=snapshot,
            roots=_build_interactive_tree(elements, parents),
        )
        self._previous[session_id] = state
        self._cache[key] = (time.monotonic(), state)
        return state

    async def _settle(self, session_id: str) -> None:
        """Let the page settle before capturing so late/async-injected content
        (cookie bars, promos, lazily-hydrated nav, subscription flyouts) is
        included: wait for document.readyState == 'complete', then until the
        in-flight request count is stable for a quiet window, floored at a
        minimum and capped at a maximum. Best-effort — any failure just returns
        early rather than blocking a capture. Disabled when
        dom_settle_max_wait <= 0 (or settings aren't real numbers, e.g. mocks)."""
        settings = self.browser.settings
        max_wait = _as_float(getattr(settings, "dom_settle_max_wait", 0.0))
        if max_wait <= 0:
            return
        min_wait = _as_float(getattr(settings, "dom_settle_min_wait", 0.0))
        idle = _as_float(getattr(settings, "dom_settle_network_idle", 0.0))
        loop = asyncio.get_running_loop()
        start = loop.time()
        deadline = start + max_wait

        # 1. document.readyState == 'complete'
        while loop.time() < deadline:
            try:
                if await self.browser.evaluate(session_id, "document.readyState") == "complete":
                    break
            except Exception:
                break
            await asyncio.sleep(0.05)

        # 2. network quiet: in-flight request count stable for `idle` seconds
        #    (0-in-flight rarely happens on ad-heavy pages, so a stable count is
        #    used rather than an empty one).
        if idle > 0:
            last_count: int | None = None
            stable_since: float | None = None
            while loop.time() < deadline:
                try:
                    requests = self.browser.network.for_session(session_id)
                    in_flight = sum(1 for r in requests if not r.finished.is_set())
                except Exception:
                    break
                now = loop.time()
                if in_flight == last_count:
                    if stable_since is None:
                        stable_since = now
                    if now - stable_since >= idle:
                        break
                else:
                    last_count = in_flight
                    stable_since = now
                await asyncio.sleep(0.05)

        # 3. minimum floor
        elapsed = loop.time() - start
        if elapsed < min_wait:
            await asyncio.sleep(min_wait - elapsed)

    async def _capture_frame(
        self,
        session_id: str,
        viewport: tuple[int, int],
        dpr: float,
        *,
        base_x: float,
        base_y: float,
        top_scroll: tuple[float, float],
        include_accessibility: bool,
        depth: int,
        remaining: list[int],
        visited: set[str],
        owner_backend: int | None = None,
    ) -> tuple[
        list[Element], list[AccessibilityNode], str, dict, dict[int, int | None]
    ]:
        """Capture and parse one frame's DOMSnapshot, then recurse into its
        cross-origin (out-of-process) child iframes — each of which is a
        separate CDP session whose content is absent from this snapshot. Every
        child frame's elements are offset by the hosting iframe's viewport
        position (``base_x/base_y``), matching how same-process iframes are
        offset inside ``_parse``. ``owner_backend`` is the backend id of the
        hosting `<iframe>` element in the parent frame, so this frame's roots
        chain up to it for tree construction. Also returns the merged
        element-parent map (backend id -> parent element backend id, across
        same- and cross-origin frames) and this frame's snapshot."""
        client = self.browser.client
        assert client is not None
        slow = self.browser.settings.cdp_slow_call_timeout
        snapshot, ax_result = await asyncio.gather(
            client.send(
                "DOMSnapshot.captureSnapshot",
                {
                    "computedStyles": COMPUTED_STYLES,
                    "includePaintOrder": True,
                    "includeDOMRects": True,
                },
                session_id=session_id,
                timeout=slow,
            ),
            self._ax_tree_for_all_frames(session_id, slow)
            if include_accessibility
            else _empty_ax(),
        )

        elements, ax_nodes, frame_id, oopifs, parents = self._parse(
            snapshot, ax_result, viewport, dpr,
            top_scroll[0], top_scroll[1], session_id, base_x, base_y,
            owner_backend,
        )
        all_elements = list(elements)
        all_ax = list(ax_nodes)
        all_parents: dict[int, int | None] = dict(parents)

        min_size = self.browser.settings.min_iframe_size
        if depth < self.browser.settings.max_iframe_depth:
            for backend_id, ivx, ivy, iw, ih in oopifs:
                if remaining[0] <= 0:
                    break
                if iw < min_size or ih < min_size:
                    continue
                child_session = await self._iframe_session(session_id, backend_id)
                if not child_session or child_session in visited:
                    continue
                remaining[0] -= 1
                visited.add(child_session)
                try:
                    child_els, child_ax, _, _, child_parents = await self._capture_frame(
                        child_session, viewport, dpr,
                        base_x=ivx, base_y=ivy, top_scroll=top_scroll,
                        include_accessibility=include_accessibility,
                        depth=depth + 1, remaining=remaining, visited=visited,
                        owner_backend=backend_id,
                    )
                except Exception:
                    continue  # a flaky OOPIF session must not fail the capture
                all_elements += child_els
                all_ax += child_ax
                all_parents.update(child_parents)

        return all_elements, all_ax, frame_id, snapshot, all_parents

    async def _iframe_session(self, parent_session_id: str, backend_id: int) -> str | None:
        """Resolve the CDP session that renders a cross-origin iframe element,
        via its content frame id (ported from the previous DOM service)."""
        client = self.browser.client
        if client is None:
            return None
        try:
            described = await client.dom.describe_node(
                {"backendNodeId": backend_id}, session_id=parent_session_id
            )
        except Exception:
            return None
        frame_id = described.get("node", {}).get("frameId")
        if not frame_id:
            return None
        session = self.browser.session
        target_id = session.target_for_frame(frame_id)
        if target_id is None and frame_id in session.targets:
            target_id = frame_id
        if target_id is None:
            return None
        target = session.targets.get(target_id)
        if target is None or target.target_type not in {"iframe", "page", "tab"}:
            return None
        try:
            return await session.session_for(target_id)
        except Exception:
            return None

    async def _ax_tree_for_all_frames(self, session_id: str, timeout) -> dict:
        """Merge ``Accessibility.getFullAXTree`` across every frame in this
        session's frame tree.

        A single unframed ``getFullAXTree`` can omit same-process child-frame
        AX nodes, which would drop iframe elements whose only accessible name
        comes from an aria-label/AX name (with no visible text). Fetching per
        frame id and merging closes that gap. Child-frame failures (e.g. an ad
        iframe detaching mid-request) are skipped; a total failure falls back
        to one unframed call."""
        client = self.browser.client
        assert client is not None
        try:
            frame_tree = await client.send(
                "Page.getFrameTree", {}, session_id=session_id, timeout=timeout
            )
        except Exception:
            try:
                return await client.send(
                    "Accessibility.getFullAXTree", {}, session_id=session_id, timeout=timeout
                )
            except Exception:
                return {"nodes": []}

        def collect(node: dict) -> list[str]:
            ids = [node["frame"]["id"]]
            for child in node.get("childFrames") or []:
                ids.extend(collect(child))
            return ids

        try:
            frame_ids = collect(frame_tree["frameTree"])
        except (KeyError, TypeError):
            frame_ids = []

        results = await asyncio.gather(
            *(
                client.send(
                    "Accessibility.getFullAXTree",
                    {"frameId": frame_id},
                    session_id=session_id,
                    timeout=timeout,
                )
                for frame_id in frame_ids
            ),
            return_exceptions=True,
        )
        merged: list = []
        for result in results:
            if isinstance(result, BaseException):
                continue
            merged.extend(result.get("nodes", []))
        return {"nodes": merged}

    async def _coverage_filter(
        self, session_id: str, elements: list[Element], main_frame_id: str
    ) -> list[Element]:
        targets = [
            e for e in elements if e.clickable and e.frame_id == main_frame_id
        ]
        if not targets:
            return elements
        payload = [
            {
                "tag": e.tag_name,
                "cx": round(e.bounds.x + e.bounds.width / 2),
                "cy": round(e.bounds.y + e.bounds.height / 2),
                "left": round(e.bounds.x),
                "top": round(e.bounds.y),
            }
            for e in targets
        ]
        try:
            visible = await self.browser.evaluate(
                session_id,
                _CHECK_COVERAGE_JS.replace("ELEMENTS", json.dumps(payload)),
            )
        except Exception:
            return elements  # keep all if the check fails
        if not isinstance(visible, list) or len(visible) != len(targets):
            return elements
        keep = {id(t) for t, ok in zip(targets, visible) if ok}
        target_ids = {id(t) for t in targets}
        return [e for e in elements if id(e) not in target_ids or id(e) in keep]

    def _parse(
        self,
        snapshot: dict,
        ax_result: dict,
        viewport: tuple[int, int],
        dpr: float,
        top_scroll_x: float,
        top_scroll_y: float,
        session_id: str,
        base_x: float = 0.0,
        base_y: float = 0.0,
        owner_backend: int | None = None,
    ) -> tuple[
        list[Element],
        list[AccessibilityNode],
        str,
        list[tuple[int, float, float, float, float]],
        dict[int, int | None],
    ]:
        """Orchestrate parsing across every document in the snapshot. Same-
        process iframes appear as additional documents; each is parsed with a
        cumulative frame offset so its element bounds land in top-viewport
        space (origin[child] = iframe_local + origin[parent] - child_scroll).
        `base_x/base_y` shift the whole snapshot (non-zero when
        this snapshot is itself a cross-origin child frame). Also returns the
        cross-origin (out-of-process) iframe elements found — (backend id, x, y,
        w, h) in top-viewport space — for the caller to recurse into, and the
        element-parent map (backend id -> parent element backend id) for tree
        construction, with each document's root chained to its hosting iframe
        (`owner_backend` for the top document of this snapshot)."""
        strings = snapshot.get("strings", [])
        docs = snapshot.get("documents", [])
        if not docs:
            return [], [], "", [], {}

        def s(idx: int) -> str:
            return strings[idx] if isinstance(idx, int) and 0 <= idx < len(strings) else ""

        # AX tree is global to the session (covers same-process iframe nodes
        # too) — build the map once, keyed by backend node id.
        ax_map: dict[int, dict] = {}
        for ax_node in ax_result.get("nodes", []):
            if ax_node.get("ignored"):
                continue
            bid = ax_node.get("backendDOMNodeId")
            if not bid:
                continue
            ax_map[bid] = {
                "role": ax_node.get("role", {}).get("value", ""),
                "name": ax_node.get("name", {}).get("value", ""),
                "props": {
                    p["name"]: p.get("value", {}).get("value")
                    for p in ax_node.get("properties", [])
                },
            }
        ax_nodes = [
            AccessibilityNode(
                backend_node_id=bid,
                role=info["role"] or "",
                name=info["name"] or "",
                focusable=bool(info["props"].get("focusable")),
                editable=bool(info["props"].get("editable")),
            )
            for bid, info in ax_map.items()
        ]

        def doc_scroll(doc: dict) -> tuple[float, float]:
            return (
                float(doc.get("scrollOffsetX", 0)) / dpr,
                float(doc.get("scrollOffsetY", 0)) / dpr,
            )

        # child document index -> (parent document index, iframe local x, y);
        # per-document set of node indices that own a same-process content doc;
        # child document index -> hosting iframe element's backend id;
        # child documents whose hosting iframe is hidden (skipped entirely).
        owner: dict[int, tuple[int, float, float]] = {}
        content_iframe_nodes: dict[int, set[int]] = {}
        iframe_backend_for_doc: dict[int, int] = {}
        hidden_child_docs: set[int] = set()
        for parent_idx, doc in enumerate(docs):
            nodes = doc.get("nodes", {})
            layout = doc.get("layout", {})
            backend = nodes.get("backendNodeId", [])
            cdi = nodes.get("contentDocumentIndex", {})
            n2l = {ni: li for li, ni in enumerate(layout.get("nodeIndex", []))}
            bounds = layout.get("bounds", [])
            styles = layout.get("styles", [])
            content_iframe_nodes[parent_idx] = set(cdi.get("index", []))
            for ni, child in zip(cdi.get("index", []), cdi.get("value", [])):
                # Skip the whole child document when its hosting <iframe> element
                # is not actually visible — display:none, visibility:hidden,
                # ~transparent, or collapsed to zero size. Google's apps/one-bar
                # menu, for instance, is a same-process iframe kept in the DOM at
                # height:0 + visibility:hidden while the menu is closed; without
                # this gate its (invisible) contents get grounded.
                li = n2l.get(ni)
                if li is None or li >= len(bounds) or len(bounds[li]) < 4:
                    hidden_child_docs.add(child)
                    continue
                iw, ih = bounds[li][2] / dpr, bounds[li][3] / dpr
                srow = styles[li] if li < len(styles) else []
                display = s(srow[_D]) if _D < len(srow) else ""
                visibility = s(srow[_V]) if _V < len(srow) else ""
                try:
                    opacity = float(s(srow[_O]) or "1") if _O < len(srow) else 1.0
                except (ValueError, TypeError):
                    opacity = 1.0
                if (
                    display == "none"
                    or visibility == "hidden"
                    or opacity <= 0.01
                    or iw < 2
                    or ih < 2
                ):
                    hidden_child_docs.add(child)
                    continue
                if ni < len(backend):
                    iframe_backend_for_doc[child] = backend[ni]
                owner[child] = (parent_idx, bounds[li][0] / dpr, bounds[li][1] / dpr)

        origin_cache: dict[int, tuple[float, float]] = {}

        def origin(di: int) -> tuple[float, float]:
            if di in origin_cache:
                return origin_cache[di]
            if di == 0 or di not in owner:
                sx, sy = (
                    (top_scroll_x, top_scroll_y) if di == 0 else doc_scroll(docs[di])
                )
                value = (base_x - sx, base_y - sy)
            else:
                parent_idx, ix, iy = owner[di]
                px, py = origin(parent_idx)
                sx, sy = doc_scroll(docs[di])
                value = (ix + px - sx, iy + py - sy)
            origin_cache[di] = value
            return value

        main_frame_id = s(docs[0].get("frameId", -1))
        all_elements: list[Element] = []
        oopifs: list[tuple[int, float, float, float, float]] = []
        parents: dict[int, int | None] = {}
        for di, doc in enumerate(docs):
            if di in hidden_child_docs:
                continue  # hosting iframe is hidden — its content isn't visible
            ox, oy = origin(di)
            frame_id = s(doc.get("frameId", -1)) or main_frame_id
            # This document's root chains up to its hosting iframe: the given
            # `owner_backend` for the top document, or the same-process iframe
            # element that owns a child document.
            doc_owner = owner_backend if di == 0 else iframe_backend_for_doc.get(di)
            elements, frame_oopifs, doc_parents = self._parse_document(
                doc, s, ax_map, viewport, dpr, ox, oy,
                top_scroll_x, top_scroll_y, frame_id, session_id,
                content_iframe_nodes.get(di, set()), doc_owner,
            )
            all_elements.extend(elements)
            oopifs.extend(frame_oopifs)
            parents.update(doc_parents)
        return all_elements, ax_nodes, main_frame_id, oopifs, parents

    def _parse_document(
        self,
        doc: dict,
        s,
        ax_map: dict[int, dict],
        viewport: tuple[int, int],
        dpr: float,
        origin_x: float,
        origin_y: float,
        top_scroll_x: float,
        top_scroll_y: float,
        frame_id: str,
        session_id: str,
        content_iframe_node_indices: set[int],
        doc_owner: int | None,
    ) -> tuple[
        list[Element],
        list[tuple[int, float, float, float, float]],
        dict[int, int | None],
    ]:
        """Per-document interactive-element detection. `origin_x/y`
        shift local document coordinates into top-viewport space (0,0 for the
        main frame minus scroll; the accumulated iframe offset for children).

        Also returns the cross-origin iframe elements in this document —
        `<iframe>`/`<frame>` nodes whose content lives in a separate process
        (i.e. not among `content_iframe_node_indices`, the same-process ones) —
        as (backend id, viewport x, y, w, h) for the caller to recurse into, and
        the element-parent map (every element's nearest element-ancestor backend
        id; document roots chain to `doc_owner`, the hosting iframe) used to
        build the interactive tree."""
        nodes = doc.get("nodes", {})
        layout = doc.get("layout", {})
        vw, vh = viewport

        node_names = nodes.get("nodeName", [])
        node_types = nodes.get("nodeType", [])
        node_parent = nodes.get("parentIndex", [])
        node_backend = nodes.get("backendNodeId", [])
        node_attrs = nodes.get("attributes", [])
        node_values = nodes.get("nodeValue", [])
        clickable_set = set(nodes.get("isClickable", {}).get("index", []))

        # Full element-parent map: every element node's nearest element-ancestor
        # backend id (skipping non-element nodes). Document-root elements chain
        # to `doc_owner` (the hosting iframe). Covers dropped intermediates so
        # the tree builder can find each kept element's nearest kept ancestor.
        parents: dict[int, int | None] = {}
        for ni in range(len(node_names)):
            if ni >= len(node_types) or node_types[ni] != 1:
                continue
            bid = node_backend[ni] if ni < len(node_backend) else 0
            if not bid:
                continue
            p = node_parent[ni] if ni < len(node_parent) else -1
            parent_backend: int | None = None
            while p is not None and p >= 0:
                if p < len(node_types) and node_types[p] == 1 and p < len(node_backend):
                    parent_backend = node_backend[p]
                    break
                p = node_parent[p] if p < len(node_parent) else -1
            parents[bid] = parent_backend if parent_backend is not None else doc_owner

        # Shadow DOM: captureSnapshot marks each shadow-content node with its
        # containing shadow root's type ("open"/"closed"/"user-agent"); the
        # host element itself is not marked. A node is a shadow host when a
        # direct child is shadow content while it is not.
        shadow = nodes.get("shadowRootType", {})
        shadow_type: dict[int, str] = {
            ni: s(v) for ni, v in zip(shadow.get("index", []), shadow.get("value", []))
        }

        layout_nodes = layout.get("nodeIndex", [])
        layout_bounds_raw = layout.get("bounds", [])
        layout_styles_raw = layout.get("styles", [])
        node_to_layout = {ni: li for li, ni in enumerate(layout_nodes)}

        children_map: dict[int, list[int]] = {}
        for i, p in enumerate(node_parent):
            if p >= 0:
                children_map.setdefault(p, []).append(i)

        def shadow_root_type_for(ni: int) -> str | None:
            """Nearest containing shadow root's type — this node's own mark, or
            the nearest marked ancestor's (robust to Chrome marking only the
            shadow boundary rather than every content node)."""
            cur = ni
            guard = 0
            while cur is not None and cur >= 0 and guard < 200:
                if cur in shadow_type:
                    return shadow_type[cur]
                cur = node_parent[cur] if cur < len(node_parent) else -1
                guard += 1
            return None

        def is_shadow_host(ni: int) -> bool:
            if ni in shadow_type:
                return False
            return any(child in shadow_type for child in children_map.get(ni, ()))

        _it_cache: dict[int, str] = {}

        def inline_text(ni: int) -> str:
            if ni in _it_cache:
                return _it_cache[ni]
            parts: list[str] = []
            for c in children_map.get(ni, []):
                if c >= len(node_types):
                    continue
                if node_types[c] == 3:  # text node
                    idx = node_values[c] if c < len(node_values) else -1
                    t = s(idx).strip() if isinstance(idx, int) and idx >= 0 else ""
                    if t:
                        parts.append(t)
                elif node_types[c] == 1:
                    ctag = s(node_names[c]).lower() if c < len(node_names) else ""
                    if ctag in INLINE_ELEMENTS:
                        t = inline_text(c)
                        if t:
                            parts.append(t)
            result = " ".join(parts)
            _it_cache[ni] = result
            return result

        def _is_own_control(c: int) -> bool:
            """Is this descendant itself an interactive control? descendant_text
            stops at it: its label belongs to it (it is grounded and named on
            its own), not to an enclosing container. Without this a landmark
            wrapper (e.g. Wikipedia's <div role=search>) would adopt its nested
            <button>Search</button>'s text as its own name and then dominate the
            real button in the name-dominance dedup."""
            ctag = s(node_names[c]).lower() if c < len(node_names) else ""
            if ctag in INTERACTIVE_TAGS:
                return True
            raw = node_attrs[c] if c < len(node_attrs) else []
            for j in range(0, len(raw) - 1, 2):
                k = s(raw[j])
                if k == "role" and s(raw[j + 1]) in INTERACTIVE_ROLES:
                    return True
                if k in ("onclick", "href"):
                    return True
            return False

        def descendant_text(ni: int, limit: int = 120) -> str:
            """Full subtree text (every descendant, not just inline children) —
            a *naming* fallback for interactive controls whose visible label
            lives inside block-level descendants that inline_text() can't reach.
            Amazon's style swatches are `<span class=a-button>` (cursor:pointer,
            no role, empty AX name) whose label sits inside a `<div>` section, so
            inline_text() returns "" and the control looks nameless. Subtrees of
            nested interactive controls are skipped (their text is their own name,
            not the container's), and the result is capped in both breadth and
            length so a large clickable container can't absorb a whole section."""
            parts: list[str] = []
            stack = list(children_map.get(ni, ()))
            total = 0
            guard = 0
            while stack and guard < 400:
                guard += 1
                c = stack.pop(0)
                if c >= len(node_types):
                    continue
                if node_types[c] == 3:
                    idx = node_values[c] if c < len(node_values) else -1
                    t = s(idx).strip() if isinstance(idx, int) and idx >= 0 else ""
                    if t:
                        parts.append(t)
                        total += len(t)
                        if total >= limit:
                            break
                elif node_types[c] == 1 and not _is_own_control(c):
                    stack.extend(children_map.get(c, ()))
            return " ".join(parts)[:limit].strip()

        def get_bounds(li: int):
            if li >= len(layout_bounds_raw):
                return None
            rect = layout_bounds_raw[li]
            if not rect or len(rect) < 4:
                return None
            return (rect[0] / dpr, rect[1] / dpr, rect[2] / dpr, rect[3] / dpr)

        def get_style(li: int, si: int) -> str:
            row = layout_styles_raw[li] if li < len(layout_styles_raw) else []
            return s(row[si]) if si < len(row) else ""

        # Effective (multiplicative) opacity up the ancestor chain. CSS opacity
        # is NOT inherited in computed style — each node reports its own — but
        # the visual result multiplies down the tree, so a link with own
        # opacity 1 inside a container with opacity 0 is invisible yet passes an
        # own-opacity check (and stays hit-testable, so the coverage check keeps
        # it too). This catches hidden dropdowns/flyouts kept in the DOM at
        # opacity 0. Memoised; walks up parentIndex.
        _opacity_cache: dict[int, float] = {}

        def effective_opacity(ni: int) -> float:
            if ni in _opacity_cache:
                return _opacity_cache[ni]
            own = 1.0
            own_li = node_to_layout.get(ni)
            if own_li is not None:
                try:
                    own = float(get_style(own_li, _O) or "1")
                except ValueError:
                    own = 1.0
            parent = node_parent[ni] if ni < len(node_parent) else -1
            value = own * (effective_opacity(parent) if 0 <= parent < len(node_names) else 1.0)
            _opacity_cache[ni] = value
            return value

        interactive: list[Element] = []
        interactive_name_by_ni: dict[int, str] = {}
        interactive_strong_by_ni: dict[int, bool] = {}
        oopifs: list[tuple[int, float, float, float, float]] = []

        for ni in range(len(node_names)):
            if ni < len(node_types) and node_types[ni] != 1:
                continue

            tag = s(node_names[ni]).lower()
            if not tag or tag in EXCLUDED_TAGS or tag.startswith("#"):
                continue

            li = node_to_layout.get(ni)
            if li is None:
                continue
            bounds = get_bounds(li)
            if bounds is None:
                continue
            local_x, local_y, w, h = bounds

            if get_style(li, _D) == "none":
                continue
            if get_style(li, _V) == "hidden":
                continue
            # Invisible via its own or any ancestor's opacity (hidden flyout).
            if effective_opacity(ni) <= 0.01:
                continue

            # Local coordinates -> top-viewport space via the frame origin.
            vx = local_x + origin_x
            vy = local_y + origin_y

            # Cross-origin iframe: content is in another process (not among the
            # same-process ones). Record it for the caller to recurse into.
            if tag in ("iframe", "frame") and ni not in content_iframe_node_indices:
                oopifs.append(
                    (node_backend[ni] if ni < len(node_backend) else 0, vx, vy, w, h)
                )
                continue

            if w < 10 or h < 10:
                continue

            position = get_style(li, _P)
            if position not in ("fixed", "sticky"):
                if vy + h < -200 or vy > vh + 200 or vx + w < -200 or vx > vw + 200:
                    continue

            bid = node_backend[ni] if ni < len(node_backend) else None
            ax = ax_map.get(bid, {}) if bid else {}
            ax_role = ax.get("role", "")
            ax_name = ax.get("name", "")
            ax_props = ax.get("props", {})

            raw_attrs = node_attrs[ni] if ni < len(node_attrs) else []
            attrs: dict[str, str] = {}
            for j in range(0, len(raw_attrs) - 1, 2):
                k = s(raw_attrs[j])
                if k in SAFE_ATTRIBUTES:
                    attrs[k] = s(raw_attrs[j + 1])

            if attrs.get("aria-hidden") == "true":
                continue

            cursor = get_style(li, _C)
            is_interactive = (
                tag in INTERACTIVE_TAGS
                or ax_role in INTERACTIVE_ROLES
                or cursor == "pointer"
                or ni in clickable_set
                or ax_props.get("focusable") is True
                or bool(ax_props.get("editable"))
                or bool(ax_props.get("settable"))
                # State properties only exist on interactive widgets, so their
                # mere presence signals an interactive custom control (e.g. a
                # <div aria-expanded> accordion toggle with no role/tag).
                or any(p in ax_props for p in ("checked", "expanded", "pressed", "selected"))
                or bool(ax_props.get("required"))
                or bool(ax_props.get("autocomplete"))
                or bool(ax_props.get("keyshortcuts"))
                or "onclick" in attrs
                or "href" in attrs
                or attrs.get("contenteditable") in ("true", "", "plaintext-only")
                or (attrs.get("tabindex", "-1") not in ("-1", ""))
                or _has_search_indicator(attrs)
            )
            if not is_interactive:
                continue

            # A *strong* interactivity signal is a real interactive tag/role, an
            # href, an onclick, or contenteditable — versus *weak* signals
            # (inherited cursor:pointer, the CDP isClickable hint, a bare
            # tabindex) that landmark/layout containers pick up incidentally.
            strong = (
                tag in INTERACTIVE_TAGS
                or ax_role in INTERACTIVE_ROLES
                or "href" in attrs
                or "onclick" in attrs
                or attrs.get("contenteditable") in ("true", "", "plaintext-only")
            )

            inner_text = inline_text(ni)
            name = (
                ax_name
                or attrs.get("aria-label")
                or attrs.get("title")
                or attrs.get("placeholder")
                or attrs.get("name")
                or inner_text
                # Label may live in block-level descendants (e.g. Amazon's
                # <span class=a-button> swatches wrap their text in a <div>).
                or descendant_text(ni)
                or ""
            )
            if not name:
                # No accessible name. Keep it only when it carries a strong
                # signal (icon-only links/buttons, e.g. a row of SVG
                # social-share links with no label); nameless weak elements stay
                # dropped as layout noise.
                if not strong:
                    continue

            # Name-dominance dedup: drop a child when an interactive ancestor
            # already carries the same accessible name. Only named elements
            # participate — an empty name must never dominate another. A *weak*
            # ancestor (interactive only via cursor/isClickable/tabindex) must
            # not dominate a *strong* descendant, or a landmark/layout wrapper
            # that incidentally shares a control's label (e.g. Wikipedia's
            # <div role=search>, named "Search" from a nested link, wrapping the
            # real <button>Search</button>) would swallow the actual control.
            if name:
                dominated = False
                cur = node_parent[ni] if ni < len(node_parent) else -1
                while cur >= 0:
                    if interactive_name_by_ni.get(cur) == name and (
                        interactive_strong_by_ni.get(cur, False) or not strong
                    ):
                        dominated = True
                        break
                    cur = node_parent[cur] if cur < len(node_parent) else -1
                if dominated:
                    continue
                interactive_name_by_ni[ni] = name
                interactive_strong_by_ni[ni] = strong
            parent_backend = (
                node_backend[node_parent[ni]]
                if 0 <= (node_parent[ni] if ni < len(node_parent) else -1) < len(node_backend)
                else None
            )
            interactive.append(
                Element(
                    backend_node_id=bid or 0,
                    tag_name=tag,
                    text=inner_text or name,
                    attributes=attrs,
                    bounds=Bounds(
                        x=vx,
                        y=vy,
                        width=w,
                        height=h,
                        document_x=vx + top_scroll_x,
                        document_y=vy + top_scroll_y,
                    ),
                    clickable=True,
                    scrollable=False,
                    depth=0,
                    parent_backend_node_id=parent_backend,
                    accessibility=ax_map_to_node(bid, ax) if bid else None,
                    frame_id=frame_id,
                    session_id=session_id,
                    shadow_root_type=shadow_root_type_for(ni),
                    is_shadow_host=is_shadow_host(ni),
                    strongly_interactive=True,
                )
            )

        return interactive, oopifs, parents


def _ax_tri(value: Any) -> bool | None:
    """Coerce a CDP AX boolean-ish property value to True / False / None."""
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    return None


def _ax_str(value: Any) -> str:
    """A meaningful AX string property, or "" for absent/false/none values."""
    if not value or value in ("false", "none"):
        return ""
    return str(value)


def ax_map_to_node(bid: int, ax: dict) -> AccessibilityNode:
    props = ax.get("props", {})
    return AccessibilityNode(
        backend_node_id=bid,
        role=ax.get("role", "") or "",
        name=ax.get("name", "") or "",
        focusable=bool(props.get("focusable")),
        editable=bool(props.get("editable")),
        disabled=_ax_tri(props.get("disabled")) is True,
        focused=_ax_tri(props.get("focused")) is True,
        required=_ax_tri(props.get("required")) is True,
        # checked/pressed keep their raw tri-state ("true"/"false"/"mixed") so a
        # tristate checkbox/toggle can render "mixed"; expanded/selected are
        # plain booleans.
        checked=props.get("checked"),
        pressed=props.get("pressed"),
        expanded=_ax_tri(props.get("expanded")),
        selected=_ax_tri(props.get("selected")),
        haspopup=_ax_str(props.get("haspopup")),
        autocomplete=_ax_str(props.get("autocomplete")),
        keyshortcuts=_ax_str(props.get("keyshortcuts")),
        value=props.get("value"),
    )


async def _empty_ax() -> dict:
    return {"nodes": []}


def _as_float(value: Any) -> float:
    """Coerce a settings value to a float, returning 0.0 for anything that
    isn't a real number (so a MagicMock settings object disables settling)."""
    return float(value) if isinstance(value, (int, float)) else 0.0


_STRONG_INTERACTIVE_TAGS = {
    "a", "button", "input", "select", "textarea", "summary", "option",
}


def _is_strong_element(element: Element) -> bool:
    """Whether an element is interactive in its own right — a real interactive
    tag/role, an href/onclick, or contenteditable — as opposed to being merely
    clickable via an inherited signal (cursor:pointer / CDP hint / its text)."""
    attrs = element.attributes
    role = attrs.get("role", "").lower() or (
        element.accessibility.role.lower() if element.accessibility else ""
    )
    return (
        element.tag_name in _STRONG_INTERACTIVE_TAGS
        or role in INTERACTIVE_ROLES
        or "href" in attrs
        or "onclick" in attrs
        or attrs.get("contenteditable") in ("true", "", "plaintext-only")
    )


def _is_link_or_button(element: Element) -> bool:
    """A genuine navigational/action control that a click bubbles up to — an
    <a>/<button>, an href/onclick, or role=link/button. A weak element nested
    inside one of these is redundant: clicking it activates this ancestor."""
    attrs = element.attributes
    role = attrs.get("role", "").lower() or (
        element.accessibility.role.lower() if element.accessibility else ""
    )
    return (
        element.tag_name in ("a", "button")
        or "href" in attrs
        or "onclick" in attrs
        or role in ("link", "button")
    )


def _collapse_contained_duplicates(
    elements: list[Element], parents: dict[int, int | None]
) -> list[Element]:
    """Collapse a kept element that is (near-)fully contained within its
    nearest kept DOM ancestor into whichever of the two is the "real" control.

    Name-dominance dedup only merges elements with identical accessible names,
    so it misses stacked pieces of one result/card. This pass works over true
    DOM ancestor/descendant pairs (nearest kept ancestor):

      1. A weak descendant (clickable only via inherited cursor/CDP hint — no
         href/onclick/interactive tag/role of its own) nested anywhere inside a
         genuine link/button ancestor is dropped regardless of geometry: a
         click on it just activates the ancestor. This folds a search result's
         site-name, title, and inline spans into the one result link.
      2. Otherwise, for pairs with ≥90% geometric containment, drop the weaker:
         a weak inner piece of a strong wrapper, a weak wrapper around a real
         control, or the larger of two weak boxes.

    Two genuinely distinct strong controls (a real button inside a real link)
    are always both kept.
    """
    kept = {e.backend_node_id: e for e in elements}

    def nearest_kept(backend_id: int) -> int | None:
        cur = parents.get(backend_id)
        seen: set[int] = set()
        while cur is not None and cur not in seen:
            if cur in kept:
                return cur
            seen.add(cur)
            cur = parents.get(cur)
        return None

    def frac_contained(outer: Bounds, inner: Bounds) -> float:
        ix0, iy0 = max(outer.x, inner.x), max(outer.y, inner.y)
        ix1 = min(outer.x + outer.width, inner.x + inner.width)
        iy1 = min(outer.y + outer.height, inner.y + inner.height)
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        area = inner.width * inner.height
        return inter / area if area else 0.0

    drop: set[int] = set()
    for element in elements:
        if element.backend_node_id in drop:
            continue
        ancestor_id = nearest_kept(element.backend_node_id)
        if ancestor_id is None or ancestor_id in drop:
            continue
        ancestor = kept[ancestor_id]
        inner_strong = _is_strong_element(element)
        outer_strong = _is_strong_element(ancestor)
        # 1. Weak element inside a genuine link/button -> redundant, drop it.
        if not inner_strong and _is_link_or_button(ancestor):
            drop.add(element.backend_node_id)
            continue
        # 2. Otherwise require geometric containment.
        if frac_contained(ancestor.bounds, element.bounds) < 0.9:
            continue
        if outer_strong and not inner_strong:
            drop.add(element.backend_node_id)  # weak inner piece of a real control
        elif inner_strong and not outer_strong:
            drop.add(ancestor_id)  # weak wrapper around a real control
        elif not inner_strong and not outer_strong:
            drop.add(ancestor_id)  # both weak — keep the tighter inner box
        # both strong: two distinct controls, keep both
    return [e for e in elements if e.backend_node_id not in drop]


def _build_interactive_tree(
    elements: list[Element], parents: dict[int, int | None]
) -> tuple[DOMTreeNode, ...]:
    """Nest the kept interactive elements into a forest by DOM ancestry.

    `parents` maps every element's backend id to its nearest element-ancestor's
    backend id (spanning same- and cross-origin iframe boundaries via the
    hosting `<iframe>`, and shadow boundaries, since both are captured in the
    same flattened parent chain). Each kept element is attached under its
    nearest *kept* ancestor; elements with none become roots. Backend ids are
    globally unique in CDP, so cross-frame ids never collide. Document order is
    preserved because `elements` arrives in document order."""
    kept = {e.backend_node_id for e in elements}

    def nearest_kept(backend_id: int) -> int | None:
        cur = parents.get(backend_id)
        seen: set[int] = set()
        while cur is not None and cur not in seen:
            if cur in kept:
                return cur
            seen.add(cur)
            cur = parents.get(cur)
        return None

    children: dict[int, list[Element]] = {e.backend_node_id: [] for e in elements}
    roots: list[Element] = []
    for element in elements:
        parent = nearest_kept(element.backend_node_id)
        if parent is not None and parent != element.backend_node_id:
            children[parent].append(element)
        else:
            roots.append(element)

    def build(element: Element) -> DOMTreeNode:
        return DOMTreeNode(
            element=element,
            children=tuple(build(child) for child in children[element.backend_node_id]),
        )

    return tuple(build(root) for root in roots)
