# refactor(ui): fold the System settings page into Machines as "This server" (#888)

- Commit: `379547a` (379547a0883b23c0a5685b76b9071e0d71881117)
- Author: Changyong Um
- Date: 2026-10-19T11:10:23+09:00
- PR: — (issue #888)

## Situation

The admin settings menu had a dedicated "System" page (`/admin/system`) whose sole function was to show the running anygarden server version against the latest PyPI release (`CHECK_PACKAGES = ["anygarden"]`). The Machines page already shows each daemon's version, update availability and a one-click update. On a unified node (`anygarden start`) the server and the local daemon are the same package, so the same "version / latest / update available" information was split across two menu entries, one of which held a single row.

## Task

- Fold the System page into the Machines page as a pinned "This server" entry that sits above the remote machines list.
- Rename `AdminSystem.tsx` → `ServerVersionPanel.tsx` and convert it from a self-fetching component to a prop-accepting presentational panel so `AdminMachines` can own the data lifecycle.
- Delete `AdminSystemPage.tsx` and redirect `/admin/system` → `/admin/machines`.
- Move the update-available badge in `SidebarAdminMenu` from the System link to the Machines link, and remove the System nav entry.
- Add i18n keys for the new "This server" / "이 서버" label; remove the now-unused `admin.system.title` and `navigation.system` keys.
- Cover the new entry with unit tests (`AdminMachines.server.test.tsx`) and an E2E spec (`admin-server-entry.spec.ts`).

## Action

- **`AdminSystem.tsx` → `ServerVersionPanel.tsx`** (`packages/cluster/frontend/src/components/`): Changed from a top-level page component that called `useUpdateStatus` internally to a panel that receives an `UpdateStatus` prop. Swapped `<div>` wrapper for `<section aria-labelledby>` and `<h1>` for `<h2>` to fit inside the Machines detail column. Removed the wide `max-w-4xl` constraint in favour of `max-w-2xl`.
- **`AdminMachines.tsx`**: Added `SERVER = '__server__'` sentinel alongside the existing `UNPLACED` sentinel; imported `useUpdateStatus` and `ServerVersionPanel`; added a pinned `<button>` entry at the top of the machines list that shows the running version and an "update available" badge when applicable; renders `<ServerVersionPanel>` in the right-hand detail column when the server entry is selected.
- **`App.tsx`**: Removed the lazy import of `AdminSystemPage`; replaced `<Route path="/admin/system" element={<AdminRoute><AdminSystemPage /></AdminRoute>}>` with `<Navigate to="/admin/machines" replace />`.
- **`AdminSystemPage.tsx`**: Deleted entirely (12 lines).
- **`SidebarAdminMenu.tsx`**: Removed the `{ labelKey: 'navigation.system', path: '/admin/system', icon: Package }` entry from the links array; moved the update badge condition from `link.path === '/admin/system'` to `link.path === '/admin/machines'`; removed `Package` from the lucide-react import.
- **`i18n/catalogs/admin.ts`**: Added `admin.machines.serverEntry` ("This server" / "이 서버"); removed `admin.system.title`.
- **`i18n/catalogs/workflows.ts`**: Removed `navigation.system` in both en and ko.
- **`useSystemVersion.ts`**: Exported `UpdateStatus` type alias (`ReturnType<typeof useUpdateStatus>`) so `ServerVersionPanel` can type its prop without re-exporting the hook internals.
- **`AdminMachines.server.test.tsx`** (new): Two Vitest/jsdom unit tests covering the `#888` entry — one checks the server row renders above machines with a version label; the other checks clicking it opens the panel with the pip update command and "Check for updates" button.
- **`ServerVersionPanel.test.tsx`** (renamed from `AdminSystem.test.tsx`): Updated imports and describe labels; removed the `vi.mock` for `useUpdateStatus` since the component now accepts a prop; passes `status()` directly instead.
- **`e2e/admin-server-entry.spec.ts`** (new): Playwright test verifying that `/admin/system` redirects to `/admin/machines`, the "This server" button shows `v0.21.0` and "update available", and clicking it reveals the pip install command.
- **`e2e/sidebar-admin-menu.spec.ts`**: Updated the keyboard-navigation tab-order assertion — after Machines, focus now lands on Skills (not System).
- **`Sidebar.test.tsx`**: Updated two tests: "all eight links" → "all seven links"; removed `System` from the visible-links array; asserted `queryByRole('button', { name: 'System' })` returns null; updated the update-badge assertion to target the Machines button.

## Decisions

- **Where the server info goes** (from `.tmp/plan-888-*.md`): a pinned list entry (chosen) vs. an inline compact card in the 256px-wide left column (the `pip install` command and errors overflow, and the mobile horizontal list has no room) vs. a banner above every machine detail (repeats unrelated info and blurs "this machine's version" with "the server's version"). The existing `UNPLACED` pseudo-entry tipped it: same layout, mobile and keyboard behaviour for free.
- **Not merged with the local machine card**: with remote-only machines (`anygarden server` + remote `anygarden machine`) the server and a daemon are different installs, so a single card would be wrong in the general case. They share a page but stay separate entries.
- **No one-click server update**: reinstalling and restarting the server itself depends on the process manager (systemd etc.); daemons were designed to exit and be restarted (#550). The panel keeps showing the command.
- **Initial selection unchanged**: the page's main job is agent placement; the menu badge and the entry badge are enough to surface an update.
- **Prop injection vs. self-fetching**: `ServerVersionPanel` was changed to accept an `UpdateStatus` prop rather than calling `useUpdateStatus` itself. This makes `AdminMachines` the single owner of the fetch lifecycle, avoids a second `/updates` poll within the page (the Sidebar still keeps its own instance for the menu badge, as before), and makes the panel trivially testable without mocking the hook. The cost is a slightly wider `AdminMachines` component; accepted because the panel is small.
- **Sentinel approach for selected state**: Using `SERVER = '__server__'` mirrors the existing `UNPLACED = '__unplaced__'` pattern already in `AdminMachines`. Keeping a single `selectedId: string | null` state rather than a discriminated union avoids refactoring the entire selection logic for one new entry.
- **`/admin/system` redirect vs. 404**: A `<Navigate replace>` preserves direct-link UX for bookmarks and external references; hard-coded 404 would break existing links silently. The `replace` flag avoids adding a dead entry to browser history.

## Result

- The "System" entry is gone from the admin sidebar; the Machines link carries the update-available badge when a new anygarden version is available on PyPI.
- Navigating to `/admin/system` redirects to `/admin/machines`.
- The "This server" entry renders at the top of the Machines list, shows the running version and an update badge, and opens the version/update panel on click.
- `AdminSystemPage.tsx` deleted; `AdminSystem.tsx` renamed to `ServerVersionPanel.tsx` with a cleaner prop-based API.
- 13 files changed; 150 insertions / 49 deletions; two new test files (unit + E2E).
- All existing Sidebar and AdminSystem tests updated to reflect the seven-link menu and prop-based panel.
