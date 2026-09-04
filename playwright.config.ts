import { defineConfig, devices } from '@playwright/test';

/**
 * Friday control-room E2E configuration.
 *
 * The server under test is `friday/ui_server.py` (Starlette) served by
 * `scripts/run_ui.py`. Two things about this app shape the config:
 *
 * 1. The face gate (`friday/access.py` GateMiddleware) answers 423 to every
 *    /api/* route without a session cookie. A test run cannot enrol a real
 *    face, so the suite launches the server with FRIDAY_FACE_GATE=0. That is
 *    the same switch `scripts/run_ui.py --bypass-face` sets, and the gate
 *    itself is covered separately by gate.spec.ts with the gate ENABLED.
 * 2. `--no-browser` stops the server opening a Chrome window of its own on
 *    each run, which would fight Playwright for the camera.
 *
 * No `waitForTimeout` anywhere in this suite: every wait is an expectation on
 * observable state, so a slow machine makes a test slower, not flakier.
 */

const HOST = process.env.FRIDAY_UI_HOST ?? '127.0.0.1';
const PORT = process.env.FRIDAY_UI_PORT ?? '8781';
const BASE_URL = `http://${HOST}:${PORT}`;

// The repo venv holds Starlette/uvicorn; the system python does not.
// Backslashed so cmd.exe (Playwright's shell on Windows) resolves it.
const PYTHON = process.env.FRIDAY_PYTHON ?? '.venv\\Scripts\\python.exe';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // one UI server, one SQLite file, one camera
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e-report' }]],
  // Booting the control room means loading face-api weights and opening a
  // camera; on a cold cache that is comfortably past Playwright's 30s default.
  timeout: 60_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 10_000,
    // The control room asks for camera + mic at boot. Granting them keeps the
    // permission prompt from blocking the page; the fake device flags below
    // mean no real hardware is touched.
    permissions: ['camera', 'microphone'],
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          args: [
            '--use-fake-ui-for-media-stream',
            '--use-fake-device-for-media-stream',
            '--autoplay-policy=no-user-gesture-required',
          ],
        },
      },
    },
  ],

  webServer: {
    command: `${PYTHON} scripts/run_ui.py --no-browser --bypass-face`,
    url: `${BASE_URL}/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
    env: {
      FRIDAY_UI_HOST: HOST,
      FRIDAY_UI_PORT: PORT,
      // Never point a test run at data/ada.sqlite3 (AGENTS.md: the live DB is
      // not to be touched). ui_server opens it read-only, but a test that
      // asserts on live rows is a test that fails for the wrong reason.
      ADA_DB: process.env.ADA_DB ?? 'data/e2e-ada.sqlite3',
      FRIDAY_FACE_GATE: '0',
    },
  },
});
