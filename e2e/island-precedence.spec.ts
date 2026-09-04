import { test, expect, BOOT_URL } from './fixtures';

/**
 * STATUS-ISLAND PRECEDENCE (G3).
 *
 * The E2E gate test cannot guard this: the original defect was a timing race
 * between three pollers, and a race that does not happen to fire proves
 * nothing. These tests instead drive setIsland() directly in the page and
 * assert the ladder's contract, which IS deterministic.
 *
 * Contract: locked > gate > speaking > thinking > listening > idle.
 * A lower-ranked writer must not replace a higher-ranked state; the owner of a
 * high-ranked state releases it explicitly via clearIsland().
 */
test.describe('status island precedence', () => {
  test('a pending gate is not overwritten by lower-priority pollers', async ({
    bootedPage: page,
  }) => {
    const result = await page.evaluate(() => {
      const w = window as any;
      const cls = () => document.getElementById('island')!.className;

      w.clearIsland('', 'idle');
      w.setIsland('gate', '1 waiting for you');
      const afterGate = cls();

      // Exactly what used to clobber it: the 6s state poll and the mic engine.
      w.setIsland('', 'idle');
      const afterIdle = cls();
      w.setIsland('listening', 'listening');
      const afterListening = cls();
      w.setIsland('thinking', 'thinking');
      const afterThinking = cls();

      // The gate's owner releasing it is allowed to win.
      w.clearIsland('', 'idle');
      const afterRelease = cls();

      return { afterGate, afterIdle, afterListening, afterThinking, afterRelease };
    });

    expect(result.afterGate).toContain('gate');
    expect(result.afterIdle, 'idle must not clear a pending gate').toContain('gate');
    expect(result.afterListening, 'listening must not hide a gate').toContain('gate');
    expect(result.afterThinking, 'thinking must not hide a gate').toContain('gate');
    expect(result.afterRelease, 'the gate owner may release it').not.toContain('gate');
  });

  test('higher-priority states still escalate over lower ones', async ({
    bootedPage: page,
  }) => {
    const result = await page.evaluate(() => {
      const w = window as any;
      const cls = () => document.getElementById('island')!.className;
      const seen: Record<string, string> = {};

      w.clearIsland('', 'idle');
      w.setIsland('listening', 'listening');
      seen.listening = cls();
      w.setIsland('thinking', 'thinking');
      seen.thinking = cls(); // thinking outranks listening
      w.setIsland('speaking', '');
      seen.speaking = cls(); // speaking outranks thinking
      w.setIsland('gate', '1 waiting');
      seen.gate = cls(); // gate outranks speaking
      w.clearIsland('', 'idle');
      return seen;
    });

    expect(result.listening).toContain('listening');
    expect(result.thinking).toContain('thinking');
    expect(result.speaking).toContain('speaking');
    expect(result.gate).toContain('gate');
  });

  test('locked outranks everything, including a gate', async ({ bootedPage: page }) => {
    const result = await page.evaluate(() => {
      const w = window as any;
      const cls = () => document.getElementById('island')!.className;

      w.clearIsland('', 'idle');
      w.setIsland('gate', '1 waiting');
      w.setIsland('locked', 'locked');
      const afterLock = cls();
      w.setIsland('gate', '1 waiting');
      const gateAfterLock = cls(); // must NOT unlock the indicator

      w.clearIsland('', 'idle');
      return { afterLock, gateAfterLock };
    });

    expect(result.afterLock).toContain('locked');
    expect(result.gateAfterLock, 'a gate must not override the lock state').toContain(
      'locked',
    );
  });
});
