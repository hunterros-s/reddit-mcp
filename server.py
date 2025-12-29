"""Reddit MCP Server - AI agent access to Reddit via .json endpoints"""

import asyncio
import re
import time
from fastmcp import FastMCP
import httpx

mcp = FastMCP("Reddit")

HEADERS = {
    "User-Agent": "reddit-mcp/0.1 (AI agent Reddit client)"
}


class RateLimiter:
    """Dynamic rate limiter using Reddit's rate limit headers"""

    def __init__(self):
        self.remaining = 100  # Assume full quota at start
        self.reset_at = 0.0   # Unix timestamp when window resets

    def update(self, headers: dict):
        """Update state from Reddit response headers"""
        if "x-ratelimit-remaining" in headers:
            self.remaining = float(headers["x-ratelimit-remaining"])
        if "x-ratelimit-reset" in headers:
            self.reset_at = time.time() + float(headers["x-ratelimit-reset"])

    async def acquire(self):
        """Wait if we're out of requests, otherwise proceed immediately"""
        if self.remaining <= 1:
            wait = max(0, self.reset_at - time.time())
            if wait > 0:
                await asyncio.sleep(wait)
            self.remaining = 100  # Assume reset after wait
        self.remaining -= 1


rate_limiter = RateLimiter()


async def _fetch(url: str) -> dict:
    """Fetch Reddit JSON with dynamic rate limiting"""
    await rate_limiter.acquire()
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(url)
        rate_limiter.update(dict(r.headers))
        r.raise_for_status()
        return r.json()


def _format_post(post: dict, include_body: bool = True) -> str:
    """Format a post as plain text"""
    d = post.get("data", post)
    title = d.get("title", "")
    author = d.get("author", "")
    subreddit = d.get("subreddit", "")
    score = d.get("score", 0)
    num_comments = d.get("num_comments", 0)
    permalink = f"https://reddit.com{d.get('permalink')}" if d.get("permalink") else ""
    selftext = d.get("selftext", "")
    url = d.get("url", "")
    is_self = d.get("is_self", False)

    lines = [
        f"## {title}",
        f"by u/{author} in r/{subreddit} | {score} pts | {num_comments} comments",
        permalink,
    ]

    if include_body:
        if selftext:
            lines.append("")
            lines.append(selftext)
        elif not is_self and url:
            lines.append(f"Link: {url}")

    return "\n".join(lines)


def _format_comment(comment: dict, depth: int = 0) -> str:
    """Format a comment with indentation for nesting"""
    if comment.get("kind") != "t1":
        return ""
    d = comment.get("data", comment)

    author = d.get("author", "")
    body = d.get("body", "")
    score = d.get("score", 0)
    indent = "  " * depth

    lines = [f"{indent}u/{author} ({score} pts)", f"{indent}{body}"]

    # Handle replies
    replies = d.get("replies")
    if replies and isinstance(replies, dict):
        children = replies.get("data", {}).get("children", [])
        for child in children[:5]:
            child_text = _format_comment(child, depth + 1)
            if child_text:
                lines.append("")
                lines.append(child_text)

    return "\n".join(lines)


def _format_listing_item(post: dict, index: int) -> str:
    """Format a post as a single-line listing item"""
    d = post.get("data", post)
    title = d.get("title", "")
    subreddit = d.get("subreddit", "")
    score = d.get("score", 0)
    permalink = f"https://reddit.com{d.get('permalink')}" if d.get("permalink") else ""

    return f"{index}. [{title}] - r/{subreddit} ({score} pts)\n   {permalink}"


