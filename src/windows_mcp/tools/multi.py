"""MultiSelect and MultiEdit tools — batch element interaction."""

from functools import partial

from fastmcp import Context
from windows_mcp.coercion import json_list, truthy
from windows_mcp.tools._common import declare_tool, require_snapshot


def register(mcp, *, get_desktop, get_analytics):
    tool = partial(declare_tool, mcp, get_analytics)

    @tool(
        "MultiSelect",
        description="Selects multiple items such as files, folders, or checkboxes if press_ctrl=True, or performs multiple clicks if False. Pass locs (list of coordinates) or labels (list of UI element labels/ids).",
        access="write",
        analytics="Multi-Select-Tool",
    )
    def multi_select_tool(
        locs: list[list[int]] | str | None = None,
        labels: list[int] | str | None = None,
        press_ctrl: bool | str = True,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        locs = json_list(locs)
        labels = json_list(labels)
        if locs is None and labels is None:
            raise ValueError("Either locs or labels must be provided.")
        locs = locs or []
        if labels is not None:
            require_snapshot(desktop)
            try:
                resolved_locs = desktop.get_coordinates_from_labels(labels)
                locs.extend([list(loc) for loc in resolved_locs])
            except Exception as e:
                raise ValueError(f"Failed to resolve labels {labels}: {e}")

        desktop.multi_select(truthy(press_ctrl), locs)
        elements_str = "\n".join([f"({loc[0]},{loc[1]})" for loc in locs])
        return f"Multi-selected elements at:\n{elements_str}"

    @tool(
        "MultiEdit",
        description="Enters text into multiple input fields at specified coordinates locs=[[x,y,text], ...] or using labels=[[label,text], ...]. Provide either locs or labels.",
        access="write",
        analytics="Multi-Edit-Tool",
    )
    def multi_edit_tool(
        locs: list[list] | str | None = None,
        labels: list[list] | str | None = None,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        locs = json_list(locs)
        labels = json_list(labels)
        if locs is None and labels is None:
            raise ValueError("Either locs or labels must be provided.")
        locs = locs or []
        if labels is not None:
            require_snapshot(desktop)

            # Pre-validate and extract labels and texts
            processed_labels = []
            for item in labels:
                if len(item) != 2:
                    raise ValueError(f"Each label item must be [label, text]. Invalid: {item}")
                try:
                    processed_labels.append((int(item[0]), item[1]))
                except (ValueError, TypeError):
                    raise ValueError(f"Invalid label id in item: {item}")

            try:
                label_ids = [item[0] for item in processed_labels]
                resolved_coords = desktop.get_coordinates_from_labels(label_ids)
                for (x, y), (_, text) in zip(resolved_coords, processed_labels):
                    locs.append([x, y, text])
            except Exception as e:
                raise ValueError(f"Failed to process labels: {e}")

        desktop.multi_edit(locs)
        elements_str = ", ".join([f"({e[0]},{e[1]}) with text '{e[2]}'" for e in locs])
        return f"Multi-edited elements at: {elements_str}"
