from __future__ import annotations
from enum import Enum


class ErrorCode(str, Enum):
    VERSION_CONFLICT = "version_conflict"      # live page changed since the draft's base version
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"              # retryable, honour retry_after
    TRANSIENT = "transient"                    # retryable (timeouts, 5xx)
    PERMISSION_DENIED = "permission_denied"    # outside the adapter's path scope
    APPROVAL_INVALID = "approval_invalid"      # missing, expired, forged, or for different content
    INVALID_REQUEST = "invalid_request"        # e.g. idempotency key reused for a different draft


RETRYABLE = {ErrorCode.RATE_LIMITED, ErrorCode.TRANSIENT}


class AdapterError(Exception):
    def __init__(self, code: ErrorCode, message: str = "", retry_after: float | None = None):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE
