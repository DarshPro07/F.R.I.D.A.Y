import { test, expect, BOOT_URL } from './fixtures';

/**
 * F2b — rendered rows must not build JS from server data.
 *
 * The original sinks looked like `onclick="gateApprove('${c.nonce}')"`: an
 * attacker-influenced value inside a JS string inside an HTML attribute. A
 * single apostrophe closed the string and the rest executed. Escaping helped,
 * but one site shipped with a RAW nonce, so the CLASS of bug is what needs
 * closing — not each instance.
 *
 * These tests feed a hostile nonce through the real gate render path and assert
 * (a) nothing executes, and (b) the action still works, with the exact hostile
 * string arriving intact at the API.
 */
const HOSTILE = `x');window.__pwned=1;//`;

test.describe('F2b — no script injection through rendered rows', () => {
  test('a hostile gate nonce cannot execute script and still rejects correctly', async ({
    page,
  }) => {
    let rejectedWith: string | null = null;
    let cleared = false;

    await page.route('**/api/gate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          browser_gates: [],
          tool_gates: cleared
            ? []
            : [
                {
                  nonce: HOSTILE,
                  action: 'browser.click',
                  question: 'Approve?',
                  seconds_left: 50,
                  state: 'PENDING',
                },
              ],
          durable: { waiting_runs: [], open_questions: [] },
        }),
      }),
    );

    await page.route('**/api/gate/reject', async (route) => {
      rejectedWith = route.request().postDataJSON()?.nonce ?? null;
      cleared = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto(BOOT_URL);
    await expect(page.locator('#toolgates')).toContainText('browser.click');

    // The payload must NOT have executed while rendering.
    expect(
      await page.evaluate(() => (window as any).__pwned),
      'rendering a hostile nonce must not execute script',
    ).toBeUndefined();

    // The nonce must be carried as data, not as code.
    const carrier = await page.evaluate(() => {
      const btn = [...document.querySelectorAll('#toolgates button')].find(
        (b) => b.textContent?.trim() === 'Reject',
      ) as HTMLElement;
      return {
        act: btn.dataset.act,
        arg: btn.dataset.arg,
        hasInlineHandler: btn.hasAttribute('onclick'),
      };
    });
    expect(carrier.act).toBe('gateReject');
    expect(carrier.arg).toBe(HOSTILE);
    expect(carrier.hasInlineHandler, 'no inline onclick may be generated').toBe(false);

    // And the delegated handler must still work, with the value intact.
    await page.getByRole('button', { name: /^Reject$/ }).click();
    await expect.poll(() => rejectedWith).toBe(HOSTILE);
    expect(await page.evaluate(() => (window as any).__pwned)).toBeUndefined();
  });

  test('the delegation whitelist refuses an unknown action', async ({
    bootedPage: page,
  }) => {
    // A forged data-act must not become an arbitrary function call.
    const fired = await page.evaluate(() => {
      (window as any).__evil = 0;
      (window as any).evilFn = () => {
        (window as any).__evil = 1;
      };
      const n = document.createElement('button');
      n.dataset.act = 'evilFn';
      n.textContent = 'x';
      document.body.appendChild(n);
      n.click();
      const v = (window as any).__evil;
      n.remove();
      return v;
    });
    expect(fired, 'only whitelisted actions may be dispatched').toBe(0);
  });
});
