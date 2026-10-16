"""Errors and texts shared by the configuration, the engine modules, the CLI and the start-up stub (standard library only)."""
from __future__ import annotations

RELOAD = "the `plugin:dbwise:postgres` server in `/mcp` or start a new session to load the database tools."


class FieldError(ValueError):
    """A server field that breaks its engine's rules, with the JSON path of the field."""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message
