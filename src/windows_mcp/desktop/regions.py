"""Clip desktop state to a capture region (a display selection or an explicit rectangle).

All functions are pure: they return filtered copies and never touch UI Automation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from windows_mcp.desktop.views import Window
from windows_mcp.tree.views import BoundingBox, SemanticNode, TreeElementNode

if TYPE_CHECKING:
    import windows_mcp.uia as uia


def rect_to_bounding_box(rect: uia.Rect | None) -> BoundingBox | None:
    if rect is None:
        return None
    return BoundingBox.from_bounding_rectangle(rect)


def point_in_region(point: tuple[int, int], region: BoundingBox) -> bool:
    x, y = point
    return region.left <= x < region.right and region.top <= y < region.bottom


def clip_bounding_box(
    box: BoundingBox | None, region: BoundingBox
) -> BoundingBox | None:
    if box is None:
        return None
    left = max(box.left, region.left)
    top = max(box.top, region.top)
    right = min(box.right, region.right)
    bottom = min(box.bottom, region.bottom)
    if right <= left or bottom <= top:
        return None
    return BoundingBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        width=right - left,
        height=bottom - top,
    )


def filter_window(window: Window | None, region: BoundingBox) -> Window | None:
    if window is None:
        return None
    clipped_box = clip_bounding_box(window.bounding_box, region)
    if clipped_box is None:
        return None
    return Window(
        name=window.name,
        is_browser=window.is_browser,
        depth=window.depth,
        status=window.status,
        bounding_box=clipped_box,
        handle=window.handle,
        process_id=window.process_id,
    )


def filter_windows(windows: list[Window], region: BoundingBox) -> list[Window]:
    filtered_windows: list[Window] = []
    for window in windows:
        filtered_window = filter_window(window, region)
        if filtered_window is not None:
            filtered_windows.append(filtered_window)
    return filtered_windows


def filter_tree_node(
    node: TreeElementNode, region: BoundingBox
) -> TreeElementNode | None:
    clipped_box = clip_bounding_box(node.bounding_box, region)
    if clipped_box is None:
        return None
    return TreeElementNode(
        name=node.name,
        control_type=node.control_type,
        window_name=node.window_name,
        bounding_box=clipped_box,
        center=clipped_box.get_center(),
        metadata=node.metadata,
    )


def filter_scroll_node(node, region: BoundingBox):
    clipped_box = clip_bounding_box(node.bounding_box, region)
    if clipped_box is None:
        return None
    return node.__class__(
        name=node.name,
        control_type=node.control_type,
        window_name=node.window_name,
        bounding_box=clipped_box,
        center=clipped_box.get_center(),
        metadata=node.metadata,
    )


def filter_semantic_node(
    node: SemanticNode | None,
    region: BoundingBox,
) -> SemanticNode | None:
    if node is None:
        return None

    clipped_box = None
    if node.bounding_box is not None:
        clipped_box = clip_bounding_box(node.bounding_box, region)
        if clipped_box is None:
            return None

    filtered_children = []
    for child in node.children:
        filtered_child = filter_semantic_node(child, region)
        if filtered_child is not None:
            filtered_children.append(filtered_child)

    if node.element_type != "desktop" and clipped_box is None and not filtered_children:
        return None

    filtered_node = SemanticNode(
        control_type=node.control_type,
        element_type=node.element_type,
        name=node.name,
        window_name=node.window_name,
        center=clipped_box.get_center() if clipped_box is not None else node.center,
        bounding_box=clipped_box,
        metadata=dict(node.metadata),
    )
    filtered_node.children = filtered_children
    return filtered_node


def filter_tree_state(tree_state, region: BoundingBox):
    filtered_interactive_nodes = []
    for node in tree_state.interactive_nodes:
        filtered_node = filter_tree_node(node, region)
        if filtered_node is not None:
            filtered_interactive_nodes.append(filtered_node)

    filtered_scrollable_nodes = []
    for node in tree_state.scrollable_nodes:
        filtered_node = filter_scroll_node(node, region)
        if filtered_node is not None:
            filtered_scrollable_nodes.append(filtered_node)

    filtered_dom_node = None
    if tree_state.dom_node is not None:
        filtered_dom_node = filter_scroll_node(tree_state.dom_node, region)

    filtered_semantic_root = filter_semantic_node(
        tree_state.semantic_tree_root,
        region,
    )
    if filtered_semantic_root is not None:
        filtered_semantic_root.bounding_box = region
        filtered_semantic_root.center = region.get_center()

    return tree_state.__class__(
        status=tree_state.status,
        root_node=TreeElementNode(
            name="Desktop",
            control_type="PaneControl",
            bounding_box=region,
            center=region.get_center(),
            window_name="Desktop",
            metadata={},
        ),
        dom_node=filtered_dom_node,
        interactive_nodes=filtered_interactive_nodes,
        scrollable_nodes=filtered_scrollable_nodes,
        dom_informative_nodes=tree_state.dom_informative_nodes if filtered_dom_node else [],
        capture_sec=tree_state.capture_sec,
        semantic_tree_root=filtered_semantic_root,
        truncated=tree_state.truncated,
        element_limit=tree_state.element_limit,
    )
