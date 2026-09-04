import { test as base, expect, type Page } from '@playwright/test';

/**
 * Shared fixtures for the Friday control room.
 *
 * The control room boots asynchronously: it reads /api/auth/status, loads
 * face-api weights, opens the camera, then builds the orb and starts polling.
 * Every test needs "the app has finished booting" and none of them should
 * express that as a sleep, so it is a fixture with a real assertion behind it.
 */

/** Console errors + failed requests, so a silently broken page fails loudly. */
export type PageDiagnostics = {
  consoleErrors: string[];
  pageErrors: string[];
};

type Fixtures = {
  diagnostics: PageDiagnostics;
  /** A page whose boot overlay has cleared and whose HUD is interactive. */
  bootedPage: Page;
};

export const test = base.extend<Fixtures>({
  diagnostics: async ({ page }, use) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => pageErrors.push(err.message));

    await use({ consoleErrors, pageErrors });
  },

  bootedPage: async ({ page, diagnostics }, use) => {
    void diagnostics; // ensure listeners attach before the first navigation
    await page.goto(BOOT_URL);
    await waitForBoot(page);
    await use(page);
  },
});

export { expect };

/**
 * Boot the control room in its own low-power mode.
 *
 * `?lite` is a first-party, documented flag (see LITE_ON() in ui/index.html):
 * lighter orb, gestures off, "GPU saved for the camera". A headless CI browser
 * has no real GPU, and the full WebGL orb saturates the main thread badly
 * enough that Playwright clicks stall mid-action. Using the app's OWN degraded
 * mode keeps the tests honest — this is a supported configuration, not a hack —
 * while removing the render loop as a source of flake.
 */
export const BOOT_URL = '/?lite';

/**
 * Wait until the control room is actually usable.
 *
 * The boot overlay (#boot) gains the class `gone` once the gate has been read
 * and the camera has either opened or been ruled out. With FRIDAY_FACE_GATE=0
 * the page unlocks immediately, so `body` loses its `data-locked` attribute.
 * Asserting on both is what makes this a state wait rather than a delay.
 */
export async function waitForBoot(page: Page): Promise<void> {
  await expect(page.locator('#boot')).toHaveClass(/gone/, { timeout: 25_000 });
  await expect(page.locator('body')).not.toHaveAttribute('data-locked', /.*/, {
    timeout: 25_000,
  });
}

/** The four top-level views, by their accessible tab names. */
export const VIEWS = ['Core', 'Control room', 'Organisation', 'Memory'] as const;
export type ViewName = (typeof VIEWS)[number];

/** Switch views through the real tab control, then assert the section is on. */
export async function openView(page: Page, name: ViewName, sectionId: string) {
  await page.getByRole('button', { name, exact: true }).click();
  await expect(page.locator(`#${sectionId}`)).toHaveClass(/\bon\b/);
}
