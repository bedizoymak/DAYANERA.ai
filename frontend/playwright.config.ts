import { defineConfig } from '@playwright/test';

// Browser smoke test against the RUNNING local stack (scripts/start-local.ps1).
// Uses the installed Microsoft Edge (channel "msedge"): no browser download.
export default defineConfig({
  testDir: './e2e',
  timeout: 20 * 60 * 1000, // local CPU inference is slow
  expect: { timeout: 15 * 60 * 1000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.DAYANERA_UI_URL ?? 'http://127.0.0.1:5173',
    channel: 'msedge',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'off',
  },
});
