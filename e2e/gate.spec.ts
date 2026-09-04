import { test as base, expect } from '@playwright/test';

/**
 * THE FACE GATE, WITH THE GATE ON.
 *
 * Every other spec runs with FRIDAY_FACE_GATE=0 because a test run cannot
 * present a face. This file is the exception and the important one: it starts
 * a SECOND server with the gate ENABLED and proves the server-side lock holds
 * for a client that never authenticated.
 *
 * That matters because the gate is the product's central security claim
 * (PRODUCT.md: "Enforced server-side, so it holds even if the page is
 * bypassed"). A suite that only ever tests with the gate off would never
 * notice it regressing.
 */

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';

const HOST = '127.0.0.1';
const PORT = process.env.FRIDAY_GATE_PORT ?? '8782';
const BASE = `http://${HOST}:${PORT}`;
const PYTHON = process.env.FRIDAY_PYTHON ?? '.venv\\Scripts\\python.exe';

let server: ChildProcessWithoutNullStreams | undefined;

async function waitForHealth(url: string, timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`gated server did not answer ${url} within ${timeoutMs}ms`);
}

const test = base.extend({});

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  server = spawn(PYTHON, ['scripts/run_ui.py', '--no-browser'], {
    env: {
      ...process.env,
      FRIDAY_UI_HOST: HOST,
      FRIDAY_UI_PORT: PORT,
      FRIDAY_FACE_GATE: '1', // the point of this file
      ADA_DB: process.env.ADA_DB ?? 'data/e2e-ada.sqlite3',
      // Point the gate at throwaway credential files so the developer's real
      // enrolment and PIN are never read or written by a test.
      FRIDAY_OWNER_FACE: 'data/e2e-owner-face.json',
      FRIDAY_OWNER_PIN: 'data/e2e-owner-pin.json',
      FRIDAY_ACCESS_LOG: 'data/e2e-access-log.jsonl',
    },
    shell: false,
  }) as ChildProcessWithoutNullStreams;

  await waitForHealth(`${BASE}/health`);
});

test.afterAll(async () => {
  server?.kill();
});

test.describe('face gate — server-side enforcement', () => {
  test('/health and the page itself stay open while locked', async ({ request }) => {
    // The lock screen has to be reachable; the camera runs somewhere.
    expect((await request.get(`${BASE}/health`)).status()).toBe(200);
    expect((await request.get(`${BASE}/`)).status()).toBe(200);
  });

  test('every /api/* route answers 423 without a session', async ({ request }) => {
    const guarded = [
      '/api/state',
      '/api/memory_snapshot',
      '/api/vault',
      '/api/graph',
      '/api/doctor',
      '/api/deck',
      '/api/gate',
      '/api/org',
    ];

    for (const path of guarded) {
      const res = await request.get(`${BASE}${path}`);
      expect(res.status(), `${path} must be locked`).toBe(423);
      const body = await res.json();
      expect(body.locked).toBe(true);
    }
  });

  test('POST routes are locked too, not only GETs', async ({ request }) => {
    for (const path of ['/api/ask', '/api/objective', '/api/browser/open', '/api/deck/run']) {
      const res = await request.post(`${BASE}${path}`, { data: { text: 'x', url: 'x', id: 'x' } });
      expect(res.status(), `${path} must be locked`).toBe(423);
    }
  });

  test('the auth surface stays reachable so a face can be presented', async ({ request }) => {
    const res = await request.get(`${BASE}/api/auth/status`);
    expect(res.status()).toBe(200);
    const status = await res.json();
    expect(status.gate).toBe(true);
    expect(status.locked).toBe(true);
  });

  test('a malformed descriptor is refused rather than accepted', async ({ request }) => {
    // access.verify() requires exactly 128 finite floats.
    const res = await request.post(`${BASE}/api/auth/verify`, {
      data: { descriptor: [1, 2, 3] },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(false);
    expect(body.error).toContain('bad descriptor');
  });

  test('a forged session cookie does not unlock anything', async ({ request }) => {
    const res = await request.get(`${BASE}/api/state`, {
      headers: { Cookie: 'friday_session=not-a-real-token' },
    });
    expect(res.status()).toBe(423);
  });
});
