# learning-tracker

A self-hostable app that has three checked links ready each morning for topics you choose: one longer read and two short ones, 15–30 minutes in all, at the level you pick (new, some or deep).

Each item shows:
- the original link (no AI summaries);
- one line on why it is relevant to you;
- a short gist of the Hacker News discussion, when there is one.

You can save items, edit your interests, and invite people with codes.

**Status:** under construction. The first build sprint is laying down the invite-gated shell, the safe page fetcher and the spend caps. Reading lists arrive in the next sprint, so it is not ready to self-host yet.

## Self-hosting (planned)

- One command with Docker Compose.
- Configuration through environment variables only (see `.env.example`).
- You bring your own AI API key.
- The app has spend caps per instance, per user and per day. Set a monthly limit in your AI provider's console as well.

Full steps will land here with the Compose files.

## Development

Needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## Licence

[AGPL-3.0](LICENSE). If you run a modified version for other people over a network, you must offer them its source.
