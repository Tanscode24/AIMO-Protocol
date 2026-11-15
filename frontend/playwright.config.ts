import { defineConfig } from "@playwright/test";
import { E2E } from "./e2e/e2e-env.mjs";

// Fully isolated stack: mock Valio (:8199) -> backend with a fresh temp SQLite DB (:8100) -> production
// frontend build (:3100). Never reuses the developer's servers on :8000/:3000 or backend/aimo.db.
// Browser: the locally installed Google Chrome (channel "chrome"); set PW_CHANNEL="" to use Playwright's
// bundled Chromium after `npx playwright install chromium`.
export default defineConfig({
  testDir: "./e2e/tests",
  fullyParallel: false,
  workers: 1, // tests share one database and one mock-Valio configuration
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["json", { outputFile: "test-results/e2e-results.json" }]],
  use: {
    baseURL: E2E.uiUrl,
    channel: process.env.PW_CHANNEL ?? "chrome",
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: [
    { command: "node e2e/mock-valio.mjs", url: `http://127.0.0.1:${E2E.mockValioPort}/__mock/health`, reuseExistingServer: false, timeout: 30_000 },
    { command: "node e2e/start-backend.mjs", url: `${E2E.apiUrl}/health`, reuseExistingServer: false, timeout: 300_000, stdout: "pipe" },
    { command: "node e2e/start-frontend.mjs", url: `${E2E.uiUrl}/login`, reuseExistingServer: false, timeout: 400_000, stdout: "pipe" },
  ],
});
