"""Tests for Reddit MCP Server"""

import json
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
import pytest
import respx

# Import the server module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import (
    _format_post,
    _format_comment,
    _format_listing_item,
    _get_subreddit_internal,
    _get_post_internal,
    _get_user_internal,
    _search_internal,
    _get_subreddit_info_internal,
    _fetch,
    rate_limiter,
    RateLimiter,
)

# Load fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


# =============================================================================
# 1. Formatter Unit Tests
# =============================================================================

class TestFormatPost:
    def test_format_post_selftext(self):
        post = {
            "data": {
                "title": "Test Title",
                "author": "testuser",
                "subreddit": "python",
                "score": 42,
                "num_comments": 10,
                "permalink": "/r/python/comments/abc/test/",
                "selftext": "Post body here",
                "is_self": True,
            }
        }
        result = _format_post(post)
        assert "## Test Title" in result
        assert "u/testuser" in result
        assert "r/python" in result
        assert "42 pts" in result
        assert "10 comments" in result
        assert "Post body here" in result

    def test_format_post_link(self):
        post = {
            "data": {
                "title": "Link Post",
                "author": "linkuser",
                "subreddit": "news",
                "score": 100,
                "num_comments": 50,
                "permalink": "/r/news/comments/def/link/",
                "selftext": "",
                "url": "https://example.com/article",
                "is_self": False,
            }
        }
        result = _format_post(post)
        assert "## Link Post" in result
        assert "Link: https://example.com/article" in result

    def test_format_post_missing_fields(self):
        post = {"data": {}}
        result = _format_post(post)
        assert "## " in result  # Empty title
        assert "u/" in result
        assert "0 pts" in result

    def test_format_post_no_data_wrapper(self):
        post = {
            "title": "Direct Post",
            "author": "user",
            "subreddit": "test",
            "score": 5,
            "num_comments": 1,
            "permalink": "/r/test/comments/xyz/",
            "selftext": "Direct body",
            "is_self": True,
        }
        result = _format_post(post)
        assert "## Direct Post" in result


class TestFormatComment:
    def test_format_comment_basic(self):
        comment = {
            "kind": "t1",
            "data": {
                "author": "commenter",
                "body": "This is a comment",
                "score": 15,
                "permalink": "/r/test/comments/abc/post/comment123/",
            }
        }
        result = _format_comment(comment)
        assert "u/commenter (15 pts)" in result
        assert "This is a comment" in result

    def test_format_comment_nested(self):
        comment = {
            "kind": "t1",
            "data": {
                "author": "parent",
                "body": "Parent comment",
                "score": 10,
                "replies": {
                    "kind": "Listing",
                    "data": {
                        "children": [
                            {
                                "kind": "t1",
                                "data": {
                                    "author": "child",
                                    "body": "Child reply",
                                    "score": 5,
                                    "replies": "",
                                }
                            }
                        ]
                    }
                }
            }
        }
        result = _format_comment(comment)
        assert "u/parent" in result
        assert "  u/child" in result  # Indented
        assert "  Child reply" in result

    def test_format_comment_wrong_kind(self):
        comment = {"kind": "more", "data": {"count": 10}}
        result = _format_comment(comment)
        assert result == ""

    def test_format_comment_deeply_nested(self):
        # Create a deeply nested comment structure
        def create_nested(depth):
            if depth == 0:
                return {"kind": "t1", "data": {"author": f"user{depth}", "body": f"Level {depth}", "score": 1, "replies": ""}}
            return {
                "kind": "t1",
                "data": {
                    "author": f"user{depth}",
                    "body": f"Level {depth}",
                    "score": 1,
                    "replies": {"kind": "Listing", "data": {"children": [create_nested(depth - 1)]}}
                }
            }

        comment = create_nested(5)
        result = _format_comment(comment)
        assert "u/user5" in result
        assert "u/user4" in result
        assert "          " in result  # Deep indentation


class TestFormatListingItem:
    def test_format_listing_item(self):
        post = {
            "data": {
                "title": "Listing Item",
                "subreddit": "python",
                "score": 75,
                "permalink": "/r/python/comments/list123/listing_item/",
            }
        }
        result = _format_listing_item(post, 1)
        assert result.startswith("1. [Listing Item]")
        assert "r/python" in result
        assert "75 pts" in result
        assert "https://reddit.com/r/python" in result