@mcp.tool
async def open(url: str) -> str:
    """
    Open any Reddit URL and return formatted content.

    Handles:
    - Subreddit: reddit.com/r/python
    - Post: reddit.com/r/python/comments/abc123/...
    - User: reddit.com/u/username or reddit.com/user/username
    - Search: reddit.com/search?q=...

    Args:
        url: Any Reddit URL
    """
    # Normalize
    url = url.replace("https://", "").replace("http://", "")
    url = url.replace("www.reddit.com", "").replace("reddit.com", "")
    url = url.lstrip("/")

    # Subreddit info: r/subreddit/about
    if match := re.match(r"r/(\w+)/about", url):
        return await _get_subreddit_info_internal(match.group(1))

    # Post: r/subreddit/comments/id/...
    if match := re.match(r"r/(\w+)/comments/(\w+)", url):
        return await _get_post_internal(f"/r/{match.group(1)}/comments/{match.group(2)}")

    # User: u/username or user/username
    if match := re.match(r"u(?:ser)?/(\w+)", url):
        return await _get_user_internal(match.group(1))

    # Search: search?q=...
    if match := re.match(r"search\?q=([^&]+)", url):
        query = match.group(1).replace("+", " ").replace("%20", " ")
        return await _search_internal(query)

    # Subreddit: r/name or r/name/hot etc
    if match := re.match(r"r/(\w+)(?:/(\w+))?", url):
        name = match.group(1)
        sort = match.group(2) if match.group(2) in ("hot", "new", "top", "rising") else "hot"
        return await _get_subreddit_internal(name, sort)

    return f"Could not parse URL: {url}"


async def _get_subreddit_info_internal(name: str) -> str:
    about = await _fetch(f"https://reddit.com/r/{name}/about.json")
    d = about.get("data", {})

    lines = [f"r/{name}"]
    lines.append("=" * 40)

    if title := d.get("title"):
        lines.append(title)

    if desc := d.get("public_description"):
        lines.append("")
        lines.append(desc)

    lines.append("")
    lines.append(f"Subscribers: {d.get('subscribers', 0):,}")
    if created := d.get("created_utc"):
        from datetime import datetime, timezone
        created_date = datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d")
        lines.append(f"Created: {created_date}")

    # Extract related subreddits from sidebar/description
    description = d.get("description", "")
    related = list(set(re.findall(r'/r/(\w+)', description)))
    related = [r for r in related if r.lower() != name.lower()][:10]
    if related:
        lines.append("")
        lines.append("Related subreddits:")
        for r in related:
            lines.append(f"  r/{r}")

    return "\n".join(lines)


async def _get_subreddit_internal(name: str, sort: str = "hot", limit: int = 10, time_filter: str = "day") -> str:
    limit = min(limit, 25)
    url = f"https://reddit.com/r/{name}/{sort}.json?limit={limit}"
    if sort == "top":
        url += f"&t={time_filter}"

    data = await _fetch(url)
    posts = data.get("data", {}).get("children", [])

    lines = [f"r/{name} - {sort}"]
    lines.append("=" * 40)
    for i, post in enumerate(posts, 1):
        lines.append(_format_listing_item(post, i))

    return "\n".join(lines)


async def _get_post_internal(url: str, comment_limit: int = 20) -> str:
    comment_limit = min(comment_limit, 50)
    if not url.endswith(".json"):
        url = url.rstrip("/") + ".json"

    full_url = f"https://reddit.com{url}?limit={comment_limit}"
    data = await _fetch(full_url)

    post_data = data[0]["data"]["children"][0] if data else {}
    comments_data = data[1]["data"]["children"] if len(data) > 1 else []

    lines = [_format_post(post_data)]
    lines.append("")
    lines.append("=" * 40)
    lines.append("COMMENTS")
    lines.append("=" * 40)

    for comment in comments_data:
        comment_text = _format_comment(comment)
        if comment_text:
            lines.append("")
            lines.append(comment_text)

    return "\n".join(lines)


