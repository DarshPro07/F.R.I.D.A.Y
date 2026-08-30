"""
Enrol the owner's face from a folder of photos -- headless, no windows.

This is the training step for the face gate. It uses the SAME recognition
model the live gate uses in the browser (face-api's ResNet descriptors), run
in a headless Chromium via Playwright, so the descriptors it writes are the
descriptors the camera will produce at unlock time. Training on one model and
recognising with another is how face gates become unreliable; this avoids it.

    .venv\\Scripts\\python.exe scripts\\enrol_face.py C:\\photos\\me
    .venv\\Scripts\\python.exe scripts\\enrol_face.py C:\\photos\\me --label darsh --replace
    .venv\\Scripts\\python.exe scripts\\enrol_face.py C:\\photos\\me --dry-run      # report only

Every photo is checked: exactly one face, detector score, descriptor spread
against the other photos (an outlier is usually a photo of someone else, a
reflection, or a poster in the background) and is rejected with the reason.
Descriptors (128 numbers per photo) go to data/owner_face.json; the photos
themselves are never copied anywhere. Needs network once for the model files.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FACEAPI = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/dist/face-api.esm.js"
MODELS = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/model"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_SPREAD = 0.62          # a photo further than this from the others is not the same person
MIN_SCORE = 0.3            # detector confidence; webcam bursts are soft, the spread check does the real vetting

PAGE = """<!doctype html><html><body><script type="module">
import * as faceapi from "%s";
await Promise.all([faceapi.nets.tinyFaceDetector.loadFromUri("%s"),
                   faceapi.nets.faceLandmark68Net.loadFromUri("%s"),
                   faceapi.nets.faceRecognitionNet.loadFromUri("%s")]);
window.describe = async (dataUrl) => {
  const img = await faceapi.fetchImage(dataUrl);
  // tiny detector at 416: the SSD model misses backlit webcam frames entirely; the cross-photo
  // consistency check below is what guards against a false face, so the threshold can be lenient.
  const all = await faceapi.detectAllFaces(img, new faceapi.TinyFaceDetectorOptions({ inputSize: 416, scoreThreshold: 0.3 }))
                           .withFaceLandmarks().withFaceDescriptors();
  return { faces: all.length, score: all[0] ? all[0].detection.score : 0,
           descriptor: all[0] ? Array.from(all[0].descriptor) : null,
           width: img.width, height: img.height,
           box: all[0] ? all[0].detection.box.width : 0 };
};
window.ready = true;
</script></body></html>""" % (FACEAPI, MODELS, MODELS, MODELS)


def _data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(path.read_bytes()).decode())


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def describe_folder(folder: Path):
    from playwright.sync_api import sync_playwright
    photos, seen = [], set()
    for p in sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTS):
        digest = hashlib.md5(p.read_bytes()).hexdigest()      # copies of a photo teach nothing new
        if digest not in seen:
            seen.add(digest)
            photos.append(p)
    if not photos:
        raise SystemExit("no photos under %s" % folder)
    total = sum(1 for q in folder.rglob("*") if q.suffix.lower() in EXTS)
    print("%d files, %d unique photos" % (total, len(photos)))
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(PAGE)
        page.wait_for_function("window.ready === true", timeout=120_000)
        for p in photos:
            try:
                r = page.evaluate("(u) => window.describe(u)", _data_url(p))
            except Exception as exc:  # noqa: BLE001
                r = {"faces": 0, "error": str(exc)[:80]}
            r["photo"] = str(p)
            results.append(r)
        browser.close()
    return results


def audit(results):
    """Keep photos with exactly one confident face that agrees with the rest."""
    good = [r for r in results if r.get("faces") == 1 and r.get("score", 0) >= MIN_SCORE and r.get("descriptor")]
    for r in results:
        if r.get("faces", 0) == 0:
            r["reject"] = "no face found"
        elif r.get("faces", 0) > 1:
            r["reject"] = "%d faces; use photos with only you" % r["faces"]
        elif r.get("score", 0) < MIN_SCORE:
            r["reject"] = "low detector confidence %.2f" % r["score"]
        elif r.get("box", 0) and r["box"] < 80:
            r["reject"] = "face too small (%dpx); move closer" % r["box"]
    good = [r for r in good if "reject" not in r]
    # outliers: mean distance to the other kept photos
    if len(good) >= 3:
        for r in good:
            others = [g for g in good if g is not r]
            r["spread"] = sum(_dist(r["descriptor"], g["descriptor"]) for g in others) / len(others)
        for r in good:
            if r["spread"] > MAX_SPREAD:
                r["reject"] = "does not match the other photos (spread %.2f)" % r["spread"]
        good = [r for r in good if "reject" not in r]
    return good


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--label", default="owner")
    ap.add_argument("--replace", action="store_true", help="discard previously enrolled descriptors first")
    ap.add_argument("--dry-run", action="store_true", help="audit the photos, write nothing")
    ap.add_argument("--max", type=int, default=40, help="keep at most this many photos, spread evenly through the set")
    args = ap.parse_args()

    results = describe_folder(args.folder)
    good = audit(results)
    for r in results:
        name = Path(r["photo"]).name
        if r.get("reject"):
            print("  x %-40s %s" % (name[:40], r["reject"]))
        else:
            print("  + %-40s score %.2f%s" % (name[:40], r.get("score", 0),
                                              ("  spread %.2f" % r["spread"]) if "spread" in r else ""))
    print("\n%d of %d photos usable" % (len(good), len(results)))
    if args.dry_run or not good:
        return
    if len(good) > args.max:                       # a burst of near-identical frames teaches nothing past a point
        step = len(good) / args.max
        good = [good[int(i * step)] for i in range(args.max)]
        print("keeping %d spread evenly through the usable photos" % len(good))
    from friday import access
    if args.replace and access.OWNER_PATH.exists():
        access.OWNER_PATH.unlink()
        print("previous enrolment discarded")
    for r in good:
        access.enrol(r["descriptor"], label=args.label)
    print("enrolled %d descriptor(s) for %r -> %s" % (len(good), args.label, access.OWNER_PATH))
    print("threshold %.2f; live matches must land within it." % access.THRESHOLD)


if __name__ == "__main__":
    main()