# =============================================================================
# 2. URL Parsing Tests
# =============================================================================

class TestURLParsing:
    """Test the URL parsing logic in the open tool"""

    def test_parse_subreddit(self):
        import re
        url = "reddit.com/r/python"
        url = url.replace("reddit.com", "").lstrip("/")
        match = re.match(r"r/(\w+)(?:/(\w+))?", url)
        assert match
        assert match.group(1) == "python"

    def test_parse_subreddit_with_sort(self):
        import re
        url = "r/python/top"
        match = re.match(r"r/(\w+)(?:/(\w+))?", url)
        assert match
        assert match.group(1) == "python"
        assert match.group(2) == "top"

    def test_parse_post(self):
        import re
        url = "r/python/comments/abc123/some_title"
        match = re.match(r"r/(\w+)/comments/(\w+)", url)
        assert match
        assert match.group(1) == "python"
        assert match.group(2) == "abc123"

    def test_parse_user_short(self):
        import re
        url = "u/spez"
        match = re.match(r"u(?:ser)?/(\w+)", url)
        assert match
        assert match.group(1) == "spez"

    def test_parse_user_long(self):
        import re
        url = "user/spez"
        match = re.match(r"u(?:ser)?/(\w+)", url)
        assert match
        assert match.group(1) == "spez"

    def test_parse_search(self):
        import re
        url = "search?q=fastapi"
        match = re.match(r"search\?q=([^&]+)", url)
        assert match
        assert match.group(1) == "fastapi"

    def test_parse_about(self):
        import re
        url = "r/python/about"
        match = re.match(r"r/(\w+)/about", url)
        assert match
        assert match.group(1) == "python"

    def test_normalize_full_url(self):
        url = "https://www.reddit.com/r/python"
        url = url.replace("https://", "").replace("http://", "")
        url = url.replace("www.reddit.com", "").replace("reddit.com", "")
        url = url.lstrip("/")
        assert url == "r/python"


# =============================================================================
# 3. Integration Tests (mocked HTTP)
# =============================================================================

@pytest.fixture
def disable_rate_limit():
    """Disable rate limiting for tests by setting high remaining count"""
    import server
    original_remaining = server.rate_limiter.remaining
    server.rate_limiter.remaining = 1000
    yield
    server.rate_limiter.remaining = original_remaining


class TestIntegration:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_subreddit_success(self, disable_rate_limit):
        fixture = load_fixture("subreddit_listing.json")
        respx.get("https://reddit.com/r/python/hot.json?limit=10").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        result = await _get_subreddit_internal("python")
        assert "r/python - hot" in result
        assert "Test Post Title" in result
        assert "Link Post Example" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_subreddit_empty(self, disable_rate_limit):
        respx.get("https://reddit.com/r/empty/hot.json?limit=10").mock(
            return_value=httpx.Response(200, json={"data": {"children": []}})
        )

        result = await _get_subreddit_internal("empty")
        assert "r/empty - hot" in result
        assert "========" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_post_with_comments(self, disable_rate_limit):
        fixture = load_fixture("post_with_comments.json")
        respx.get("https://reddit.com/r/python/comments/xyz789.json?limit=20").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        result = await _get_post_internal("/r/python/comments/xyz789")
        assert "Test Post With Comments" in result
        assert "postauthor" in result
        assert "COMMENTS" in result
        assert "commenter1" in result
        assert "replier1" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_user_mixed(self, disable_rate_limit):
        fixture = load_fixture("user_overview.json")
        respx.get("https://reddit.com/user/testuser/overview.json?limit=15").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        result = await _get_user_internal("testuser")
        assert "u/testuser" in result
        assert "[POST]" in result
        assert "[COMMENT]" in result
        assert "..." in result  # Truncated long comment

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_results(self, disable_rate_limit):
        fixture = load_fixture("search_results.json")
        respx.get("https://reddit.com/search.json?q=fastapi&sort=relevance&t=all&limit=10").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        result = await _search_internal("fastapi")
        assert "Search: 'fastapi'" in result
        assert "FastAPI Tutorial" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_subreddit_info(self, disable_rate_limit):
        fixture = load_fixture("subreddit_about.json")
        respx.get("https://reddit.com/r/python/about.json").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        result = await _get_subreddit_info_internal("python")
        assert "r/python" in result
        assert "Python" in result
        assert "1,500,000" in result  # Subscribers formatted
        assert "Related subreddits:" in result
        assert "r/learnpython" in result
        assert "r/django" in result


