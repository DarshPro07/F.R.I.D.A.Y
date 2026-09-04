import { test, expect, type Page } from '@playwright/test';

/**
 * Friday control room - end-to-end.
 *
 * Every test mocks the face gate open (/api/auth/status) so the page reaches
 * the unlocked HUD without a camera, and silences /api/tts so no audio plays.
 * The brain (/api/ask) is mocked per test: success, failure, and latency.
 * No static sleeps anywhere - only auto-waiting locators and web-first asserts.
 */

const UNLOCKED = { locked: false, gate: false, enrolled: true, auth_mode: 'face' };

async function mockShell(page: Page) {
  // Hermetic by construction. The HUD polls a dozen read endpoints, and the
  // client treats ANY 423 from the real server as "you are locked" and relocks
  // itself - so a suite that lets those polls through depends on whether the
  // owner's face happens to have unlocked the live server in the last few
  // minutes. (It did on one run and not the next; the results flipped.) The
  // catch-all is registered first, so the specific mocks below win over it.
  await page.route('**/api/**', r => r.fulfill({ json: {} }));
  await page.route('**/api/auth/status', r => r.fulfill({ json: UNLOCKED }));
  await page.route('**/api/tts**', r => r.fulfill({ status: 204, body: '' }));
}

function collectErrors(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  return errors;
}

const ask = (page: Page) => page.getByPlaceholder(/paste a prompt/i);
const send = (page: Page) => page.getByRole('button', { name: 'Send' });
const logbox = (page: Page) => page.locator('#logbox');

test.describe('Friday control room', () => {
  test.beforeEach(async ({ page }) => { await mockShell(page); });

  test('boots unlocked with the transcript and text box, and no runtime errors of its own', async ({ page }) => {
    const errors = collectErrors(page);
    await page.goto('/');
    await expect(ask(page)).toBeVisible();
    await expect(send(page)).toBeVisible();
    await expect(page.locator('#sttmode')).toHaveText(/^voice: /);
    // Third-party CDN / WebGL / model-download noise is not Friday's bug in
    // headless Chromium; only errors from Friday's own scripts count here.
    const own = errors.filter(e => !/cdn\.jsdelivr|three|WebGL|mediapipe|face-api|storage\.googleapis/i.test(e));
    expect(own, own.join('\n')).toEqual([]);
  });

  test('a typed prompt round-trips through the brain and lands in the transcript', async ({ page }) => {
    await page.route('**/api/ask', r => r.fulfill({
      json: { reply: 'Right there, sir.', action: 'screen.point', status: 'succeeded',
              used_capabilities: ['screen_point'] },
    }));
    await page.goto('/');
    await ask(page).fill('where do I click to schedule this email');
    await ask(page).press('Enter');
    await expect(logbox(page).locator('.log-line.you')).toContainText('where do I click');
    await expect(logbox(page).locator('.log-line.friday').last()).toContainText('Right there, sir.');
    // The brain logs what it DID as well as what it said, so there can be more
    // than one action row (the action itself, then the capabilities it used).
    await expect(logbox(page).locator('.log-line.act').filter({ hasText: 'used screen_point' })).toBeVisible();
    await expect(ask(page)).toHaveValue('');            // the box clears after sending
  });

  test('when the brain is down the transcript says so instead of hanging', async ({ page }) => {
    await page.route('**/api/ask', r => r.fulfill({ status: 500, json: { error: 'boom' } }));
    await page.goto('/');
    await ask(page).fill('hello');
    await send(page).click();
    await expect(logbox(page).locator('.log-line.friday').last())
      .toContainText(/could not reach my brain/i);
  });

  test('a slow brain still answers - the UI waits on the reply, not on a timer', async ({ page }) => {
    await page.route('**/api/ask', async r => {
      await new Promise(res => setTimeout(res, 2500));      // simulated latency spike
      await r.fulfill({ json: { reply: 'Sorry for the wait, sir.' } });
    });
    await page.goto('/');
    await ask(page).fill('are you there');
    await ask(page).press('Enter');
    await expect(logbox(page).locator('.log-line.friday').last())
      .toContainText('Sorry for the wait', { timeout: 15_000 });
  });

  test('an empty prompt sends nothing to the brain', async ({ page }) => {
    let calls = 0;
    await page.route('**/api/ask', r => { calls++; return r.fulfill({ json: { reply: 'x' } }); });
    await page.goto('/');
    await ask(page).fill('   ');
    await ask(page).press('Enter');
    await expect(logbox(page).locator('.log-line.you')).toHaveCount(0);
    expect(calls).toBe(0);
  });

  test('the speech-engine toggle cycles auto -> deepgram -> browser and persists across a reload', async ({ page }) => {
    await page.goto('/');
    const toggle = page.locator('#sttmode');
    await expect(toggle).toHaveText('voice: auto');
    await toggle.click();
    await expect(toggle).toHaveText('voice: deepgram');
    await page.reload();                                     // persistence (localStorage)
    await expect(page.locator('#sttmode')).toHaveText('voice: deepgram');
    await page.locator('#sttmode').click();                  // -> browser
    await expect(page.locator('#sttmode')).toHaveText('voice: browser');
    await page.locator('#sttmode').click();                  // -> auto
    await expect(page.locator('#sttmode')).toHaveText('voice: auto');
  });

  test('the transcript is never covered by the camera panel (the overlap regression)', async ({ page, isMobile }) => {
    test.skip(isMobile, 'the HUD panels are a desktop layout');
    await page.goto('/');
    const log = await page.locator('.b-log').boundingBox();
    const cam = await page.locator('.b-cap').boundingBox();
    expect(log && cam).toBeTruthy();
    const apart = log!.y + log!.height <= cam!.y || cam!.y + cam!.height <= log!.y
               || log!.x + log!.width <= cam!.x || cam!.x + cam!.width <= log!.x;
    expect(apart, 'transcript and camera panels intersect').toBe(true);
  });

  test('a locked gate shows the lock screen and marks the HUD locked', async ({ page }) => {
    await page.unroute('**/api/auth/status');
    await page.route('**/api/auth/status', r => r.fulfill({ json: { locked: true, gate: true, enrolled: true } }));
    await page.goto('/');
    await expect(page.locator('#lock')).toBeVisible();
    await expect(page.locator('[data-locked]').first()).toBeAttached();
  });
});

test.describe('mobile layout', () => {
  test('no horizontal page overflow', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'mobile project only');
    await mockShell(page);
    await page.goto('/');
    const [scrollW, clientW] = await page.evaluate(() =>
      [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(scrollW).toBeLessThanOrEqual(clientW + 1);
  });
});
