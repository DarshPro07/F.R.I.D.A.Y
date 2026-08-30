
const FACEAPI = "/ui/vendor_face-api.esm.js";              
const FACE_MODELS = "/ui/models";

let faceapi = null;
export async function loadFaceApi(onStage) {
  if (faceapi) return faceapi;
  onStage && onStage("face library");
  const m = await import(FACEAPI);
  onStage && onStage("face models");
  await Promise.all([
    m.nets.tinyFaceDetector.loadFromUri(FACE_MODELS),
    m.nets.faceLandmark68TinyNet.loadFromUri(FACE_MODELS),
    m.nets.faceRecognitionNet.loadFromUri(FACE_MODELS),
  ]);
  faceapi = m;
  return m;
}

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

export class FaceGate {
  constructor(video, overlay, cb) { this.video = video; this.overlay = overlay; this.cb = cb; this.running = false; this.timer = 0; this.every = 650; this.light = false; }
  async start() {
    await loadFaceApi(this.cb.onStage);
    this.running = true;
    this.tick();
  }
  stop() { this.running = false; clearTimeout(this.timer); }
  async tick() {
    if (!this.running) return;
    if (document.hidden) { this.timer = setTimeout(() => this.tick(), this.every); return; }   // nobody is looking
    try {
      if (this.video.readyState >= 2 && this.video.videoWidth) {
        const opts = new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.5 });
        // light = presence only (is a face there?); the descriptor is computed only when identity matters
        const det = this.light ? await faceapi.detectSingleFace(this.video, opts)
                               : await faceapi.detectSingleFace(this.video, opts).withFaceLandmarks(true).withFaceDescriptor();
        if (det) {
          const d = this.light ? det : det.detection, b = d.box;
          this.cb.onFace(this.light ? null : Array.from(det.descriptor), { x: b.x, y: b.y, w: b.width, h: b.height, score: d.score });
        } else this.cb.onNoFace && this.cb.onNoFace();
      }
    } catch (e) { this.cb.onError && this.cb.onError(e); }
    this.timer = setTimeout(() => this.tick(), this.every);
  }
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
