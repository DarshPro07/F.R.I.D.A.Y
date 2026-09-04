import { defineConfig, devices } from '@playwright/test';

// Friday's control room runs on 127.0.0.1:8770 (not a generic :3000). The suite
// assumes it is already up - `Friday.exe` or `scripts/run_ui.py` - and reaches
// the unlocked state by mocking /api/auth/status, so no camera or face is
// needed in CI. Override the target with FRIDAY_URL.
export default defineConfig({
  testDir: '.',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: process.env.FRIDAY_URL ?? 'http://127.0.0.1:8770',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      // The page asks for the microphone and camera on unlock; answer with
      // fakes instead of a permission prompt that would hang headless runs.
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
    },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
});
