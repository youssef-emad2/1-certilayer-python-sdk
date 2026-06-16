"""
test_client.py — L1 Python SDK
--------------------------------
Unit tests for CertiLayerClient.
Uses respx to mock httpx — no real HTTP calls made.
"""

import sys
import types
import pytest
import respx
import httpx

from certilayer import CertiLayerClient, CertiLayerError, VerifyResult, QuickCheckResult
from certilayer.types import HCSVerdict

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_URL = "https://api.certilayer.net/v1"

VALID_RESPONSE = {
    "score":         0.95,
    "verdict":       "human_verified",
    "sessionId":     "550e8400-e29b-41d4-a716-446655440000",
    "scoredAt":      "2026-06-06T02:24:00Z",
    "sessionActive": True,
}

@pytest.fixture
def client():
    return CertiLayerClient(api_key="cl_test_abc", max_retries=0)


@pytest.fixture(autouse=False)
def mock_fastapi():
    """Inject a minimal fastapi stub so tests run without installing fastapi."""

    class _HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            self.status_code = status_code
            self.detail = detail

    def _Header(default=None, alias=None):
        return default

    fastapi_mod = types.ModuleType("fastapi")
    fastapi_mod.HTTPException = _HTTPException
    fastapi_mod.Header = _Header

    sys.modules["fastapi"] = fastapi_mod
    yield fastapi_mod
    sys.modules.pop("fastapi", None)


@pytest.fixture(autouse=False)
def mock_django():
    """Inject a minimal django stub so tests run without installing django."""

    class _JsonResponse:
        def __init__(self, data, status=200):
            self.data = data
            self.status_code = status

    django_mod  = types.ModuleType("django")
    http_mod    = types.ModuleType("django.http")
    http_mod.JsonResponse = _JsonResponse
    django_mod.http = http_mod

    sys.modules["django"]      = django_mod
    sys.modules["django.http"] = http_mod
    yield
    sys.modules.pop("django", None)
    sys.modules.pop("django.http", None)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_raises_on_empty_api_key(self):
        with pytest.raises(CertiLayerError) as exc_info:
            CertiLayerClient(api_key="")
        assert exc_info.value.code == "INVALID_API_KEY"

    def test_raises_on_whitespace_api_key(self):
        with pytest.raises(CertiLayerError) as exc_info:
            CertiLayerClient(api_key="   ")
        assert exc_info.value.code == "INVALID_API_KEY"

    def test_constructs_with_valid_key(self):
        assert CertiLayerClient(api_key="cl_test_x") is not None


# ---------------------------------------------------------------------------
# verify_session
# ---------------------------------------------------------------------------

class TestVerifySession:
    @respx.mock
    async def test_returns_verify_result_on_200(self, client):
        respx.get(f"{BASE_URL}/sessions/valid-session/score").mock(
            return_value=httpx.Response(200, json=VALID_RESPONSE)
        )
        result = await client.verify_session("valid-session")
        assert isinstance(result, VerifyResult)
        assert result.score == 0.95
        assert result.verdict == HCSVerdict.HUMAN_VERIFIED
        assert result.session_active is True

    @respx.mock
    async def test_raises_session_not_found_on_404(self, client):
        respx.get(f"{BASE_URL}/sessions/bad-id/score").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("bad-id")
        assert exc_info.value.code == "SESSION_NOT_FOUND"

    @respx.mock
    async def test_raises_session_expired_on_410(self, client):
        respx.get(f"{BASE_URL}/sessions/expired/score").mock(
            return_value=httpx.Response(410, json={"message": "expired"})
        )
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("expired")
        assert exc_info.value.code == "SESSION_EXPIRED"

    @respx.mock
    async def test_raises_invalid_api_key_on_401(self, client):
        respx.get(f"{BASE_URL}/sessions/any/score").mock(
            return_value=httpx.Response(401, json={"message": "unauthorized"})
        )
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("any")
        assert exc_info.value.code == "INVALID_API_KEY"

    @respx.mock
    async def test_raises_rate_limited_on_429(self, client):
        respx.get(f"{BASE_URL}/sessions/any/score").mock(
            return_value=httpx.Response(429, json={"message": "rate limit"})
        )
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("any")
        assert exc_info.value.code == "RATE_LIMITED"

    async def test_raises_on_empty_session_id(self, client):
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("")
        assert exc_info.value.code == "SESSION_NOT_FOUND"

    async def test_raises_on_non_string_session_id(self, client):
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session(None)  # type: ignore
        assert exc_info.value.code == "SESSION_NOT_FOUND"

    @respx.mock
    async def test_raises_network_error_on_connect_error(self, client):
        respx.get(f"{BASE_URL}/sessions/any/score").mock(
            side_effect=httpx.ConnectError("ECONNREFUSED")
        )
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("any")
        assert exc_info.value.code == "NETWORK_ERROR"

    @respx.mock
    async def test_raises_timeout(self, client):
        respx.get(f"{BASE_URL}/sessions/any/score").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        with pytest.raises(CertiLayerError) as exc_info:
            await client.verify_session("any")
        assert exc_info.value.code == "TIMEOUT"


