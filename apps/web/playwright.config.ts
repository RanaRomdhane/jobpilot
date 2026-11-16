import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end journeys. The stack (web, API, worker, stub LLM and Gmail) is started by
 * `tests/e2e/run_stack.py` (`make e2e`), which also sets the E2E_* variables.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 240_000,
  expect: { timeout: 60_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    locale: "fr-FR",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
