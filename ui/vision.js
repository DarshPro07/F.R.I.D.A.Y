
const FACEAPI = "/ui/vendor_face-api.esm.js";              
const FACE_MODELS = "/ui/models";

class CameraTimeout extends Error {
  constructor(msg) { super(msg); this.name = "CameraTimeout"; }
}

function withTimeout(promise, ms, what) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new CameraTimeout(what)), ms); }),
  ]);
}

/** Wait for real pixels. A stream that never paints is the same as no camera. */
function firstFrame(video, ms) {
  if (video.videoWidth) return Promise.resolve(true);
  return new Promise((resolve) => {
    const done = () => { clearTimeout(t); video.removeEventListener("loadeddata", done); resolve(!!video.videoWidth); };
    const t = setTimeout(done, ms);
    video.addEventListener("loadeddata", done);
  });
}

/**
 * Open the camera, or fail in bounded time.
 *
 * getUserMedia is documented to reject when the device is busy, and on this
 * machine it sometimes just never settles instead -- which used to hang the
 * whole unlock and leave a black rectangle with no explanation. Every step
 * here has a deadline, so "the camera is not available" is an answer Friday
 * can give in seconds rather than a state she gets stuck in.
 */
export async function openCamera(video, ms = 6000) {
  if (video.srcObject) {
    if (video.paused) await video.play().catch(() => {});
    return video.srcObject;
  }
  const stream = await withTimeout(
    navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false }),
    ms, "the camera did not respond");
  video.srcObject = stream;
  await withTimeout(video.play(), 3000, "the camera did not start").catch((e) => {
    if (!(e instanceof CameraTimeout)) throw e;                 // a real play() error still matters
  });
  if (!await firstFrame(video, 3000)) {
    stream.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
    throw new CameraTimeout("the camera gave no picture");
  }
  return stream;
}

/**
 * The recognition engine, living on a worker thread.
 *
 * tf.js spends 12-33 seconds compiling WebGL shaders the first time it runs
 * these nets on this GPU. On the main thread that is a frozen window and
 * Chrome offering to kill the page -- which is exactly what it used to do.
 * Here it is a worker being busy while the boot screen keeps animating.
 *
 * There is no main-thread fallback on purpose: falling back would restore the
 * freeze, quietly. If the worker cannot start, the gate says so and offers the
 * PIN, which is the honest outcome.
 */
class FaceEngine {
  constructor() {
    this.worker = null;
    this.readyPromise = null;
    this.pending = null;       // one frame in flight at a time
    this.onStage = null;
  }

  start(onStage) {
    this.onStage = onStage;
    if (this.readyPromise) return this.readyPromise;
    this.readyPromise = new Promise((resolve, reject) => {
      let worker;
      try {
        worker = new Worker("/ui/face-worker.js", { type: "module" });
      } catch (e) { return reject(new Error("this browser cannot run module workers")); }
      this.worker = worker;
      const failed = setTimeout(() => reject(new Error("the recognition worker did not start")), 120000);

      worker.onerror = (e) => { clearTimeout(failed); reject(new Error(e.message || "worker failed")); };
      worker.onmessage = (e) => {
        const m = e.data || {};
        if (m.type === "stage") { this.onStage && this.onStage(m.stage); return; }
        if (m.type === "ready") { clearTimeout(failed); resolve(this); return; }
        if (m.type === "face" || m.type === "none") {
          const p = this.pending; this.pending = null;
          p && p.resolve(m.type === "face" ? m : null);
          return;
        }
        if (m.type === "error") {
          const p = this.pending; this.pending = null;
          if (p) p.resolve(null); else { clearTimeout(failed); reject(new Error(m.error)); }
        }
      };
      worker.postMessage({ type: "load", lib: FACEAPI, models: FACE_MODELS });
    });
    return this.readyPromise;
  }

  /** One frame in, one answer out. `want`: "descriptor" (identity) or "presence". */
  async look(source, want) {
    if (!this.worker || this.pending) return null;         // never queue: frames are cheap
    let bitmap;
    try { bitmap = await createImageBitmap(source); }
    catch (e) { return null; }
    return new Promise((resolve) => {
      this.pending = { resolve };
      try { this.worker.postMessage({ type: "frame", bitmap, want }, [bitmap]); }
      catch (e) { this.pending = null; bitmap.close(); resolve(null); }
      setTimeout(() => { if (this.pending && this.pending.resolve === resolve) { this.pending = null; resolve(null); } }, 8000);
    });
  }
}

