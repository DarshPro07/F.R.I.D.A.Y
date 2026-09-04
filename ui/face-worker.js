/**
 * face-worker.js -- recognition, off the main thread.
 *
 * On this machine tf.js spends 12 to 33 seconds compiling WebGL shaders the
 * first time it runs the detector and the descriptor net. On the main thread
 * that is a frozen page and Chrome offering to kill it. Here it is just a
 * worker being busy: the boot screen keeps animating and the window stays
 * alive, which is the whole point of this file.
 *
 * Steady state on the same GPU is ~60ms an inference, so once this thread is
 * warm the gate is fast. Frames arrive as ImageBitmap (transferable, no copy).
 *
 * Protocol:
 *   { type: "load",  models }            -> { type: "ready" } | { type: "error" }
 *   { type: "frame", bitmap, want }      -> { type: "face", box, descriptor } | { type: "none" }
 * Progress arrives as { type: "stage", stage }.
 */

let faceapi = null;
let canvas = null;
let ctx = null;

function say(msg) { self.postMessage(msg); }

async function load(lib, models) {
  say({ type: "stage", stage: "face library" });
  faceapi = await import(lib);

  // face-api decides what it is running inside by looking for `window` and
  // `document`. A worker has neither, so it throws "environment is not
  // defined" before it does anything. Hand it an environment built from the
  // worker equivalents: OffscreenCanvas is a Canvas, ImageBitmap is an Image,
  // and fetch is already here.
  faceapi.env.setEnv({
    Canvas: OffscreenCanvas,
    CanvasRenderingContext2D: OffscreenCanvasRenderingContext2D,
    Image: ImageBitmap,
    ImageData: ImageData,
    // Nothing may match this. face-api tests `input instanceof env.Video`
    // before the canvas branch, so naming a real class here made every
    // OffscreenCanvas look like a video and it read videoWidth: undefined.
    Video: class NoVideoInAWorker {},
    createCanvasElement: () => new OffscreenCanvas(1, 1),
    createImageElement: () => { throw new Error("no <img> in a worker"); },
    fetch: self.fetch.bind(self),
    readFile: () => { throw new Error("no filesystem in a worker"); },
  });

  for (const [label, net] of [
    ["detector", faceapi.nets.tinyFaceDetector],
    ["landmarks", faceapi.nets.faceLandmark68TinyNet],
    ["recognition", faceapi.nets.faceRecognitionNet],
  ]) {
    say({ type: "stage", stage: "model: " + label });
    await net.loadFromUri(models);
  }

  // Compile the shaders now, here, where a long block costs nothing. A blank
  // frame only exercises the detector, so each net is warmed directly.
  say({ type: "stage", stage: "warming up" });
  const grey = (w, h) => {
    const c = new OffscreenCanvas(w, h);
    const x = c.getContext("2d");
    x.fillStyle = "#6b6b6b"; x.fillRect(0, 0, w, h);
    return c;
  };
  try {
    await faceapi.detectSingleFace(grey(320, 240), opts());
    await faceapi.nets.faceLandmark68TinyNet.detectLandmarks(grey(112, 112));
    await faceapi.nets.faceRecognitionNet.computeFaceDescriptor(grey(150, 150));
  } catch (e) { /* best effort: a cold shader only costs a slow first frame */ }

  say({ type: "ready", backend: faceapi.tf.getBackend() });
}

function opts() {
  return new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.5 });
}

async function onFrame(bitmap, want) {
  if (!faceapi) { bitmap.close(); return say({ type: "none", reason: "not loaded" }); }
  if (!canvas || canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    ctx = canvas.getContext("2d", { willReadFrequently: true });
  }
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();

  try {
    // `want === "descriptor"` is the identity check; presence alone skips the
    // expensive net, which is what keeps the post-unlock watch cheap.
    const det = want === "descriptor"
      ? await faceapi.detectSingleFace(canvas, opts()).withFaceLandmarks(true).withFaceDescriptor()
      : await faceapi.detectSingleFace(canvas, opts());
    if (!det) return say({ type: "none" });
    const d = want === "descriptor" ? det.detection : det;
    const b = d.box;
    say({
      type: "face",
      box: { x: b.x, y: b.y, w: b.width, h: b.height, score: d.score },
      descriptor: det.descriptor ? Array.from(det.descriptor) : null,
    });
  } catch (e) {
    say({ type: "error", error: String((e && e.message) || e).slice(0, 200) });
  }
}

self.onmessage = async (e) => {
  const m = e.data || {};
  if (m.type === "load") {
    try { await load(m.lib, m.models); }
    catch (err) { say({ type: "error", error: String((err && err.message) || err).slice(0, 200) }); }
  } else if (m.type === "frame") {
    await onFrame(m.bitmap, m.want);
  }
};
