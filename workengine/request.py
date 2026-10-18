"""Thin intake seam that creates a generic ServerOS Item."""

from __future__ import annotations

from .engine import ServerOS
from .model import WorkItem


class WorkRequest:
    def __init__(self, server: ServerOS | None = None) -> None:
        self.server = server or ServerOS()

    def submit(
        self,
        *,
        project: str,
        title: str,
        kind: str,
        card_locator: str | None = None,
    ) -> WorkItem:
        return self.server.create_item(project, title.strip() or "Untitled Item", kind, card_locator)