# ---------------------------------------------------------------------------
# quick_check
# ---------------------------------------------------------------------------

class TestQuickCheck:
    @respx.mock
    async def test_passed_true_when_score_above_default(self, client):
        respx.get(f"{BASE_URL}/sessions/valid/score").mock(
            return_value=httpx.Response(200, json=VALID_RESPONSE)
        )
        result = await client.quick_check("valid")
        assert isinstance(result, QuickCheckResult)
        assert result.passed is True
        assert result.score == 0.95

    @respx.mock
    async def test_passed_false_when_synthetic(self, client):
        respx.get(f"{BASE_URL}/sessions/bot/score").mock(
            return_value=httpx.Response(200, json={
                **VALID_RESPONSE, "score": 0.30, "verdict": "synthetic"
            })
        )
        result = await client.quick_check("bot")
        assert result.passed is False
        assert result.verdict == HCSVerdict.SYNTHETIC

    @respx.mock
    async def test_respects_custom_min_score(self, client):
        respx.get(f"{BASE_URL}/sessions/grey/score").mock(
            return_value=httpx.Response(200, json={
                **VALID_RESPONSE, "score": 0.70, "verdict": "human_likely"
            })
        )
        result = await client.quick_check("grey", min_score=0.90)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestRetryLogic:
    @respx.mock
    async def test_retries_on_500_and_succeeds(self):
        client = CertiLayerClient(api_key="cl_test_abc", max_retries=1)
        route = respx.get(f"{BASE_URL}/sessions/valid/score")
        route.side_effect = [
            httpx.Response(500, json={"message": "server error"}),
            httpx.Response(200, json=VALID_RESPONSE),
        ]
        result = await client.verify_session("valid")
        assert result.verdict == HCSVerdict.HUMAN_VERIFIED
        assert route.call_count == 2

    @respx.mock
    async def test_raises_after_exhausting_retries(self):
        client = CertiLayerClient(api_key="cl_test_abc", max_retries=1)
        route = respx.get(f"{BASE_URL}/sessions/any/score")
        route.mock(return_value=httpx.Response(500, json={"message": "server error"}))
        with pytest.raises(CertiLayerError):
            await client.verify_session("any")
        assert route.call_count == 2


# ---------------------------------------------------------------------------
# FastAPI dependency (uses mock_fastapi fixture)
# ---------------------------------------------------------------------------

class TestFastapiDependency:
    @respx.mock
    async def test_returns_check_result_when_passed(self, client, mock_fastapi):
        respx.get(f"{BASE_URL}/sessions/valid/score").mock(
            return_value=httpx.Response(200, json=VALID_RESPONSE)
        )
        dep = client.fastapi_dependency()
        result = await dep(session_id="valid")
        assert result.passed is True

    @respx.mock
    async def test_raises_403_when_synthetic(self, client, mock_fastapi):
        respx.get(f"{BASE_URL}/sessions/bot/score").mock(
            return_value=httpx.Response(200, json={
                **VALID_RESPONSE, "score": 0.30, "verdict": "synthetic"
            })
        )
        dep = client.fastapi_dependency()
        with pytest.raises(mock_fastapi.HTTPException) as exc_info:
            await dep(session_id="bot")
        assert exc_info.value.status_code == 403

    async def test_raises_403_when_no_session_id(self, client, mock_fastapi):
        dep = client.fastapi_dependency()
        with pytest.raises(mock_fastapi.HTTPException) as exc_info:
            await dep(session_id=None)
        assert exc_info.value.status_code == 403

    @respx.mock
    async def test_fails_open_on_sdk_error(self, client, mock_fastapi):
        respx.get(f"{BASE_URL}/sessions/any/score").mock(
            side_effect=httpx.ConnectError("network down")
        )
        dep = client.fastapi_dependency()
        result = await dep(session_id="any")
        assert result.passed is True

    def test_raises_import_error_without_fastapi(self, client):
        sys.modules.pop("fastapi", None)
        with pytest.raises(ImportError, match="fastapi"):
            client.fastapi_dependency()


