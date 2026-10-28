# feat(peers): let ask_peer questions carry room files (#969)

- Author: Claude (assisted, user-directed)
- Date: 2026-10-28
- Issue: #969 (item 2)

## Situation

In the reviewed goal run, the lead asked two peers to review
`hai-30-ideas-draft-v1.md`, a room artifact uploaded 45 seconds before
the question. Both peers answered that they had no file and reviewed the
summary only. The draft was 31,118 characters; the question carried 1,270
(4%). `ask_peer` had no way to pass a file, a question is capped at 2,000
characters, and a peer's turn downloads only the files its triggering
message references (`materialize_inbox_attachments`).

## Task

Let the caller hand a peer whole files through `ask_peer`, using the
existing inbox path on the receiving side.

## Action

- `ask_peer` asks take `attachments`: up to 5 file names in the room.
  The schema says a file written in the current turn is not in the room
  until the turn ends.
- `orchestration/peer_ask.resolve_attachments` maps names to
  `RoomArtifact` rows. On a reused name the caller's newest file wins,
  then anyone's newest, so a person's upload can be passed on (every
  participant already sees the room's files).
- Every name is resolved before anything is posted. An unknown name is a
  tool error that sends nothing and lists the caller's newest file names
  in the room plus the "reaches the room when your turn ends" note.
- `post_question` puts canonical `artifact` references on the question
  message. The durable turn frame keeps the message metadata, so the
  peer's Codex/Pi turn downloads them into its inbox unchanged.
- `artifact_reference` moved from `campaigns/report.py` to
  `messages/references.py`, which already built the same dict inline.
- Roster peer-call rule: pass a room file as an attachment instead of
  summarizing it into the question.
- New requirement COL-10.

## Result

- 4 new tests in `tests/test_mcp_ask_peer.py` (`TestAskPeerAttachments`):
  files ride on the question, the caller's newest file wins on a reused
  name, an unknown name sends nothing, more than 5 files is rejected.
- Related suites (ask_peer, peer ask, references, campaign report, Pi
  bridge, agent, ws handler): 952 passed.

Not covered: a file written in the same turn as the `ask_peer` call still
cannot be attached, because outbox files upload when the reply is sent.
The tool error tells the model so.
