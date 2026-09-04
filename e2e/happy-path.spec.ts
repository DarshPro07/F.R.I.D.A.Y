import { test, expect, openView, waitForBoot } from './fixtures';

/**
 * HAPPY PATH — the main execution flow of the control room.
 *
 * What this proves: the page boots against a live `friday/ui_server.py`, the
 * four views mount from real API data, and the header status chips reflect
 * what /api/state actually returned. Every assertion is on something the
 * server produced, so a stubbed-out server would fail these.
 */
test.describe('control room — happy path', () => {
  test('boots, unlocks, and renders the HUD from live state', async ({
    bootedPage: page,
    diagnostics,
  }) => {
    await expect(page).toHaveTitle('Friday');

    // The brand and the four view tabs are the app's skeleton.
    await expect(page.getByText('F.R.I.D.A.Y', { exact: true }).first()).toBeVisible();
    for (const name of ['Core', 'Control room', 'Organisation', 'Memory']) {
      await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
    }

    // The island reports "listening" once the mic engine is live, which the
    // boot sequence can reach on its own. Assert on the control's own state
    // rather than on a specific island string.
    await expect(page.locator('#islandtxt')).not.toHaveText('locked');

    // The objective block is populated from /api/state, never from markup.
    await expect(page.locator('#objtext')).not.toBeEmpty();

    // A booted control room must not be throwing.
    expect(diagnostics.pageErrors, 'uncaught page errors').toEqual([]);
  });

  test('/api/state returns the documented envelope', async ({ request }) => {
    const res = await request.get('/api/state');
    expect(res.status()).toBe(200);

    const state = await res.json();
    // Contract from friday/ui_server.py build_state().
    expect(state).toMatchObject({ v: 1 });
    for (const key of [
      'at', 'db', 'system', 'mcp', 'connections',
      'memory', 'todos', 'metrics', 'agency', 'build',
    ]) {
      expect(state, `build_state() must expose ${key}`).toHaveProperty(key);
    }

    // The MCP inventory is read from capability_router, so it must be real.
    expect(state.mcp.status).toBe('ok');
    expect(state.mcp.core_count).toBeGreaterThan(0);
    expect(state.mcp.total).toBeGreaterThanOrEqual(state.mcp.core_count);
  });

  test('health endpoint answers before anything else is asked of it', async ({ request }) => {
    const res = await request.get('/health');
    expect(res.status()).toBe(200);
    expect((await res.text()).trim()).toBe('ok');
  });

  test('navigating to every view mounts its section', async ({ bootedPage: page }) => {
    await openView(page, 'Control room', 'v-room');
    // The room renders from /api/state; the Objective section is always present.
    await expect(page.locator('#cr')).not.toBeEmpty();

    await openView(page, 'Organisation', 'v-os');
    await expect(page.locator('#os')).not.toBeEmpty();

    await openView(page, 'Memory', 'v-mem');
    await expect(page.locator('#memtiers')).not.toBeEmpty();

    await openView(page, 'Core', 'v-hud');
    await expect(page.locator('#orbroot')).toBeVisible();
  });

  test('the memory tier panel is built from /api/memory/tiers', async ({
    bootedPage: page,
  }) => {
    // Assert on the network contract AND the render, so a UI that draws
    // plausible numbers from nowhere cannot pass.
    const tiersPromise = page.waitForResponse(
      (r) => r.url().includes('/api/memory/tiers') && r.status() === 200,
    );
    await openView(page, 'Memory', 'v-mem');
    const tiers = await (await tiersPromise).json();

    expect(Array.isArray(tiers.tiers)).toBe(true);
    expect(tiers.tiers.length).toBeGreaterThan(0);
    expect(tiers).toHaveProperty('budget_tokens');

    // Every tier the API returned is drawn.
    for (const tier of tiers.tiers) {
      await expect(page.locator('#memtiers')).toContainText(tier.name);
    }
  });
});
