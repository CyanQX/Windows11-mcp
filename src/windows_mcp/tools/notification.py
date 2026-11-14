"""Notification tool — Windows toast notifications."""

from functools import partial
from typing import Annotated

from pydantic import Field
from fastmcp import Context
from windows_mcp import notifications
from windows_mcp.tools._common import declare_tool


def register(mcp, *, get_desktop, get_analytics):
    tool = partial(declare_tool, mcp, get_analytics)

    @tool(
        "Notification",
        description="Sends a Windows toast notification with a title and message.",
        access="write",
        analytics="Notification-Tool",
        offload="background",
    )
    def notification_tool(
        title: Annotated[
            str,
            Field(description="The title/heading of the toast notification."),
        ],
        message: Annotated[
            str,
            Field(description="The body text of the toast notification displayed below the title."),
        ],
        app_id: Annotated[
            str,
            Field(
                description="The valid Application User Model ID of the toast notification. Required to display the notification in a specific app.",
            ),
        ],
        ctx: Context = None,
    ) -> str:
        try:
            return notifications.send_notification(title, message, app_id)
        except Exception:
            raise
