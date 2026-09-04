import { test, expect } from './fixtures';

/**
 * THE PAUSE RULE (owner, 2026-09-02).
 *
 * "If I pause, the system should: respect the pause and wait for a set time
 *  limit; if the limit expires without additional speech, no input should be
 *  processed [beyond what was said]; if someone speaks within those few
 *  seconds, the speech should be captured."
 *
 * Both speech engines hand the page a "final" chunk the moment the recogniser
 * closes a phrase. The page must not send that chunk to the brain; it must
 * hold it, restart a window, and only send the whole utterance once the
 * window lapses with no further speech. These tests drive the accumulator
 * (`turnPush` / `turnHold` / `turnCommit`) exactly the way the engines do
 * and count what reaches `/api/ask`.
 *
 * Timing is done INSIDE the page (one evaluate schedules the whole
 * sequence) because a Playwright round trip on a loaded host measured
 * >250ms, which is the same order as a real pause; driving the clock from
 * the test would be testing the harness.
 */
const WINDOW_MS = 1500;

async function driveTurns(
  page: import('@playwright/test').Page,
  steps: Array<{ at: number; push?: string; hold?: true }>,
): Promise<void> {
  await page.evaluate(
    ([steps]) =>
      new Promise<void>((done) => {
        const w = window as any;
        let last = 0;
        for (const s of steps) {
          setTimeout(() => {
            if (s.push !== undefined) w.turnPush(s.push, 1, 0.9);
            if (s.hold) w.turnHold();
          }, s.at);
          last = Math.max(last, s.at);
        }
        setTimeout(done, last + 20);
      }),
    [steps] as const,
  );
}

test.describe('the pause rule', () => {
  let asked: string[];

  test.beforeEach(async ({ bootedPage: page }) => {
    asked = [];
    await page.evaluate((ms) => (window as any).setPauseMs(ms), WINDOW_MS);
    // The brain is not the subject: answer instantly and record what arrived.
    await page.route('**/api/ask', async (route) => {
      asked.push(route.request().postDataJSON().text);
      await route.fulfill({ json: { reply: 'ok' } });
    });
  });

  test('a mid-sentence pause shorter than the window does NOT send anything', async ({
    bootedPage: page,
  }) => {
    // Two finals 700ms apart - a breath to think - inside one 1500ms window.
    await driveTurns(page, [
      { at: 0, push: 'I was thinking about' },
      { at: 700, push: 'a system where we would be' },
    ]);
    expect(asked).toEqual([]); // nothing left the page yet
    expect(await page.evaluate(() => (window as any).turnOpen())).toBe(
      'I was thinking about a system where we would be',
    );
    // The window lapses: ONE utterance, the whole thought, goes out.
    await expect.poll(() => asked.length, { timeout: 6_000 }).toBe(1);
    expect(asked[0]).toBe('I was thinking about a system where we would be');
    expect(await page.evaluate(() => (window as any).turnOpen())).toBe('');
  });

  test('a partial result inside the window keeps the turn open', async ({
    bootedPage: page,
  }) => {
    // He keeps talking; the engine only has interim text. Partials at 900,
    // 1800, 2700ms: each re-arms the window, so 2700ms > 1500ms must NOT
    // have committed before the final arrives.
    await driveTurns(page, [
      { at: 0, push: 'open the' },
      { at: 900, hold: true },
      { at: 1800, hold: true },
      { at: 2700, hold: true },
      { at: 3000, push: 'control room please' },
    ]);
    expect(asked).toEqual([]);
    await expect.poll(() => asked.length, { timeout: 6_000 }).toBe(1);
    expect(asked[0]).toBe('open the control room please');
  });

  test('silence past the window with nothing said sends nothing', async ({
    bootedPage: page,
  }) => {
    await driveTurns(page, [{ at: 0, hold: true }]); // a partial, never a final
    await page.waitForTimeout(WINDOW_MS + 800);
    expect(asked).toEqual([]);
  });

  test('two thoughts separated by more than the window are two turns', async ({
    bootedPage: page,
  }) => {
    await driveTurns(page, [
      { at: 0, push: 'what time is it' },
      { at: WINDOW_MS + 900, push: 'and the weather' },
    ]);
    await expect.poll(() => asked.length, { timeout: 8_000 }).toBe(2);
    expect(asked).toEqual(['what time is it', 'and the weather']);
  });

  test('the window is a real setting: persisted and bounded', async ({
    bootedPage: page,
  }) => {
    await page.evaluate(() => (window as any).setPauseMs(99_999));
    expect(await page.evaluate(() => (window as any).getPauseMs())).toBe(8000);
    await page.evaluate(() => (window as any).setPauseMs(2500));
    await page.reload();
    await expect(page.locator('#boot')).toHaveClass(/gone/, { timeout: 25_000 });
    expect(await page.evaluate(() => (window as any).getPauseMs())).toBe(2500);
  });
});
