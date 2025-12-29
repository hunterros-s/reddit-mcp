# reddit-mcp

Reddit MCP server for AI agents. No PRAW - uses `.json` URL suffix.

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install fastmcp httpx
```

## Run

```bash
fastmcp dev server.py      # Dev mode with inspector
fastmcp run server.py      # Production
```

## Tools

| Tool | Description |
|------|-------------|
| `open` | Any Reddit URL - auto-routes |
| `get_subreddit` | Posts from subreddit |
| `get_subreddit_info` | Metadata + related subs |
| `get_post` | Post + comments |
| `get_user` | User activity |
| `search` | Search Reddit |
| `rate_limit_status` | Current API quota |

## Output

Plain text, not JSON. ~97% smaller than raw Reddit API responses.

```
r/python - hot
========================================
1. [Why Python Is Removing The GIL] - r/Python (85 pts)
   https://reddit.com/r/Python/comments/...
```

## Rate Limiting

Dynamic - reads `x-ratelimit-remaining` from Reddit headers. Burst requests allowed until quota exhausted.

## Test

```bash
uv pip install pytest pytest-asyncio respx
pytest tests/ -v
```
