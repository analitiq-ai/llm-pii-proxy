"""Errors the proxy answers with itself (contracts/http-interface.md, "Proxy-originated errors").

Claude Code shows the cause to the user, so it must never contain request content, values or tokens.
"""

from typing import Any, ClassVar


class ProxyError(Exception):
    status: ClassVar[int]
    error_type: ClassVar[str]
    retry_after: ClassVar[int | None] = None

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause

    def body(self) -> dict[str, Any]:
        return {
            "type": "error",
            "error": {"type": self.error_type, "message": f"pii-proxy: {self.cause}"},
        }

    def headers(self) -> dict[str, str]:
        return {} if self.retry_after is None else {"retry-after": str(self.retry_after)}


class BadRequest(ProxyError):
    status = 400
    error_type = "invalid_request_error"


class ScrubFailed(ProxyError):
    status = 400
    error_type = "invalid_request_error"


class UnsupportedEncoding(ProxyError):
    status = 415
    error_type = "invalid_request_error"


class RouteNotFound(ProxyError):
    status = 404
    error_type = "not_found_error"


class SecretDetectorUnavailable(ProxyError):
    status = 503
    error_type = "api_error"


class ConfidentialUnavailable(ProxyError):
    status = 503
    error_type = "api_error"
    retry_after = 5


class ConfidentialInvalid(ProxyError):
    status = 502
    error_type = "api_error"


class UpstreamUnavailable(ProxyError):
    status = 502
    error_type = "api_error"
