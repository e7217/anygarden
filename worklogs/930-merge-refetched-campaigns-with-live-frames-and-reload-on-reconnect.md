# fix(campaigns): merge refetched campaigns with live frames and reload on reconnect (#930)

- Commit: `916dfd8` (916dfd8a369068a53468bc32020c9106e0101eca)
- Author: Changyong Um
- Date: 2026-10-26T14:44:39+09:00
- PR: #930

## Situation

The room's campaign rail fetched campaigns and their recent attempts from REST on mount and replaced local state wholesale with each fresh response. This meant that any WebSocket frames (`campaign.updated`, `campaign.attempt_updated`) that arrived while the list request was in-flight were silently discarded — the REST snapshot overwrote them. Additionally, when the WebSocket reconnected after a drop, frames sent during the outage were already lost and the UI had no mechanism to recover them.

## Task

- Ensure live WebSocket frames that arrive while a REST refetch is in-flight are not lost; merge them into the response instead.
- Ensure attempt frames for campaigns not yet in the local list are buffered and applied once the campaign shows up.
- Trigger a full refetch of campaigns and attempts whenever the room's WebSocket reconnects (so missed frames are recovered from the server).
- Dispatch a custom `anygarden:ws:reconnected` event from `useWebSocket` **only** on reconnects (not the initial open), carrying `{ room_id }`.

## Action

**`packages/cluster/frontend/src/hooks/useRoomCampaigns.ts`**

- Added `mergeCampaigns(list, held)`: reduces held campaigns (live frames) into a fresh REST list using `upsertCampaign`, so the higher `state_revision` wins and campaigns the REST snapshot doesn't know yet are kept at the top.
- Added `mergeAttempts(fetched, held)`: unions fetched and held attempt maps per campaign, deduplicating by attempt id; when both copies exist, the one that progressed further (scored by `attemptProgress` — trusted score > outcome > closed_at > eval_status) wins; result is sorted by `cycle_no` descending and capped at `RECENT_ATTEMPTS`.
- Changed `setState` in the success path of `refresh` to a functional updater; when `prev.roomId === roomId`, it calls `mergeCampaigns` / `mergeAttempts` rather than replacing outright.
- Changed the error path of `refresh` similarly: on failure for the same room, keeps previous campaigns/attempts visible rather than blanking them.
- Changed the `campaign.attempt_updated` handler to buffer frames for campaigns not yet in the list (removed the guard that dropped them).
- Added an `onReconnect` listener for `anygarden:ws:reconnected`; calls `refresh()` only when `event.detail.room_id === roomId`.
- Added `refresh` to the `useEffect` dependency array for the event listeners (was omitted, causing a stale-closure lint gap).

**`packages/cluster/frontend/src/hooks/useWebSocket.ts`**

- Added `openedRef` (boolean ref, initialised `false`) to track whether any socket has successfully opened in this visit.
- In `ws.onopen`: if `openedRef.current` is already `true` (i.e. this is a reconnect), dispatches `new CustomEvent('anygarden:ws:reconnected', { detail: { room_id: roomId } })`; then sets `openedRef.current = true`.
- In the room-change cleanup path (`seqRef.current = 0` block): resets `openedRef.current = false` so a room change resets the sentinel.

**`packages/cluster/frontend/src/hooks/useRoomCampaigns.test.ts`**

- Updated the "frames for unknown campaign are ignored" test: now asserts those frames are *buffered* (kept in `attempts.nope`) while still absent from the `campaigns` list.
- Added "keeps a newer frame that arrived while the list request was in flight": uses a gate-promise to hold the REST response, dispatches `campaign.updated` and `campaign.attempt_updated` before releasing, then asserts the merged state is correct.
- Added "reloads after the room socket reconnects": dispatches `anygarden:ws:reconnected` for a different room (no effect), then for the correct room, and asserts a fresh REST response is reflected.

**`packages/cluster/frontend/src/hooks/useWebSocket.test.tsx`**

- Added "announces a reconnect, not the first open": verifies the `anygarden:ws:reconnected` event is **not** fired on the initial `onopen`, and **is** fired on a subsequent `onopen` after a close/reconnect cycle.

## Decisions

- **Merge vs. discard on REST response**: discarding live frames on each refetch keeps the code simple but causes visible flicker and data loss during any reconnect-triggered reload. Merging (higher revision wins) is safe because `state_revision` is monotonic and attempts progress in one direction; the cost is two extra utility functions.
- **Buffering frames for unlisted campaigns**: the previous guard (drop frames whose campaign id isn't in the current list) was simple and conservative but wrong: the list response might arrive *after* a frame for a brand-new campaign. Buffering all frames and filtering at render time is the correct model; memory cost is negligible.
- **Reconnect signal via custom event rather than a shared ref or context**: a custom DOM event keeps `useWebSocket` decoupled from `useRoomCampaigns`; any other REST-backed hook can listen to the same event without wiring. The alternative (a shared Zustand slice or a React context signal) would have required touching more files and creating a new dependency boundary.
- **`openedRef` reset on room change**: without this reset, navigating away and back would treat the first open for the new room as a reconnect and fire a spurious refetch. Assumption: a room change is always accompanied by a new `useWebSocket` call with the new `roomId` and a cleanup cycle. If `useWebSocket` is ever reused across room changes without remounting, this assumption breaks.

## Result

- Live WebSocket frames that race a REST refetch are preserved: the merged state reflects both the server snapshot and any concurrent updates.
- Attempt frames for campaigns not yet loaded are buffered and appear once the campaign is fetched.
- A WebSocket reconnect triggers a full reload of the room's campaigns and attempts, recovering any frames missed during the outage.
- Four new tests cover these paths; existing tests continue to pass.
- No behaviour change when the socket is connected and stable (no extra re-renders, no changed API surface).
