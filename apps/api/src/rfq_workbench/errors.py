"""Stable errors suitable for UI display without leaking provider details."""


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, metrics: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.metrics = metrics or {}