# ---------------------------------------------------------------------------
# Django middleware (uses mock_django fixture)
# ---------------------------------------------------------------------------

class TestDjangoMiddleware:
    def test_returns_middleware_class(self, client):
        assert isinstance(client.django_middleware(), type)

    def test_calls_get_response_when_no_header(self, client, mock_django):
        middleware_cls = client.django_middleware()
        called = []

        def get_response(request):
            called.append(True)
            return "OK"

        middleware = middleware_cls(get_response)

        class FakeRequest:
            META = {}

        assert middleware(FakeRequest()) == "OK"
        assert called

    @respx.mock
    def test_allows_request_when_score_passes(self, client, mock_django):
        respx.get(f"{BASE_URL}/sessions/valid/score").mock(
            return_value=httpx.Response(200, json=VALID_RESPONSE)
        )
        middleware_cls = client.django_middleware()

        def get_response(request):
            return "allowed"

        middleware = middleware_cls(get_response)

        class FakeRequest:
            META = {"HTTP_X_CERTILAYER_SESSION": "valid"}

        assert middleware(FakeRequest()) == "allowed"

    @respx.mock
    def test_blocks_request_when_synthetic(self, client, mock_django):
        respx.get(f"{BASE_URL}/sessions/bot/score").mock(
            return_value=httpx.Response(200, json={
                **VALID_RESPONSE, "score": 0.30, "verdict": "synthetic"
            })
        )
        middleware_cls = client.django_middleware()
        middleware = middleware_cls(lambda r: "allowed")

        class FakeRequest:
            META = {"HTTP_X_CERTILAYER_SESSION": "bot"}

        result = middleware(FakeRequest())
        assert result.status_code == 403


# ---------------------------------------------------------------------------
# HCSVerdict
# ---------------------------------------------------------------------------

class TestHCSVerdict:
    def test_all_verdicts_parse(self):
        assert HCSVerdict("human_verified") == HCSVerdict.HUMAN_VERIFIED
        assert HCSVerdict("human_likely")   == HCSVerdict.HUMAN_LIKELY
        assert HCSVerdict("synthetic")      == HCSVerdict.SYNTHETIC

    def test_unknown_verdict_falls_back_to_synthetic(self):
        assert HCSVerdict("future_unknown") == HCSVerdict.SYNTHETIC


# ---------------------------------------------------------------------------
# CertiLayerError
# ---------------------------------------------------------------------------

class TestCertiLayerError:
    def test_code_and_message(self):
        err = CertiLayerError("test error", "TIMEOUT")
        assert err.code == "TIMEOUT"
        assert str(err) == "test error"

    def test_is_exception(self):
        assert isinstance(CertiLayerError("x", "NETWORK_ERROR"), Exception)

    def test_repr_contains_code(self):
        assert "RATE_LIMITED" in repr(CertiLayerError("msg", "RATE_LIMITED"))


# ---------------------------------------------------------------------------
# VerifyResult.from_dict
# ---------------------------------------------------------------------------

class TestVerifyResultFromDict:
    def test_parses_correctly(self):
        r = VerifyResult.from_dict(VALID_RESPONSE)
        assert r.score == 0.95
        assert r.verdict == HCSVerdict.HUMAN_VERIFIED
        assert r.session_id == "550e8400-e29b-41d4-a716-446655440000"
        assert r.session_active is True

    def test_score_cast_from_int(self):
        r = VerifyResult.from_dict({**VALID_RESPONSE, "score": 1})
        assert isinstance(r.score, float)
        assert r.score == 1.0
