import { defineConfig } from '@playwright/test'

const port = 4173
const baseURL = `http://127.0.0.1:${port}`

/**
 * Browser E2E deliberately starts only Vite.  Tests stub the small REST
 * contract they exercise, keeping this suite deterministic and separate
 * from the server/WebSocket integration suites and live-engine checks.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // Tests stub REST and share nothing, so they run in parallel on CI too;
  // one worker made the suite the slowest CI job (~9.4 min for 91 tests).
  workers: process.env.CI ? 4 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  // CI serves the production bundle that the job has already built: the dev
  // server compiles each route on first request, and with parallel workers
  // that pushed first paints past the 5 s expect timeout. Locally the dev
  // server keeps the edit-and-rerun loop build-free.
  webServer: {
    command: process.env.CI
      ? `npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`
      : `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