export const faceEngine = new FaceEngine();

/** Load and warm the recognition worker. Resolves when it can answer. */
export async function loadFaceApi(onStage) {
  return faceEngine.start(onStage);
}

/**
 * Watches a video element for the owner's face.
 *
 * `light` means presence only -- is anyone there -- which skips the expensive
 * descriptor net. That is what the post-unlock watch uses; identity was already
 * proved at the door.
 */
export class FaceGate {
  constructor(video, overlay, cb) {
    this.video = video; this.overlay = overlay; this.cb = cb;
    this.running = false; this.timer = 0; this.every = 650; this.light = false;
  }

  async start() {
    await faceEngine.start(this.cb && this.cb.onStage);
    this.running = true;
    this.tick();
  }

  stop() { this.running = false; clearTimeout(this.timer); }

  async tick() {
    if (!this.running) return;
    if (document.hidden || this.video.readyState < 2 || !this.video.videoWidth) {
      this.timer = setTimeout(() => this.tick(), this.every);
      return;
    }
    try {
      const det = await faceEngine.look(this.video, this.light ? "presence" : "descriptor");
      if (!this.running) return;
      if (det) this.cb.onFace && this.cb.onFace(det.descriptor, det.box);
      else this.cb.onNoFace && this.cb.onNoFace();
    } catch (e) {
      this.cb.onError && this.cb.onError(e);
    }
    this.timer = setTimeout(() => this.tick(), this.every);
  }
}

/** A descriptor for one still image -- used when enrolling from photos. */
export async function describeImage(source) {
  await faceEngine.start();
  const det = await faceEngine.look(source, "descriptor");
  return det && det.descriptor ? det.descriptor : null;
}

/* draw boxes onto the capture overlay; the video is mirrored, so mirror x */
export function drawBoxes(overlay, video, items, style) {
  const ctx = overlay.getContext("2d"); if (!ctx) return;
  const W = overlay.width, H = overlay.height, sx = W / (video.videoWidth || 640), sy = H / (video.videoHeight || 480);
  ctx.clearRect(0, 0, W, H);
  for (const it of items) {
    const b = it.box; if (!b) continue;
    const x = W - (b.x + b.w) * sx, y = b.y * sy, w = b.w * sx, h = b.h * sy;
    ctx.strokeStyle = it.color || style.color; ctx.lineWidth = it.width || 1.5;
    ctx.strokeRect(x, y, w, h);
    // corner ticks read as a capture reticle
    const t = Math.min(10, w / 4);
    ctx.lineWidth = 2.5;
    for (const [cx, cy, dx, dy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) {
      ctx.beginPath(); ctx.moveTo(cx, cy + dy * t); ctx.lineTo(cx, cy); ctx.lineTo(cx + dx * t, cy); ctx.stroke();
    }
    if (it.label) {
      ctx.font = "10px 'IBM Plex Mono', monospace"; ctx.fillStyle = it.color || style.color;
      ctx.fillText(it.label.toUpperCase() + (it.score ? " " + Math.round(it.score * 100) + "%" : ""), x + 3, Math.max(10, y - 4));
    }
  }
}

/* short tones; no audio assets, no library */
let ac = null;
function tone(freq, ms, gain = 0.06, type = "sine", when = 0) {
  try {
    ac = ac || new (window.AudioContext || window.webkitAudioContext)();
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.value = 0; g.gain.setValueAtTime(0, ac.currentTime + when);
    g.gain.linearRampToValueAtTime(gain, ac.currentTime + when + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + when + ms / 1000);
    o.connect(g); g.connect(ac.destination);
    o.start(ac.currentTime + when); o.stop(ac.currentTime + when + ms / 1000 + 0.02);
  } catch (e) {}
}
export const cues = {
  unlock() { tone(523, 120); tone(784, 160, 0.06, "sine", 0.12); },
  lock() { tone(440, 140); tone(330, 200, 0.05, "sine", 0.14); },
  pinch() { tone(1200, 45, 0.04, "square"); },
  release() { tone(600, 45, 0.03, "square"); },
  seen() { tone(880, 70, 0.03); },
  tick() { tone(300, 25, 0.015, "triangle"); },
};
