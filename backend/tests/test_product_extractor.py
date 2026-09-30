import pytest

from app.services.product_extractor import UnsafeUrlError, assert_safe_url


def test_rejects_non_http_scheme():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("file:///etc/passwd")


def test_rejects_localhost():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://localhost:8000/admin")


def test_rejects_loopback_ip():
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://127.0.0.1/internal")


def test_allows_public_url():
    # Should not raise for a normal public domain.
    assert_safe_url("https://example.com/product/123")
