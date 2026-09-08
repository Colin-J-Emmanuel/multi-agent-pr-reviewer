import httpx
import pytest

from app.github_client import GitHubError
from app.worker import classify_failure


@pytest.mark.parametrize("exc", [
    httpx.TimeoutException("slow"),
    httpx.ConnectError("refused"),
])
def test_network_errors_are_transient(exc):
    assert classify_failure(exc) == "transient"


def test_missing_pr_is_permanent():
    err = GitHubError("404 Not Found: /repos/x/y/pulls/9999 — check repo name")
    assert classify_failure(err) == "permanent"


def test_bad_token_is_permanent():
    assert classify_failure(GitHubError("401 Unauthorized — token invalid")) == "permanent"


def test_rate_limit_is_transient():
    assert classify_failure(GitHubError("GitHub rate limit exceeded.")) == "transient"


@pytest.mark.parametrize("exc", [
    ValueError("too many values to unpack"),
    TypeError("bad argument"),
    KeyError("missing"),
])
def test_our_own_bugs_are_permanent(exc):
    """Unrecognized exceptions must never retry — they fail identically every time."""
    assert classify_failure(exc) == "permanent"
