"""#902 — `anygarden start` tells a new user where to open the UI."""

import pytest

from anygarden.cli import _browser_url


@pytest.mark.parametrize(
    ("host", "port", "url"),
    [
        ("127.0.0.1", 8000, "http://localhost:8000"),
        ("0.0.0.0", 8000, "http://localhost:8000"),
        ("::", 8100, "http://localhost:8100"),
        ("localhost", 8000, "http://localhost:8000"),
        ("192.168.1.20", 9000, "http://192.168.1.20:9000"),
        ("node.example", 8000, "http://node.example:8000"),
        ("fd00::5", 8000, "http://[fd00::5]:8000"),
    ],
)
def test_browser_url(host, port, url):
    assert _browser_url(host, port) == url
