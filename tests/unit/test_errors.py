import pytest

from llm_pii_proxy import errors

# Rows of the "Proxy-originated errors" table in contracts/http-interface.md.
TABLE = [
    (errors.BadRequest, 400, "invalid_request_error", None),
    (errors.ScrubFailed, 400, "invalid_request_error", None),
    (errors.SecretDetectorUnavailable, 503, "api_error", None),
    (errors.UnsupportedEncoding, 415, "invalid_request_error", None),
    (errors.RouteNotFound, 404, "not_found_error", None),
    (errors.ConfidentialUnavailable, 503, "api_error", "5"),
    (errors.ConfidentialInvalid, 502, "api_error", None),
    (errors.UpstreamUnavailable, 502, "api_error", None),
]


@pytest.mark.parametrize("cls,status,error_type,retry_after", TABLE)
def test_error_table(cls, status, error_type, retry_after):
    err = cls("some cause")
    assert isinstance(err, errors.ProxyError)
    assert err.status == status
    assert err.body() == {
        "type": "error",
        "error": {"type": error_type, "message": "pii-proxy: some cause"},
    }
    assert err.headers().get("retry-after") == retry_after
