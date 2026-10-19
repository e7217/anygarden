# feat(ui): zoom and pan attached images in the image viewer (#882)

- Commit: `72683da` (72683da24a24ba10c92b6c042abee0f097bd5e3e)
- Author: Changyong Um
- Date: 2026-10-19T09:14:54+09:00
- PR: #882

## Situation

The attachment dialog previously rendered images in a static `<img>` tag inside a scrollable container capped at `70vh`. Large or high-resolution images could not be inspected in detail—users had to download the file to see it clearly. There was no way to zoom in, pan around, or compare regions of an image without leaving the app.

## Task

Replace the static image display in `ImageAttachment` with an interactive viewer that supports:

- Mouse wheel and trackpad pinch zoom around the cursor
- Two-finger touch pinch zoom
- Drag-to-pan when zoomed
- Double-click to toggle between fit and actual size
- Toolbar buttons (zoom out, zoom level / actual size, zoom in, fit)
- Keyboard shortcuts: `+` / `=` zoom in, `-` zoom out, `0` fit
- Scale range: fit (scale = 1) to 8×; edges never pushed inside the viewport
- Dialog widened to give the image more room

## Action

- **`packages/cluster/frontend/src/lib/imageZoom.ts`** (new, 76 lines): Pure geometry library. Defines `ZoomState` (`scale`, `offset`), `FIT` constant, `zoomAt` (zoom around an arbitrary point while clamping to bounds), `panBy` (translate with edge clamping), and `actualScale` (ratio of natural image size to fitted size). All functions are stateless and unit-tested.

- **`packages/cluster/frontend/src/lib/imageZoom.test.ts`** (new, 86 lines): Unit tests for `zoomAt`, `panBy`, and `actualScale` covering scale clamping, edge clamping, and zoom-around-cursor math.

- **`packages/cluster/frontend/src/components/ImageZoomViewer.tsx`** (new, 243 lines): React component that wraps the geometry. Key implementation choices:
  - Viewport div uses `touch-none` and a non-passive `wheel` listener attached via `useEffect` (React registers wheel listeners as passive by default; a non-passive listener is needed to call `event.preventDefault()` and suppress dialog scroll during zoom).
  - `ResizeObserver` on the viewport resets to fit and recomputes `actualScale` on resize.
  - Pointer events handle both mouse drag and two-finger pinch (tracks `Map<pointerId, Point>`; when two pointers are active, uses distance delta as zoom factor with midpoint as zoom center).
  - Toolbar uses `Button` with `variant="ghost"` and Lucide icons (`ZoomIn`, `ZoomOut`, `Minimize2`); zoom level button shows `Math.round(scale / actualScale * 100)%`.
  - `smooth` flag gates `motion-safe:transition-transform` so toolbar/key steps animate while pointer input does not.
  - Accessibility: viewport has `role="group"`, `tabIndex={0}`, `aria-label` (image name), `aria-describedby` pointing to a screen-reader-only hint; toolbar has `role="toolbar"`.

- **`packages/cluster/frontend/src/components/MessageAttachments.tsx`** (modified): Replaced static `<div><img/></div>` block in `ImageAttachment` with `<ImageZoomViewer src={image.src} alt={attachment.name} />`. Widened `DialogContent` from `max-w-[min(92vw,64rem)]` to `max-w-[min(96vw,80rem)]`.

- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`** (modified): Added i18n keys: `imageViewerLabel`, `imageViewerHint`, `imageZoomToolbar`, `imageZoomOut`, `imageZoomActual`, `imageZoomIn`, `imageZoomFit`.

- **`packages/cluster/frontend/e2e/image-viewer.spec.ts`** (new, 244 lines): Playwright E2E spec covering wheel zoom, toolbar buttons, keyboard shortcuts, double-click toggle, drag-to-pan, touch pinch, and edge clamping.

- **`packages/cluster/frontend/src/components/ImageZoomViewer.test.tsx`** (new, 80 lines): Component-level tests for mount, toolbar render, and key interactions.

## Decisions

**No third-party zoom library (react-zoom-pan-pinch rejected)**
The geometry is small (76 lines) and the toolbar, tokens, and interaction model are all custom. Adding `react-zoom-pan-pinch` as a runtime dependency would import ~15 KB for one dialog whose visual contract already diverges from the library's defaults. Writing the geometry from scratch keeps the bundle lean and makes the clamping logic (edges never come inside the viewport) fully explicit and testable.

**scale = 1 means "fit", not "100%"**
Fit-to-screen is the most useful starting point and avoids a jarring initial jump for large images. The `actualScale` helper computes the ratio of natural pixels to fitted pixels so the toolbar can still show a meaningful percentage (e.g. "42%"), and double-click snaps to `actual` (100% in natural-pixel terms) rather than an arbitrary scale. This model is consistent with how browser DevTools and Figma handle image zoom.

**Non-passive wheel listener via imperative `addEventListener`**
React's synthetic `onWheel` is always passive in React 17+, which prevents `event.preventDefault()`. The dialog scroll must be suppressed while the cursor is over the image. Attaching a non-passive listener imperatively in `useEffect` is the standard workaround; it is cleaned up on unmount so there is no leak.

**Options weighed**:
- Use a CSS `transform` on the img with `transform-origin: center` — chosen; keeps layout stable, scale 1 is always fit, and clamping is done in JS against viewport geometry.
- Render the image on a `<canvas>` — rejected; more complex, loses native lazy-loading and alt text, adds re-encoding overhead.
- Use `react-zoom-pan-pinch` — rejected; see above.

**Assumption**: the image is always available as a blob URL via `useArtifactBlobUrl`; if that hook ever returns a cross-origin URL, the `naturalWidth/naturalHeight` read in `onLoad` could be 0 for tainted canvases (not a concern here since no canvas is used, but worth noting if caching changes).

## Result

- Attachment images are now fully interactive: zoom (wheel, pinch, keyboard, toolbar), pan (drag), double-click to toggle fit/actual.
- Scale is bounded: minimum is fit (no empty border), maximum is 8×.
- E2E spec (`e2e/image-viewer.spec.ts`) and unit tests (`imageZoom.test.ts`, `ImageZoomViewer.test.tsx`) added; 746 lines net new across 7 files.
- Dialog is 25% wider (`80rem` vs `64rem`) to give large images more room without overflowing narrow viewports.
- No new runtime dependency introduced.
