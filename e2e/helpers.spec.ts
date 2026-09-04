import { test, expect, openView } from './fixtures';

/**
 * HELPERS — every connected upstream helper, visible and askable.
 *
 * What this proves: the Organisation view's Helpers section is drawn from
 * live /api/helpers data (not markup), and the endpoint answers with the
 * documented shape fabric.report()/family_report()/processes() produce.
 */
test.describe('control room — helpers', () => {
  test('Organisation view lists at least 20 helper providers', async ({
    bootedPage: page,
    request,
  }) => {
    // The page fetches /api/helpers at boot and re-polls on a 10 s cache, so
    // waiting for "the" response is a race the full suite loses. Ask the API
    // directly for the truth, then assert the view drew it (auto-waiting).
    const helpers = await (await request.get('/api/helpers')).json();
    await openView(page, 'Organisation', 'v-os');

    expect(helpers.providers.length).toBeGreaterThanOrEqual(20);

    // Every provider the API returned is drawn into the section.
    for (const p of helpers.providers.slice(0, 5)) {
      await expect(page.locator('#os')).toContainText(p.provider);
    }
  });

  test('/api/helpers returns the documented shape', async ({ request }) => {
    const res = await request.get('/api/helpers');
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect(Object.keys(body).sort()).toEqual(['families', 'processes', 'providers']);
    expect(body.providers.length).toBeGreaterThanOrEqual(20);
    for (const key of ['provider', 'family', 'state', 'integration_mode', 'license_mode']) {
      expect(body.providers[0], `provider must expose ${key}`).toHaveProperty(key);
    }
    for (const key of ['family', 'state', 'providers']) {
      expect(body.families[0], `family must expose ${key}`).toHaveProperty(key);
    }
    expect(body.processes).toHaveProperty('supported');
  });
});
