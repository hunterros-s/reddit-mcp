"""Pytest configuration"""

import pytest


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset rate limiter state between tests"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    import server
    original_remaining = server.rate_limiter.remaining
    original_reset_at = server.rate_limiter.reset_at

    yield

    server.rate_limiter.remaining = original_remaining
    server.rate_limiter.reset_at = original_reset_at
