import { test, expect } from './fixtures';

/**
 * Two owner asks from 2026-09-02.
 *
 * 1. Mute is HIS microphone, not her voice: muting must stop listening and
 *    must not stop or prevent speech. What he had already said before
 *    muting is sent, not thrown away.
 * 2. After a delegation she reports progress on her own - each new progress
 *    line once, the result once, then silence.
 */
test.describe('mute is the mic, and Hermes progress is spoken', () => {
  test('muting commits the open turn and leaves her voice alone', async ({
    bootedPage: page,
  }) => {
    const asked: string[] = [];
    await page.route('**/api/ask', async (route) => {
      asked.push(route.request().postDataJSON().text);
      await route.fulfill({ json: { reply: 'ok' } });
    });
    await page.evaluate(() => (window as any).setPauseMs(5000));
    await page.evaluate(() => {
      const w = window as any;
      w.LISTEN = true; // as if the mic were on (headless has no real engine)
      w.turnPush('remind me to call mum', 1, 0.9);
    });
    expect(asked).toEqual([]);
    await page.evaluate(() => (window as any).stopListening());
    await expect.poll(() => asked.length, { timeout: 3_000 }).toBe(1);
    expect(asked[0]).toBe('remind me to call mum');
    // Her voice: the sound control is untouched by the mic control.
    expect(await page.evaluate(() => (window as any).soundOn())).toBe(true);
    await expect(page.locator('#snd')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#mic')).toHaveAttribute('aria-pressed', 'false');
  });

  test('a delegation is followed: progress spoken on change, result once', async ({
    bootedPage: page,
  }) => {
    let seq = 0;
    let status = 'WORKING';
    await page.route('**/api/ask', async (route) => {
      await route.fulfill({
        json: { reply: 'Hermes has it, sir.', action: 'hermes.delegate',
                used_capabilities: ['hermes'], work_run_id: 'hermes-test1' },
      });
    });
    await page.route('**/api/hermes/progress**', async (route) => {
      await route.fulfill({
        json: { runs: [{ work_run_id: 'hermes-test1', status, seq,
                         line: status === 'WORKING' ? `Hermes is editing policy.py - step ${seq}` : 'Hermes finished after 3 steps.',
                         tools: seq, result: 'Added the comment.' }] },
      });
    });
    await page.evaluate(() => (window as any).HERMES_MIN_GAP_OVERRIDE = 0);
    await page.evaluate(() => (window as any).askJarvis('delegate this to hermes: do the thing'));
    await expect.poll(() => page.evaluate(() => (window as any).hermesFollowing().length)).toBe(1);

    const spoken = () => page.evaluate(() =>
      [...document.querySelectorAll('#conv .m.friday, #conv [data-who="Friday"], #conv .friday')].map((e) => e.textContent || ''));
    const said = async () => (await page.evaluate(() => document.getElementById('conv')!.textContent || ''));

    seq = 1; await page.evaluate(() => (window as any).pollHermes());
    await expect.poll(said).toContain('step 1');
    // Same seq again: nothing new is said.
    await page.evaluate(() => (window as any).pollHermes());
    const once = (await said()).split('step 1').length - 1;
    expect(once).toBe(1);
    void spoken;

    status = 'COMPLETE'; seq = 2; await page.evaluate(() => (window as any).pollHermes());
    await expect.poll(said).toContain('Hermes is done, sir. Added the comment.');
    // Settled: following stops; a further poll says nothing more.
    await page.evaluate(() => (window as any).pollHermes());
    const done = (await said()).split('Hermes is done').length - 1;
    expect(done).toBe(1);
  });
});
