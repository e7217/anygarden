# test(e2e): measure the zoomed image only after its zoom glide settles (#882)

- Commit: `591990f` (591990f7381b34ed0946c41199b58dde1628df8b)
- Author: Changyong Um
- Date: 2026-10-19T11:22:43+09:00
- PR: #882

## Situation

The `image-viewer` e2e drag test was flaky: after pressing `+` to zoom in, the test immediately read the image's bounding box to establish a baseline position. However, the `+` key triggers a 150 ms CSS glide transition on the image element. A drag performed right after the key press cancelled the running animation and jumped the image to the transition's end position, causing the measured pan offset to be anywhere from −7 px to +47 px instead of the expected 60 px.

## Task

- Make the drag test wait until the zoom animation has fully settled before recording the baseline image position.
- Constraint: must not add an arbitrary `sleep`; the wait must be anchored to the actual animation state.

## Action

- `packages/cluster/frontend/e2e/image-viewer.spec.ts` (line ~196): inserted one `expect.poll` call after the initial zoom check and before `const before = await box(image)`:
  ```ts
  await expect.poll(() => image.evaluate(el => el.getAnimations().length)).toBe(0)
  ```
  This polls until `getAnimations()` returns an empty array, meaning the CSS glide has finished and the element is at its final position before the drag starts.

## Decisions

- **Poll `getAnimations().length === 0` vs. fixed timeout**: A fixed `page.waitForTimeout(200)` would work but couples the test to an implementation constant. Polling the actual animation state makes the test self-documenting and robust to future timing changes.
- **`expect.poll` vs. `page.waitForFunction`**: `expect.poll` integrates with Playwright's built-in retry/timeout machinery and produces a clearer failure message when the condition is never met.
- **Rejected — no wait at all**: The original code had no wait; this is what caused the flakiness on 30-run local validation (pass rate was inconsistent).
- Assumption: if the transition duration is changed in the future, `getAnimations().length` will still converge to 0 when the animation ends; revisit only if the animation is replaced with a JS animation that doesn't register in the Web Animations API.

## Result

- Drag test in `image-viewer.spec.ts` now waits for the zoom glide to settle before measuring the baseline position.
- Local 30/30 pass rate confirmed by the developer.
- Fixes the flaky pan-offset assertion (−7 ~ +47 px → consistently 60 px).