async def _get_user_internal(username: str, content_type: str = "overview", limit: int = 15) -> str:
    limit = min(limit, 25)
    url = f"https://reddit.com/user/{username}/{content_type}.json?limit={limit}"

    data = await _fetch(url)
    items = data.get("data", {}).get("children", [])

    lines = [f"u/{username} - {content_type}"]
    lines.append("=" * 40)

    for i, item in enumerate(items, 1):
        kind = item.get("kind")
        d = item.get("data", {})
        permalink = f"https://reddit.com{d.get('permalink')}" if d.get("permalink") else ""

        if kind == "t3":
            title = d.get("title", "")
            subreddit = d.get("subreddit", "")
            score = d.get("score", 0)
            lines.append(f"{i}. [POST] {title}")
            lines.append(f"   r/{subreddit} | {score} pts")
            lines.append(f"   {permalink}")
        elif kind == "t1":
            body = d.get("body", "")[:200]
            if len(d.get("body", "")) > 200:
                body += "..."
            subreddit = d.get("subreddit", "")
            score = d.get("score", 0)
            lines.append(f"{i}. [COMMENT] in r/{subreddit} | {score} pts")
            lines.append(f"   {body}")
            lines.append(f"   {permalink}")

    return "\n".join(lines)


async def _search_internal(query: str, subreddit: str | None = None, sort: str = "relevance", time_filter: str = "all", limit: int = 10) -> str:
    limit = min(limit, 25)

    if subreddit:
        url = f"https://reddit.com/r/{subreddit}/search.json?q={query}&restrict_sr=on&sort={sort}&t={time_filter}&limit={limit}"
    else:
        url = f"https://reddit.com/search.json?q={query}&sort={sort}&t={time_filter}&limit={limit}"

    data = await _fetch(url)
    posts = data.get("data", {}).get("children", [])

    scope = f"r/{subreddit}" if subreddit else "all of Reddit"
    lines = [f"Search: '{query}' in {scope}"]
    lines.append("=" * 40)
    for i, post in enumerate(posts, 1):
        lines.append(_format_listing_item(post, i))

    return "\n".join(lines)


@mcp.tool
def rate_limit_status() -> str:
    """Get current Reddit API rate limit status"""
    remaining = rate_limiter.remaining
    reset_in = max(0, rate_limiter.reset_at - time.time())
    return f"Remaining: {remaining:.0f} requests\nResets in: {reset_in:.0f} seconds"


@mcp.tool
async def get_subreddit_info(name: str) -> str:
    """
    Get subreddit metadata including description, subscriber count, and related subreddits.

    Args:
        name: Subreddit name (without r/)
    """
    return await _get_subreddit_info_internal(name)


@mcp.tool
async def get_subreddit(name: str, sort: str = "hot", limit: int = 10, time_filter: str = "day") -> str:
    """
    Get posts from a subreddit.

    Args:
        name: Subreddit name (without r/)
        sort: Sort order - hot, new, top, rising
        limit: Number of posts (max 25)
        time_filter: For 'top' sort - hour, day, week, month, year, all
    """
    return await _get_subreddit_internal(name, sort, limit, time_filter)


@mcp.tool
async def get_post(url: str, comment_limit: int = 20) -> str:
    """
    Get a post and its comments.

    Args:
        url: Reddit post URL or permalink (e.g., /r/python/comments/abc123/title)
        comment_limit: Number of top-level comments (max 50)
    """
    if url.startswith("http"):
        url = url.replace("https://reddit.com", "").replace("https://www.reddit.com", "")
    return await _get_post_internal(url, comment_limit)


@mcp.tool
async def get_user(username: str, content_type: str = "overview", limit: int = 15) -> str:
    """
    Get a user's recent activity.

    Args:
        username: Reddit username (without u/)
        content_type: What to fetch - overview, submitted, comments
        limit: Number of items (max 25)
    """
    return await _get_user_internal(username, content_type, limit)


@mcp.tool
async def search(query: str, subreddit: str | None = None, sort: str = "relevance", time_filter: str = "all", limit: int = 10) -> str:
    """
    Search Reddit for posts.

    Args:
        query: Search query
        subreddit: Limit to specific subreddit (optional)
        sort: Sort order - relevance, hot, top, new, comments
        time_filter: Time filter - hour, day, week, month, year, all
        limit: Number of results (max 25)
    """
    return await _search_internal(query, subreddit, sort, time_filter, limit)


def main():
    """Run the MCP server"""
    mcp.run()


if __name__ == "__main__":
    main()
