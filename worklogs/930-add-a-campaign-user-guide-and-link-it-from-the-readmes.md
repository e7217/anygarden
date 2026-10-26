# docs(campaigns): add a campaign user guide and link it from the READMEs (#930)

- Commit: `77b79c2` (77b79c2205fdb924e41cf7fb5364dae725c96335)
- Author: Changyong Um
- Date: 2026-10-26T00:00:00+09:00
- PR: #930

## Situation

The campaigns feature had been fully implemented across backend, frontend, and tests, but no user-facing documentation existed. Users and operators had no reference to understand how to start a campaign, what modes are available, how the evaluation command must behave, or what the various campaign states mean. The README "Learn more" sections in both English and Korean also lacked a link to this new major feature.

## Task

- Write a Korean-language user guide for the campaigns feature at `docs/campaigns.md`.
- Cover: starting a campaign, the two modes (`target` / `optimize`), evaluation command rules, campaign states and transitions, retrospective and learning mechanics, the agent-facing tools, and security/limitation notes.
- Add a "Campaigns" link to both `README.md` (English) and `README.ko.md` (Korean) "Learn more" sections.

## Action

- `docs/campaigns.md` — new file (92 lines). Structured as: intro paragraph, 시작하기 (getting started, 4-step walkthrough), 모드와 필요한 입력 (modes table), 평가 명령 규칙 (evaluation command rules with JSON output spec, pin files, machine isolation), 상태와 할 일 (state machine table with transitions), 회고와 학습 (retrospective + learning mechanics, AGENTS.md constraints), 에이전트가 쓰는 도구 (agent MCP tool catalogue), 한계와 보안 (limits and security callouts referencing #931, #933).
- `README.md` line 59 — added `- [Campaigns: improve a metric in a loop](docs/campaigns.md) (Korean)` to the "Learn more" list.
- `README.ko.md` line 57 — added `- [캠페인: 지표를 정해 두고 계속 개선하기](docs/campaigns.md)` to the "더 알아보기" list.

## Decisions

- **Korean-only guide at this stage**: the campaigns feature documentation is written entirely in Korean, matching the project's primary audience and the convention used by other Korean-language docs in the repo. The English README links to it with a `(Korean)` annotation to set expectations for English-only readers. An English translation is deferred.
- **Single flat doc vs. multi-page**: all campaigns content was placed in one `docs/campaigns.md` file rather than a subdirectory, consistent with the existing `docs/runbook/` style for self-contained guides. The feature is complex enough to deserve its own file but not yet large enough to split.
- **Agent tools section included**: rather than leaving agent tool documentation to API docs alone, the guide includes the MCP tool catalogue (`record_campaign_attempt`, `schedule_campaign_wakeup`, `report_campaign_blocked`, `read_campaign_attempts`, `apply_campaign_learning`) so operators configuring agents can cross-check expected behavior without reading backend code.
- **Explicit callout of trusted permission requirement for Codex**: the guide surfaces the `trusted` permission requirement for Codex agents in the getting-started section — an operationally critical detail that could otherwise cause silent MCP failures.
- **Security scope explicitly bounded**: limitations (no sandbox isolation, no cross-node trust enforcement) are named and cross-referenced to follow-up issues (#931, #933) rather than omitted or overpromised.

## Result

- `docs/campaigns.md` now provides a complete Korean-language reference for the campaigns feature, covering startup, modes, evaluation commands, state machine, retrospectives, agent tools, and known limits.
- Both `README.md` and `README.ko.md` surface a direct link to the guide in their "Learn more" sections.
- No behavior was changed; this commit is documentation only.
