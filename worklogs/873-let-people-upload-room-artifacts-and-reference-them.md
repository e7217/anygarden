# feat(rooms): let people upload room artifacts and reference them (#873)

- Commit: `cc7240c` (cc7240cc7d09a6e972b4c308fce27998bea5e921)
- Author: Changyong Um
- Date: 2026-10-16T17:56:42+09:00
- PR: #873

## Situation

Room artifacts already existed as a mechanism for agents to attach
binary files (images, documents) to their replies (#854). The upload
endpoint and the reference-validation logic were locked to agents only:
a 403 was returned for non-agents, and `canonicalize_message_references`
silently dropped any `artifact` reference from a person. People had no
way to attach images to their own messages.

## Task

- Allow any room participant with `MESSAGE_SEND` capability (agents and
  people) to upload to the room's artifact store.
- Allow people's messages to reference any artifact in the same room,
  not just one they produced themselves.
- Keep agents' own-artifacts-only rule intact.
- Update both the HTTP message path and the WebSocket message path.
- Add or extend tests covering the new person-upload and person-reference
  scenarios.

## Action

- **`rooms/router.py` (upload endpoint)**: Removed the hard
  `identity.kind != "agent"` 403 guard; replaced it with the existing
  `_require_room_participant(..., Capability.MESSAGE_SEND)` check. When
  the uploader is a person, `agent_id` is passed as `None` to the
  service layer.
- **`rooms/artifacts.py` (`store_uploaded_artifact` /
  `_insert_artifact`)**: Widened `agent_id` type from `str` to
  `str | None` so person uploads produce rows with
  `produced_by_agent_id = NULL`. Doc-string updated to reflect both
  caller kinds.
- **`messages/references.py`
  (`canonicalize_message_references`)**: Added `allow_room_artifacts:
  bool = False` parameter. When set, the SQLAlchemy query for
  `RoomArtifact` rows omits the `produced_by_agent_id` filter, allowing
  the person to reference any artifact in the room. Agents still have the
  producing-agent filter applied.
- **`messages/router.py`**: HTTP `_write_message` passes
  `allow_room_artifacts=identity.kind == "user"`.
- **`ws/handler.py`**: WebSocket message path mirrors the same flag:
  `allow_room_artifacts=(identity is not None and identity.kind == "user")`.
- **`tests/test_rooms_artifacts_endpoints.py`**: Added tests for
  person upload success (201/200 dedup) and verified the old 403 is
  gone.
- **`tests/test_message_artifact_references.py`**: Added tests for
  person referencing a room artifact (own upload, other agent's upload)
  and the rejection path when `allow_room_artifacts=False`.
- **`tests/test_ws_handler.py`**: Added WebSocket-path coverage for
  person messages that carry an artifact reference.

## Decisions

- **Own-uploads-only rejected for people** — the upload store dedupes on
  `(room_id, sha256)`. If an agent uploaded a file first, the person's
  upload returns the agent's existing row (`produced_by_agent_id` = agent).
  Filtering by `produced_by_agent_id IS NULL` would silently reject that
  reference even though the bytes are identical and the person is the one
  who pasted them. Allowing same-room references for people is therefore
  the only approach that is both correct and unsurprising to the user.
- **Capability gate kept, agent-kind gate removed** — using the existing
  `Capability.MESSAGE_SEND` check rather than a new capability keeps the
  permission model shallow. A guest who cannot send messages also cannot
  upload, without a special case.
- **`allow_room_artifacts` flag over caller-identity introspection inside
  `references.py`** — the reference-canonicalization function is a pure
  data-validation layer shared by HTTP and WS paths. Passing a flag keeps
  it from importing identity types and keeps the call-site intent explicit.
- **Assumption to revisit**: if a future role (e.g. "observer") gains
  `MESSAGE_SEND` but should not attach files, the upload endpoint's single
  capability check would need a finer gate.

## Result

People can now paste or attach images to their messages via the room
artifact upload endpoint and reference those artifacts in their message
metadata. Agents are unaffected: they still may only reference artifacts
they produced themselves. Dedup semantics are preserved — a second upload
of the same bytes returns 200 with the existing row. Eight files changed
(+188 / -26 lines); 188 net additions are predominantly new test cases.
