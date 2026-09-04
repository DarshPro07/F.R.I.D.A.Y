import { test, expect, waitForBoot, BOOT_URL } from './fixtures';

/**
 * REAL-TIME UI STATE TRANSITIONS.
 *
 * The control room has an explicit voice/UI state machine surfaced through the
 * status island (`#island`): locked -> idle -> listening -> thinking ->
 * speaking, plus `gate` when a confirmation is waiting. These tests drive the
 * transitions through real controls and real (intercepted) server events, and
 * assert the class actually changes — that is the difference between a state
 * machine and four CSS classes nobody sets.
 */
test.describe('real-time state transitions', () => {
  test('the mic control reflects real speech-engine availability', async ({
    bootedPage: page,
  }) => {
    // Headless Chromium has a fake audio device and no real speech engine, so
    // whether a click reaches a listening state is genuinely non-deterministic
    // (announceMic() enumerates devices async; SR.start() can fail later).
    // Waiting for a particular outcome makes this test flaky for reasons that
    // have nothing to do with the product.
    //
    // What IS deterministic, and what actually matters, is the INVARIANT: the
    // button's pressed state, its accessible name, and the island must never
    // disagree. A control announcing "Unmute" while reporting pressed=true is
    // the real bug (that is exactly the F4 class of defect). So: click, let the
    // engine land wherever it lands, then assert self-consistency.
    const mic = page.locator('#mic');
    const wasListening = (await mic.getAttribute('aria-pressed')) === 'true';

    // The accessible name must always describe the ACTION, never the state.
    await expect(
      page.getByRole('button', {
        name: wasListening ? /mute microphone/i : /unmute microphone/i,
      }),
    ).toBeVisible();

    await mic.click();

    if (wasListening) {
      // Turning it OFF never depends on an engine: it must always succeed.
      await expect(mic).toHaveAttribute('aria-pressed', 'false');
      await expect(page.getByRole('button', { name: /unmute microphone/i })).toBeVisible();
      return;
    }

    // Let the engine settle either way, then check the three surfaces agree.
    // expect.poll re-reads until consistent, so this needs no fixed delay and
    // cannot pass on a torn intermediate state.
    await expect
      .poll(
        async () =>
          page.evaluate(() => {
            const m = document.getElementById('mic')!;
            const listening = m.getAttribute('aria-pressed') === 'true';
            const label = (m.getAttribute('aria-label') || '').toLowerCase();
            const island = document.getElementById('island')!.className;
            const labelOk = listening
              ? label.startsWith('mute')
              : label.startsWith('unmute');
            // The island may legitimately show a higher-priority state
            // (gate/speaking); it must simply never claim to be listening
            // when the mic is off.
            const islandOk = listening || !/\blistening\b/.test(island);
            return labelOk && islandOk;
          }),
        {
          message:
            'mic pressed-state, aria-label and island must agree — a control that ' +
            'announces the wrong action or shows a stale listening state is lying',
        },
      )
      .toBe(true);
  });

  test('the voice-output control toggles without breaking the island', async ({
    bootedPage: page,
  }) => {
    const snd = page.locator('#snd');
    const before = await snd.getAttribute('aria-pressed');
    const wasOn = before === 'true';

    await expect(
      page.getByRole('button', {
        name: wasOn ? /mute friday's voice/i : /unmute friday's voice/i,
      }),
    ).toBeVisible();

    await snd.click();

    await expect(snd).toHaveAttribute('aria-pressed', wasOn ? 'false' : 'true');
    // Regression guard: toggleSound() used to update aria-pressed but leave a
    // stale aria-label, so the button announced the wrong action after a click.
    await expect(
      page.getByRole('button', {
        name: wasOn ? /unmute friday's voice/i : /mute friday's voice/i,
      }),
    ).toBeVisible();
  });

  test('asking a question drives idle -> thinking -> answered', async ({ page }) => {
    // Intercept the brain so the transition is deterministic and no model is
    // called. The delay is served by the route, not by waitForTimeout, so the
    // test observes a genuinely in-flight request.
    let release: () => void = () => {};
    const inFlight = new Promise<void>((resolve) => (release = resolve));

    await page.route('**/api/ask', async (route) => {
      await inFlight;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          reply: 'All systems nominal, boss.',
          action: 'status',
          used_capabilities: [],
        }),
      });
    });

    await page.goto(BOOT_URL);
    await waitForBoot(page);

    const input = page.getByPlaceholder(/type or paste a prompt/i);
    await input.fill('system status');
    await input.press('Enter');

    // THINKING: asserted while the request is genuinely outstanding.
    await expect(page.locator('#island')).toHaveClass(/thinking/);
    await expect(page.locator('#islandtxt')).toHaveText('thinking');

    release();

    // The reply lands in the transcript, attributed to Friday.
    await expect(page.locator('#logbox')).toContainText('All systems nominal, boss.');
    await expect(page.locator('#island')).not.toHaveClass(/thinking/);
  });

  test('a brain failure is reported, never narrated as success', async ({ page }) => {
    await page.route('**/api/ask', (route) =>
      route.fulfill({ status: 503, contentType: 'text/plain', body: 'brain offline' }),
    );

    await page.goto(BOOT_URL);
    await waitForBoot(page);

    const input = page.getByPlaceholder(/type or paste a prompt/i);
    await input.fill('what is my name');
    await input.press('Enter');

    // The UI must say it could not reach the brain. PRODUCT.md: honest state.
    await expect(page.locator('#logbox')).toContainText(/could not reach my brain/i);
  });

  test('a pending confirmation gate surfaces and can be rejected', async ({ page }) => {
    const nonce = 'test-nonce-1';
    let rejected = false;

    await page.route('**/api/gate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          browser_gates: [],
          tool_gates: rejected
            ? []
            : [
                {
                  nonce,
                  action: 'browser.click',
                  target: '#buy-now',
                  question: 'Approve click on #buy-now?',
                  seconds_left: 55,
                  state: 'PENDING',
                },
              ],
          durable: { waiting_runs: [], open_questions: [] },
        }),
      }),
    );

    await page.route('**/api/gate/reject', async (route) => {
      const body = route.request().postDataJSON();
      expect(body.nonce, 'the UI must reject the exact nonce it displayed').toBe(nonce);
      rejected = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto(BOOT_URL);
    await waitForBoot(page);

    // The gate card appears and names the action awaiting approval.
    const gates = page.locator('#toolgates');
    await expect(gates).toContainText('browser.click');
    await expect(gates).toContainText('Approve click on #buy-now?');

    // The island must ESCALATE to the gate and stay there. Before the
    // precedence ladder this raced: the 6s state poll and the mic engine
    // overwrote "gate" with "idle"/"listening", hiding a pending confirmation.
    await expect(page.locator('#island')).toHaveClass(/gate/);

    // F3b — the regression guard for the 4s re-render defect. Stamp the live
    // Reject button, let at least one gate poll tick elapse, and require the
    // SAME DOM node to survive. The old code rebuilt innerHTML every tick, so
    // the stamped node was replaced and a click could land on a detached
    // element. This fails deterministically against that version.
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll('#toolgates button')].find(
        (b) => b.textContent?.trim() === 'Reject',
      ) as HTMLElement;
      (btn as any).__identity = 'stamped';
    });

    const ticks = await page.evaluate(
      () =>
        new Promise<number>((resolve) => {
          // Count real /api/gate responses rather than waiting a fixed time.
          let seen = 0;
          const orig = window.fetch;
          window.fetch = async (...args: any[]) => {
            const res = await (orig as any).apply(window, args);
            if (String(args[0]).includes('/api/gate') && ++seen >= 2) {
              window.fetch = orig;
              resolve(seen);
            }
            return res;
          };
        }),
    );
    expect(ticks, 'the gate poll must have run').toBeGreaterThanOrEqual(2);

    const survived = await page.evaluate(() => {
      const btn = [...document.querySelectorAll('#toolgates button')].find(
        (b) => b.textContent?.trim() === 'Reject',
      ) as any;
      return btn?.__identity === 'stamped';
    });
    expect(
      survived,
      'Reject must be the same DOM node after a gate poll tick — rebuilding it ' +
        'under the pointer drops clicks on a security confirmation',
    ).toBe(true);

    await page.getByRole('button', { name: /^Reject$/ }).click();

    // Once rejected the card must clear — a stale gate is a lie about state.
    await expect(gates).not.toContainText('browser.click');
  });

  test('the SSE stream delivers frames the UI subscribes to', async ({ page }) => {
    await page.goto(BOOT_URL);
    await waitForBoot(page);

    // Read the real /events stream directly: it must open and emit at least
    // one framed event (model.health is sent immediately on connect).
    const frame = await page.evaluate<string>(
      () =>
        new Promise((resolve, reject) => {
          const es = new EventSource('/events');
          const timer = setTimeout(() => {
            es.close();
            reject(new Error('no SSE frame within 15s'));
          }, 15_000);
          es.addEventListener('model.health', (ev) => {
            clearTimeout(timer);
            es.close();
            resolve((ev as MessageEvent).data);
          });
          es.onerror = () => {
            clearTimeout(timer);
            es.close();
            reject(new Error('SSE connection error'));
          };
        }),
    );

    const payload = JSON.parse(frame);
    expect(payload.type).toBe('model.health');
    expect(payload.payload.connected).toBe(true);
    expect(payload).toHaveProperty('at');
  });
});
