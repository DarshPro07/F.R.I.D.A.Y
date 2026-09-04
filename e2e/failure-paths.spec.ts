import { test, expect, openView, waitForBoot, BOOT_URL } from './fixtures';

/**
 * FAILURE PATHS — network interception, timeouts, invalid payloads.
 *
 * These are the tests that matter for a cockpit whose whole product claim is
 * "honest state: unavailable is shown as unavailable" (PRODUCT.md). Each one
 * breaks a specific dependency and asserts the UI degrades rather than lying
 * or throwing.
 */
test.describe('control room — degradation and failure handling', () => {
  test('a 500 from /api/state does not crash the page', async ({ page, diagnostics }) => {
    await page.route('**/api/state', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'state unavailable' }),
      }),
    );

    await page.goto(BOOT_URL);
    await waitForBoot(page);

    // The shell must still be interactive: poll() swallows the error and the
    // page keeps running rather than white-screening.
    await expect(page.getByRole('button', { name: 'Core', exact: true })).toBeVisible();
    await expect(page.locator('#orbroot')).toBeVisible();
    expect(diagnostics.pageErrors, 'a failed poll must not throw').toEqual([]);
  });

  test('a hung /api/state is survived without a frozen UI', async ({ page }) => {
    // Never resolve the route: this is the timeout case, expressed as a real
    // stalled request rather than as a sleep.
    await page.route('**/api/state', async () => {
      /* deliberately never fulfilled */
    });

    await page.goto(BOOT_URL);
    await waitForBoot(page);

    // The tab control is client-side and must stay responsive while a poll
    // is outstanding.
    await openView(page, 'Memory', 'v-mem');
    await expect(page.locator('#v-mem')).toHaveClass(/\bon\b/);
  });

  test('malformed JSON from an API is contained', async ({ page, diagnostics }) => {
    await page.route('**/api/memory/tiers', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '{ this is not json',
      }),
    );

    await page.goto(BOOT_URL);
    await waitForBoot(page);
    await openView(page, 'Memory', 'v-mem');

    // pollTiers() catches its own parse failure; the view still mounts.
    await expect(page.locator('#v-mem')).toHaveClass(/\bon\b/);
    expect(diagnostics.pageErrors).toEqual([]);
  });

  test('/api/ask rejects an empty prompt without inventing a reply', async ({ request }) => {
    const res = await request.post('/api/ask', { data: { text: '   ' } });
    expect(res.status()).toBe(200);

    const body = await res.json();
    // friday/voice_brain.py reply(): empty in, empty out, explicitly flagged.
    expect(body.empty).toBe(true);
    expect(body.reply).toBe('');
  });

  test('/api/browser/open refuses a missing url with 400', async ({ request }) => {
    const res = await request.post('/api/browser/open', { data: {} });
    expect(res.status()).toBe(400);
    expect((await res.json()).error).toContain('no url');
  });

  test('/api/desk validates its query parameter', async ({ request }) => {
    const bad = await request.get('/api/desk?what=../../etc/passwd');
    expect(bad.status()).toBe(400);
    expect((await bad.json()).error).toContain('what must be');
  });

  test('/api/vault/file refuses a traversal path', async ({ request }) => {
    // friday/vault.py _safe() resolves and re-checks containment; anything
    // outside the vault must 404 rather than read.
    const res = await request.get(
      '/api/vault/file?path=' + encodeURIComponent('../../../../Windows/win.ini'),
    );
    expect(res.status()).toBe(404);
  });

  test('/api/browser/shot refuses a name that is not a screenshot', async ({ request }) => {
    const res = await request.get('/api/browser/shot?name=' + encodeURIComponent('../../.env'));
    expect(res.status()).toBe(404);
  });

  test('an unknown route 404s rather than leaking a stack trace', async ({ request }) => {
    const res = await request.get('/api/does-not-exist');
    expect(res.status()).toBe(404);
    expect(await res.text()).not.toContain('Traceback');
  });
});
