"""Safety checks at the SDK synthetic-error boundary; no network requests."""

import pytest

from lisca.asr.recognition_process import RecognitionProviderError, _sdk_failure_kind
from lisca.provider_errors import classify_provider_failure


@pytest.mark.parametrize(("exception_type", "kind"), [
    ("TimeoutError", "timeout"),
    ("asyncio.exceptions.TimeoutError", "timeout"),
    ("aiohttp.client_exceptions.ServerTimeoutError", "timeout"),
    ("aiohttp.client_exceptions.ConnectionTimeoutError", "timeout"),
    ("aiohttp.client_exceptions.SocketTimeoutError", "timeout"),
    ("aiohttp.client_exceptions.ClientOSError", "connection"),
    ("aiohttp.client_exceptions.ClientConnectionResetError", "connection"),
    ("aiohttp.client_exceptions.ServerDisconnectedError", "connection"),
    ("json.decoder.JSONDecodeError", "protocol"),
])
def test_sdk_generated_class_prefix_is_reduced_before_message_is_discarded(exception_type, kind):
    message = f"Error type: <class '{exception_type}'>, message: private-key-and-transcript"
    diagnostic = _sdk_failure_kind(-1, "Unknown", message)
    assert diagnostic == kind
    details = classify_provider_failure(RecognitionProviderError(-1, "Unknown", diagnostic))
    assert details["sdk_failure_kind"] == kind
    assert details["sdk_failure_source"] == "dashscope_exception_type"
    assert details["failure_category"] == "unknown"
    assert details["retryable"] is False
    assert details["provider_status"] is None
    assert details["provider_code"] is None
    assert "private" not in str(details)
    assert exception_type not in str(details)


class UntrustedMessage:
    def __str__(self):
        raise AssertionError("arbitrary message objects must never be converted")


class UntrustedString(str):
    def startswith(self, *_args):
        raise AssertionError("message subclass methods must never run")


_PREFIX = "Error type: <class 'json.decoder.JSONDecodeError'>, message: "


@pytest.mark.parametrize(("status", "code", "message"), [
    (44, "Unknown", _PREFIX + "private"),
    (-1, "Model.AccessDenied", _PREFIX + "private"),
    (-1, "Unknown", UntrustedMessage()),
    (-1, "Unknown", UntrustedString(_PREFIX + "private")),
    (-1, "Unknown", b"Error type: <class 'TimeoutError'>, message: private"),
    (-1, "Unknown", "private timeout connection JSONDecodeError"),
    (-1, "Unknown", "private " + _PREFIX),
    (-1, "Unknown", "Error type: <class 'TimeoutError'>, private"),
    (-1, "Unknown", "Error type: <class 'dashscope.common.error.UnexpectedMessageReceived'>, "
                     "message: private"),
    (-1, "Unknown", "Error type: <class 'private.Class'>, message: private"),
])
def test_unsupported_sdk_error_shapes_keep_unknown_without_inspecting_text(status, code, message):
    assert _sdk_failure_kind(status, code, message) is None


@pytest.mark.parametrize(("status", "code", "kind"), [
    (403, "Model.AccessDenied", "protocol"),
    (-1, "private-provider-code", "protocol"),
    (-1, "Unknown", "private-diagnostic"),
    (-1, "Unknown", UntrustedMessage()),
])
def test_public_diagnostics_reject_untrusted_fields(status, code, kind):
    details = classify_provider_failure(RecognitionProviderError(status, code, kind))
    assert "sdk_failure_kind" not in details
    assert "sdk_failure_source" not in details
    assert "private" not in str(details)
