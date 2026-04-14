# CHANGELOG


## v0.1.0 (2026-04-14)

### Chores

- Switch license to Apache-2.0 and update author
  ([`613986b`](https://github.com/e7217/doorae-agent/commit/613986b4cf30644928817a1b40f3578938e07888))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Continuous Integration

- Add python-semantic-release for automatic versioning
  ([`f51f2dd`](https://github.com/e7217/doorae-agent/commit/f51f2dd96f7c3b1c286a7449d8cf82a3efdfbaae))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Features

- Initial release — doorae-agent v0.1.0
  ([`3e6e66a`](https://github.com/e7217/doorae-agent/commit/3e6e66a9e5d34231fdf9a6d2e3d8e79c2649795a))

Extracted from e7217/doorae monorepo (formerly doorae-sdk). Renamed package doorae_sdk →
  doorae_agent for clarity.

Includes: - ChatClient (WebSocket + REST) - 6 engine adapters (OpenAI, Anthropic, Claude Code,
  Codex, Gemini CLI, Deep Agents) - CLI entry points (doorae-agent, doorae-client) - Agent profile
  system - Protocol frames & versioning

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