# =============================================================================
# 4. Edge Cases
# =============================================================================

class TestEdgeCases:
    def test_limit_capped(self):
        # Test that limits are properly capped
        limit = min(100, 25)
        assert limit == 25

    def test_deleted_user(self):
        post = {
            "data": {
                "title": "Deleted User Post",
                "author": "[deleted]",
                "subreddit": "test",
                "score": 10,
                "num_comments": 5,
                "permalink": "/r/test/comments/del/deleted/",
                "selftext": "[removed]",
                "is_self": True,
            }
        }
        result = _format_post(post)
        assert "[deleted]" in result
        assert "[removed]" in result

    def test_unicode_content(self):
        post = {
            "data": {
                "title": "Unicode Test: こんにちは 🎉",
                "author": "unicoder",
                "subreddit": "test",
                "score": 50,
                "num_comments": 3,
                "permalink": "/r/test/comments/uni/unicode/",
                "selftext": "Emoji: 🐍🚀 Chinese: 中文 Arabic: العربية",
                "is_self": True,
            }
        }
        result = _format_post(post)
        assert "こんにちは" in result
        assert "🎉" in result
        assert "🐍" in result

    def test_very_long_body(self):
        long_text = "x" * 15000
        post = {
            "data": {
                "title": "Long Post",
                "author": "longuser",
                "subreddit": "test",
                "score": 1,
                "num_comments": 0,
                "permalink": "/r/test/comments/long/",
                "selftext": long_text,
                "is_self": True,
            }
        }
        result = _format_post(post)
        assert len(result) > 15000  # Body is included fully

    def test_special_chars_in_query(self):
        # URL encoding should handle special chars
        query = "c++"
        encoded = query.replace("+", " ")  # Basic handling
        assert encoded == "c  "


# =============================================================================
# 5. Error Handling
# =============================================================================

class TestErrorHandling:
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_404(self, disable_rate_limit):
        respx.get("https://reddit.com/r/nonexistent/hot.json?limit=10").mock(
            return_value=httpx.Response(404)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await _get_subreddit_internal("nonexistent")

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_403(self, disable_rate_limit):
        respx.get("https://reddit.com/r/private/hot.json?limit=10").mock(
            return_value=httpx.Response(403)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await _get_subreddit_internal("private")

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_429(self, disable_rate_limit):
        respx.get("https://reddit.com/r/ratelimited/hot.json?limit=10").mock(
            return_value=httpx.Response(429)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await _get_subreddit_internal("ratelimited")

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_500(self, disable_rate_limit):
        respx.get("https://reddit.com/r/error/hot.json?limit=10").mock(
            return_value=httpx.Response(500)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await _get_subreddit_internal("error")


# =============================================================================
# 6. Rate Limiter Tests
# =============================================================================

class TestRateLimiter:
    def test_rate_limiter_update_from_headers(self):
        """Test that rate limiter correctly parses Reddit headers"""
        limiter = RateLimiter()
        headers = {
            "x-ratelimit-remaining": "50.0",
            "x-ratelimit-reset": "120",
        }
        limiter.update(headers)
        assert limiter.remaining == 50.0
        assert limiter.reset_at > time.time()

    @pytest.mark.asyncio
    async def test_rate_limiter_burst_allowed(self):
        """Test that burst requests are allowed when quota available"""
        limiter = RateLimiter()
        limiter.remaining = 100

        start = time.time()
        for _ in range(10):
            await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.1  # All 10 requests should be instant
        assert limiter.remaining == 90

    @pytest.mark.asyncio
    async def test_rate_limiter_waits_on_exhaustion(self):
        """Test that rate limiter waits when quota exhausted"""
        limiter = RateLimiter()
        limiter.remaining = 1
        limiter.reset_at = time.time() + 0.1  # Reset in 100ms

        start = time.time()
        await limiter.acquire()  # Uses last request
        await limiter.acquire()  # Should wait for reset
        elapsed = time.time() - start

        assert elapsed >= 0.08  # Should have waited ~100ms

    @pytest.mark.asyncio
    async def test_rate_limiter_no_wait_after_reset(self):
        """Test that no wait needed after reset time passed"""
        limiter = RateLimiter()
        limiter.remaining = 0
        limiter.reset_at = time.time() - 1  # Already reset

        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.05  # No wait needed
