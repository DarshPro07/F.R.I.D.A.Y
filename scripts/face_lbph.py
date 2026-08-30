"""
The owner's original OpenCV/LBPH recogniser, upgraded -- and headless.

What changed from the original two scripts:
  * BUG FIXED: the face crop was `gray[y:y+w, x:x+h]` (width and height
    swapped). It is `gray[y:y+h, x:x+w]`. The old model was trained on
    mis-cropped faces, which is one reason its confidence drifted.
  * No windows. `cv2.imshow` is gone; verify mode returns a result, it does
    not open anything (the control room rule: no Python windows).
  * Training data expands properly: `train <dir>` walks `dir/<label>/*.jpg`,
    keeps only images with exactly one face, adds mirrored + brightness
    variants so a few photos train like many, and writes labels.json beside
    the model so names are not hard-coded in the code any more.
  * A confidence threshold you can set (`--max-confidence`, LBPH: lower is
    better; 60 was the old cut-off) and a per-image report.

Needs `opencv-contrib-python-headless` (the `cv2.face` module is contrib-only
and the live venv has plain opencv). Install into a separate venv or add it
deliberately; this script refuses politely rather than crashing.

    python scripts/face_lbph.py train data/faces --out data/face_lbph.yml
    python scripts/face_lbph.py verify photo.jpg --model data/face_lbph.yml
    python scripts/face_lbph.py check trainingData.yml      # inspect an old model

This is a SECOND opinion. The face gate's primary recogniser is the descriptor
model (scripts/enrol_face.py); LBPH is weaker on lighting and pose and should
not gate anything on its own.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cv2():
    try:
        import cv2  # noqa: WPS433
    except ImportError:
        raise SystemExit("opencv is not installed; pip install opencv-contrib-python-headless")
    if not hasattr(cv2, "face"):
        raise SystemExit("this opencv has no cv2.face (contrib); install opencv-contrib-python-headless")
    return cv2


def detect_faces(cv2, img, scale=1.32, neighbours=5):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return cascade.detectMultiScale(gray, scaleFactor=scale, minNeighbors=neighbours), gray


def crop(gray, face, size=200):
    x, y, w, h = face
    roi = gray[y:y + h, x:x + w]                 # the fixed crop
    return roi if size is None else __import__("cv2").resize(roi, (size, size))


def augment(cv2, roi):
    """Mirror + two brightness variants: cheap, honest expansion of few photos."""
    out = [roi, cv2.flip(roi, 1)]
    for alpha in (0.8, 1.2):
        out.append(cv2.convertScaleAbs(roi, alpha=alpha, beta=0))
    return out


def load_training(cv2, root: Path):
    faces, ids, names, report = [], [], {}, []
    for label_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        label = len(names)
        names[label] = label_dir.name
        for img_path in sorted(label_dir.iterdir()):
            if img_path.name.startswith(".") or not img_path.is_file():
                continue
            img = cv2.imread(str(img_path))
            if img is None:
                report.append((img_path.name, "could not read"))
                continue
            rects, gray = detect_faces(cv2, img)
            if len(rects) != 1:
                report.append((img_path.name, "%d faces, need exactly 1" % len(rects)))
                continue
            for variant in augment(cv2, crop(gray, rects[0])):
                faces.append(variant)
                ids.append(label)
            report.append((img_path.name, "ok (x4 with augmentation)"))
    return faces, ids, names, report


def cmd_train(args):
    cv2 = _cv2()
    import numpy as np
    faces, ids, names, report = load_training(cv2, args.folder)
    for name, why in report:
        print("  %s %-40s %s" % ("+" if why.startswith("ok") else "x", name[:40], why))
    if not faces:
        raise SystemExit("nothing to train on")
    rec = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
    rec.train(faces, np.array(ids))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rec.write(str(args.out))
    args.out.with_suffix(".labels.json").write_text(json.dumps(names, indent=2), encoding="utf-8")
    print("\ntrained %d samples for %s -> %s" % (len(faces), names, args.out))


def cmd_verify(args):
    cv2 = _cv2()
    rec = cv2.face.LBPHFaceRecognizer_create()
    rec.read(str(args.model))
    labels_path = args.model.with_suffix(".labels.json")
    names = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    img = cv2.imread(str(args.image))
    if img is None:
        raise SystemExit("could not read %s" % args.image)
    rects, gray = detect_faces(cv2, img)
    if len(rects) == 0:
        print(json.dumps({"ok": False, "reason": "no face"}))
        return
    results = []
    for face in rects:
        label, confidence = rec.predict(crop(gray, face))
        results.append({"label": names.get(str(label), label), "confidence": round(float(confidence), 1),
                        "match": confidence <= args.max_confidence})
    print(json.dumps({"ok": any(r["match"] for r in results), "faces": results,
                      "max_confidence": args.max_confidence}))


def cmd_check(args):
    cv2 = _cv2()
    rec = cv2.face.LBPHFaceRecognizer_create()
    rec.read(str(args.model))
    hist = rec.getHistograms()
    print(json.dumps({"model": str(args.model), "samples": len(hist), "radius": rec.getRadius(),
                      "neighbors": rec.getNeighbors(), "grid": [rec.getGridX(), rec.getGridY()],
                      "labels": sorted(set(int(x) for x in rec.getLabels().flatten()))}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train"); t.add_argument("folder", type=Path); t.add_argument("--out", type=Path, default=Path("data/face_lbph.yml"))
    v = sub.add_parser("verify"); v.add_argument("image", type=Path); v.add_argument("--model", type=Path, default=Path("data/face_lbph.yml")); v.add_argument("--max-confidence", type=float, default=60.0)
    c = sub.add_parser("check"); c.add_argument("model", type=Path)
    args = ap.parse_args()
    {"train": cmd_train, "verify": cmd_verify, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
