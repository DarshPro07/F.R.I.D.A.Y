
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";

const HOME_POSITION = new THREE.Vector3(0, 0.5, 5.5);
const MIN_DISTANCE = 0.6;
const MAX_DISTANCE = 40;

export const PALETTE = {
  bright: 0xffaa30, mid: 0xdd7700, dim: 0x884400, faint: 0x553300, hot: 0xffcc66, alert: 0xff5533,
};
// "blue" is the verified state: same lightness ladder, cool hue.
export const PALETTES = {
  amber: PALETTE,
  blue: { bright: 0x35aaff, mid: 0x1177dd, dim: 0x0a4a90, faint: 0x083366, hot: 0x8fd4ff, alert: 0xff5533 },
};

export function createOrbScene(container, opts = {}) {
  const reduce = !!opts.reduce;
  const q = opts.quality === "low" ? 0.5 : 1;   // halves sprite/dust counts on weak machines
  const width = container.clientWidth || 800;
  const height = container.clientHeight || 600;

  // ——— SCENE ———
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 500);
  camera.position.copy(HOME_POSITION);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  let pixelRatio = Math.min(window.devicePixelRatio, opts.quality === "low" ? 1 : 1.25);
  const RATIO_FLOOR = 1;                      // never render the core below 1:1 -- it reads as blur, not as speed
  renderer.setPixelRatio(pixelRatio);
  // ponytail: adaptive resolution, not adaptive geometry. Slow frames -> render smaller (bloom/chromatic scale with it);
  // fast frames -> step back up. Same look, cost follows the machine.
  let frameEma = 16, adaptAt = 0, chromaticOn = true;
  function adapt(now) {
    if (now - adaptAt < 1500) return;
    const ceiling = Math.min(window.devicePixelRatio, opts.quality === "low" ? 1 : 1.25);
    if (frameEma > 34 && pixelRatio > RATIO_FLOOR) { pixelRatio = Math.max(RATIO_FLOOR, pixelRatio - 0.25); renderer.setPixelRatio(pixelRatio); composer.setPixelRatio(pixelRatio); adaptAt = now; }
    else if (frameEma > 40 && chromaticOn) { chromaticOn = false; chromaticPass.enabled = false; adaptAt = now; }   // the last thing to go
    else if (frameEma < 16 && (pixelRatio < ceiling || !chromaticOn)) {
      if (!chromaticOn) { chromaticOn = true; chromaticPass.enabled = true; }
      else { pixelRatio = Math.min(ceiling, pixelRatio + 0.25); renderer.setPixelRatio(pixelRatio); composer.setPixelRatio(pixelRatio); }
      adaptAt = now;
    }
  }
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.8;
  container.appendChild(renderer.domElement);

  // ——— POST PROCESSING ———
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(width / 2, height / 2), 1.8, 0.4, 0.2);   // half-res bloom: same look, a quarter of the fill
  composer.addPass(bloom);

  const chromaticShader = {
    uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uIntensity: { value: 0.003 } },
    vertexShader: `
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `
      uniform sampler2D tDiffuse; uniform float uTime; uniform float uIntensity; varying vec2 vUv;
      void main() {
        vec2 dir = vUv - vec2(0.5); float d = length(dir); float offset = uIntensity * d;
        float flicker = 1.0 + 0.02 * sin(uTime * 30.0) * sin(uTime * 7.3);
        vec4 cr = texture2D(tDiffuse, vUv + dir * offset);
        vec4 cg = texture2D(tDiffuse, vUv);
        vec4 cb = texture2D(tDiffuse, vUv - dir * offset * 0.5);
        gl_FragColor = vec4(cr.r, cg.g * 1.05, cb.b * 0.6, 1.0) * flicker;
        gl_FragColor.rgb = mix(gl_FragColor.rgb, gl_FragColor.rgb * vec3(1.15, 0.85, 0.55), 0.3);
      }`,
  };
  const chromaticPass = new ShaderPass(chromaticShader);
  composer.addPass(chromaticPass);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.04;
  controls.minDistance = MIN_DISTANCE;
  controls.maxDistance = MAX_DISTANCE;
  controls.zoomSpeed = 1.4;
  controls.enablePan = false;

  const { bright: C_BRIGHT, mid: C_MID, dim: C_DIM, faint: C_FAINT, hot: C_HOT, alert: C_ALERT } = PALETTE;
  const KEY_OF = { [C_BRIGHT]: "bright", [C_MID]: "mid", [C_DIM]: "dim", [C_FAINT]: "faint", [C_HOT]: "hot" };
  const tinted = [];                       // [material, paletteKey] for setPalette()
  function reg(mat, color) { const k = KEY_OF[color]; if (k) tinted.push([mat, k]); return mat; }

  const orbGroup = new THREE.Group();
  scene.add(orbGroup);

  function lineMat(color, opacity = 1) {
    return reg(new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false }), color);
  }
  function latRing(radius, lat, segs = 120) {
    const r = radius * Math.cos(lat), y = radius * Math.sin(lat), pts = [];
    for (let i = 0; i <= segs; i++) { const a = (i / segs) * Math.PI * 2; pts.push(new THREE.Vector3(r * Math.cos(a), y, r * Math.sin(a))); }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }
  function meridian(radius, lon, segs = 120) {
    const pts = [];
    for (let i = 0; i <= segs; i++) {
      const lat = (i / segs) * Math.PI - Math.PI / 2;
      pts.push(new THREE.Vector3(radius * Math.cos(lat) * Math.cos(lon), radius * Math.sin(lat), radius * Math.cos(lat) * Math.sin(lon)));
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }

  // LAYER 1: OUTER SHELL
  const outerShell = new THREE.Group();
  const R1 = 2.0;
  for (let i = -15; i <= 15; i++) {
    const lat = (i / 15) * (Math.PI / 2) * 0.95;
    outerShell.add(new THREE.Line(latRing(R1, lat), lineMat(i % 3 === 0 ? C_MID : C_FAINT, i % 3 === 0 ? 0.5 : 0.12)));
  }
  for (let i = 0; i < 24; i++) {
    const lon = (i / 24) * Math.PI * 2, isMajor = i % 6 === 0;
    outerShell.add(new THREE.Line(meridian(R1, lon), lineMat(isMajor ? C_MID : C_FAINT, isMajor ? 0.6 : 0.1)));
  }
  const CROSS_LINES = 18, CROSS_SPREAD = 0.25;
  for (let i = 0; i < 4; i++) {
    const lon = (i / 4) * Math.PI * 2;
    for (let j = 0; j < CROSS_LINES; j++) {
      const t = (j / (CROSS_LINES - 1)) * 2 - 1, offset = (t * CROSS_SPREAD) / 2, falloff = 1 - Math.abs(t) * 0.7;
      outerShell.add(new THREE.Line(meridian(R1, lon + offset, 200), lineMat(Math.abs(t) < 0.3 ? C_BRIGHT : C_MID, 0.85 * falloff)));
    }
  }
  const EQ_LINES = 20, EQ_SPREAD = 0.35;
  for (let j = 0; j < EQ_LINES; j++) {
    const t = (j / (EQ_LINES - 1)) * 2 - 1, offset = (t * EQ_SPREAD) / 2, falloff = 1 - Math.abs(t) * 0.65;
    outerShell.add(new THREE.Line(latRing(R1, offset, 200), lineMat(Math.abs(t) < 0.3 ? C_BRIGHT : C_MID, 0.8 * falloff)));
  }
  orbGroup.add(outerShell);

  // LAYER 2: GRID PANELS
  const panelGroup = new THREE.Group();
  function createSpherePanel(latCenter, lonCenter, latSpan, lonSpan, radius, divisions = 4) {
    const group = new THREE.Group(), mat = lineMat(C_DIM, 0.25);
    for (let i = 0; i <= divisions; i++) {
      const lat = latCenter - latSpan / 2 + (i / divisions) * latSpan, pts = [];
      for (let j = 0; j <= divisions * 4; j++) {
        const lon = lonCenter - lonSpan / 2 + (j / (divisions * 4)) * lonSpan;
        pts.push(new THREE.Vector3(radius * Math.cos(lat) * Math.cos(lon), radius * Math.sin(lat), radius * Math.cos(lat) * Math.sin(lon)));
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }
    for (let j = 0; j <= divisions; j++) {
      const lon = lonCenter - lonSpan / 2 + (j / divisions) * lonSpan, pts = [];
      for (let i = 0; i <= divisions * 4; i++) {
        const lat = latCenter - latSpan / 2 + (i / (divisions * 4)) * latSpan;
        pts.push(new THREE.Vector3(radius * Math.cos(lat) * Math.cos(lon), radius * Math.sin(lat), radius * Math.cos(lat) * Math.sin(lon)));
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }
    return group;
  }
  for (let i = 0; i < 30; i++) {
    const lat = (Math.random() - 0.5) * Math.PI * 0.8, lon = Math.random() * Math.PI * 2, size = 0.15 + Math.random() * 0.25;
    panelGroup.add(createSpherePanel(lat, lon, size, size, R1 + 0.01, 3 + Math.floor(Math.random() * 3)));
  }
  orbGroup.add(panelGroup);

  // LAYER 3: SECONDARY SHELL
  const shell2 = new THREE.Group();
  const R2 = 2.12;
  for (let i = 0; i < 16; i++) {
    const lat = (Math.random() - 0.5) * Math.PI * 0.85, startLon = Math.random() * Math.PI * 2, arcLen = 0.3 + Math.random() * 1.2, pts = [];
    const r = R2 * Math.cos(lat), y = R2 * Math.sin(lat);
    for (let j = 0; j <= 60; j++) { const a = startLon + (j / 60) * arcLen; pts.push(new THREE.Vector3(r * Math.cos(a), y, r * Math.sin(a))); }
    shell2.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(C_MID, 0.2 + Math.random() * 0.3)));
  }
  for (let i = 0; i < 12; i++) {
    const lon = Math.random() * Math.PI * 2, startLat = (Math.random() - 0.5) * Math.PI * 0.8, arcLen = 0.3 + Math.random() * 0.8, pts = [];
    for (let j = 0; j <= 40; j++) {
      const lat = startLat + (j / 40) * arcLen;
      pts.push(new THREE.Vector3(R2 * Math.cos(lat) * Math.cos(lon), R2 * Math.sin(lat), R2 * Math.cos(lat) * Math.sin(lon)));
    }
    shell2.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(C_DIM, 0.15 + Math.random() * 0.2)));
  }
  orbGroup.add(shell2);

  // LAYER 4: INNER CORE
  const innerCore = new THREE.Group();
  const R3 = 0.9;
  for (let s = 0; s < 8; s++) {
    const pts = [], turns = 3 + Math.random() * 2, phase = (s / 8) * Math.PI * 2;
    for (let i = 0; i <= 300; i++) {
      const t = i / 300, lat = t * Math.PI - Math.PI / 2, lon = t * turns * Math.PI * 2 + phase;
      pts.push(new THREE.Vector3(R3 * Math.cos(lat) * Math.cos(lon), R3 * Math.sin(lat), R3 * Math.cos(lat) * Math.sin(lon)));
    }
    innerCore.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(C_BRIGHT, 0.3 + Math.random() * 0.2)));
  }
  for (let i = -6; i <= 6; i++) innerCore.add(new THREE.Line(latRing(R3, (i / 6) * (Math.PI / 2) * 0.9, 80), lineMat(C_DIM, 0.2)));
  for (let i = 0; i < 12; i++) innerCore.add(new THREE.Line(meridian(R3, (i / 12) * Math.PI * 2, 80), lineMat(C_DIM, 0.15)));
  orbGroup.add(innerCore);

  // LAYER 5: INNERMOST CORE
  const icoWireMat = lineMat(C_HOT, 0.9);
  const icoWire = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.25, 1)), icoWireMat);
  orbGroup.add(icoWire);
  const coreSphereMat = reg(new THREE.MeshBasicMaterial({ color: C_HOT, transparent: true, opacity: 0.15, blending: THREE.AdditiveBlending }), C_HOT);
  const coreSphere = new THREE.Mesh(new THREE.SphereGeometry(0.15, 16, 16), coreSphereMat);
  orbGroup.add(coreSphere);
  const glowSphereMat = reg(new THREE.MeshBasicMaterial({ color: C_MID, transparent: true, opacity: 0.04, blending: THREE.AdditiveBlending }), C_MID);
  const glowSphere = new THREE.Mesh(new THREE.SphereGeometry(0.5, 16, 16), glowSphereMat);
  orbGroup.add(glowSphere);

  // CODE TEXT
  const codeSnippets = [
    "sys.init()", "0xFF3A", "malloc()", ">> SCAN", "void*", "ACK", "SYNC OK", "ptr_ref", "exec()", "hash256", "::bind", "core.0",
    "01101001", "10110100", ">>> RDY", "HEAP 4K", "TCP/SYN", "mutex.lk", "IRQ 0x7", "DMA xfer", "REG EAX", "FAULT 0",
    "kernel.d", "pipe |>", "chmod +x", "fork()", "SIGTERM", "eth0: UP", "AES-256", "RSA 4096", "TLS 1.3", "HTTP/2",
    "latency", "200 OK", "PATCH /", "fn main", "use std", "impl Orb", "async {}", "spawn()", "arc::new", ".unwrap",
  ];
  // ——— TEXT FIELD ———
  // ponytail: one instanced draw per layer instead of ~1,700 Sprites (1,700 draw calls a frame).
  // Every snippet is baked once into an atlas; each instance picks a tile, and the orbit is
  // computed in the vertex shader, so the CPU writes one uniform per layer per frame instead of
  // 1,700 positions. Same snippets, same density, same look.
  const TILE_W = 256, TILE_H = 32, ATLAS_COLS = 8;
  const atlasRows = Math.ceil(codeSnippets.length / ATLAS_COLS);
  const atlasCanvas = document.createElement("canvas");
  atlasCanvas.width = TILE_W * ATLAS_COLS; atlasCanvas.height = TILE_H * atlasRows;
  {
    const a = atlasCanvas.getContext("2d");
    a.font = "bold 14px Courier New"; a.fillStyle = "#fff"; a.textAlign = "center"; a.textBaseline = "middle";
    codeSnippets.forEach((txt, i) => a.fillText(txt, (i % ATLAS_COLS) * TILE_W + TILE_W / 2,
                                                     Math.floor(i / ATLAS_COLS) * TILE_H + TILE_H / 2));
  }
  const atlasTex = new THREE.CanvasTexture(atlasCanvas);
  atlasTex.minFilter = THREE.LinearFilter; atlasTex.magFilter = THREE.LinearFilter;
  atlasTex.colorSpace = THREE.SRGBColorSpace;

  const TEXT_VERT = `
    attribute vec3 aSph;       // phi, r, theta0
    attribute vec2 aSize;      // half-width, half-height in world units
    attribute vec2 aTile;      // atlas uv origin
    attribute float aSpeed;
    attribute float aKind;     // 0 = bright, 1 = mid
    attribute float aAlpha;
    uniform float uPhase, uScale;
    uniform vec2 uTileSize;
    varying vec2 vUv; varying float vKind; varying float vAlpha;
    void main() {
      float theta = aSph.z + aSpeed * uPhase, phi = aSph.x, r = aSph.y;
      vec3 p = vec3(r * sin(phi) * cos(theta), r * cos(phi), r * sin(phi) * sin(theta));
      vec4 mv = modelViewMatrix * vec4(p, 1.0);
      mv.xy += position.xy * aSize * uScale;               // view-aligned billboard
      gl_Position = projectionMatrix * mv;
      vUv = aTile + uv * uTileSize; vKind = aKind; vAlpha = aAlpha;
    }`;
  const TEXT_FRAG = `
    uniform sampler2D uMap; uniform vec3 uBright, uMid;
    varying vec2 vUv; varying float vKind; varying float vAlpha;
    void main() {
      vec4 tx = texture2D(uMap, vUv);
      gl_FragColor = vec4(mix(uBright, uMid, vKind) * tx.rgb, tx.a * vAlpha);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`;

  const textLayers = [];
  function textField(count, sizeFn, rFn, speedScale, speedMult) {
    const geo = new THREE.InstancedBufferGeometry();
    const plane = new THREE.PlaneGeometry(2, 2);            // position.xy in [-1,1] -> aSize is a half-extent
    geo.index = plane.index;
    geo.attributes.position = plane.attributes.position;
    geo.attributes.uv = plane.attributes.uv;
    geo.instanceCount = count;
    const sph = new Float32Array(count * 3), size = new Float32Array(count * 2), tile = new Float32Array(count * 2),
          speed = new Float32Array(count), kind = new Float32Array(count), alpha = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const phi = Math.acos(2 * Math.random() - 1), theta = Math.random() * Math.PI * 2, r = rFn(), sz = sizeFn();
      sph[i * 3] = phi; sph[i * 3 + 1] = r; sph[i * 3 + 2] = theta;
      size[i * 2] = sz * 2.5; size[i * 2 + 1] = sz * 0.35;   // matches the old sprite scale (size*5, size*0.7)
      const n = Math.floor(Math.random() * codeSnippets.length);
      tile[i * 2] = (n % ATLAS_COLS) / ATLAS_COLS; tile[i * 2 + 1] = 1 - (Math.floor(n / ATLAS_COLS) + 1) / atlasRows;
      speed[i] = (speedScale[0] + Math.random() * speedScale[1]) * (Math.random() > 0.5 ? 1 : -1) * speedMult;
      kind[i] = Math.random() > 0.5 ? 0 : 1;
      alpha[i] = [0.4, 0.6, 0.85][Math.floor(Math.random() * 3)];
    }
    geo.setAttribute("aSph", new THREE.InstancedBufferAttribute(sph, 3));
    geo.setAttribute("aSize", new THREE.InstancedBufferAttribute(size, 2));
    geo.setAttribute("aTile", new THREE.InstancedBufferAttribute(tile, 2));
    geo.setAttribute("aSpeed", new THREE.InstancedBufferAttribute(speed, 1));
    geo.setAttribute("aKind", new THREE.InstancedBufferAttribute(kind, 1));
    geo.setAttribute("aAlpha", new THREE.InstancedBufferAttribute(alpha, 1));
    const mat = new THREE.ShaderMaterial({
      vertexShader: TEXT_VERT, fragmentShader: TEXT_FRAG, transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: { uMap: { value: atlasTex }, uPhase: { value: 0 }, uScale: { value: 1 },
                  uTileSize: { value: new THREE.Vector2(1 / ATLAS_COLS, 1 / atlasRows) },
                  uBright: { value: new THREE.Color(C_BRIGHT) }, uMid: { value: new THREE.Color(C_MID) } },
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    textLayers.push(mat);
    return mesh;
  }

  const textOuter = textField(Math.round(1200 * q), () => 0.04 + Math.random() * 0.04, () => R1 + 0.03 + Math.random() * 0.08, [0.0002, 0.0008], 1);
  const textInner = textField(Math.round(100 * q), () => 0.03 + Math.random() * 0.03, () => R3 + 0.02, [0.0005, 0.001], 2);
  const textAmbient = textField(Math.round(400 * q), () => 0.03, () => R3 + 0.2 + Math.random() * (R1 - R3 - 0.3), [0.0003, 0.0006], 1.2);
  orbGroup.add(textOuter, textInner, textAmbient);

  // ORBITING DEBRIS
  const debrisGeos = [
    new THREE.IcosahedronGeometry(0.012, 0), new THREE.IcosahedronGeometry(0.02, 0), new THREE.IcosahedronGeometry(0.03, 1),
    new THREE.IcosahedronGeometry(0.008, 0), new THREE.TetrahedronGeometry(0.015, 0), new THREE.OctahedronGeometry(0.018, 0),
  ];
  const debris = [];
  for (let i = 0; i < Math.round(250 * q); i++) {
    const dc = Math.random() > 0.7 ? C_BRIGHT : C_MID;
    const mesh = new THREE.Mesh(debrisGeos[Math.floor(Math.random() * debrisGeos.length)],
      reg(new THREE.MeshBasicMaterial({ color: dc, transparent: true, opacity: 0.3 + Math.random() * 0.6, blending: THREE.AdditiveBlending }), dc));
    const orbitR = 1.2 + Math.random() * 4.0, speed = (0.08 + Math.random() * 0.6) * (Math.random() > 0.5 ? 1 : -1);
    const tiltX = (Math.random() - 0.5) * Math.PI * 0.9, tiltZ = (Math.random() - 0.5) * Math.PI * 0.5, phase = Math.random() * Math.PI * 2;
    mesh.userData = { orbitR, speed, tiltX, tiltZ, phase };
    debris.push(mesh); orbGroup.add(mesh);
    if (Math.random() > 0.85) {
      const trailPts = [];
      for (let j = 0; j <= 15; j++) { const a = -(j / 15) * 0.3; trailPts.push(new THREE.Vector3(orbitR * Math.cos(a + phase), orbitR * 0.08 * Math.sin(a * 3), orbitR * Math.sin(a + phase))); }
      mesh.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(trailPts), lineMat(C_FAINT, 0.08)));
    }
  }

  // DUST
  const dustCount = Math.round(2000 * q), dustPos = new Float32Array(dustCount * 3);
  for (let i = 0; i < dustCount; i++) {
    const rr = 0.5 + Math.pow(Math.random(), 0.6) * 7, theta = Math.random() * Math.PI * 2, phi = Math.acos(2 * Math.random() - 1);
    dustPos[i * 3] = rr * Math.sin(phi) * Math.cos(theta); dustPos[i * 3 + 1] = rr * Math.cos(phi); dustPos[i * 3 + 2] = rr * Math.sin(phi) * Math.sin(theta);
  }
  const dustGeo = new THREE.BufferGeometry(); dustGeo.setAttribute("position", new THREE.Float32BufferAttribute(dustPos, 3));
  const dotC = document.createElement("canvas"); dotC.width = dotC.height = 64;
  const dCtx = dotC.getContext("2d"), g = dCtx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,170,48,1)"); g.addColorStop(0.2, "rgba(255,120,20,0.6)"); g.addColorStop(0.5, "rgba(200,80,0,0.15)"); g.addColorStop(1, "rgba(100,40,0,0)");
  dCtx.fillStyle = g; dCtx.fillRect(0, 0, 64, 64);
  const dustPoints = new THREE.Points(dustGeo, reg(new THREE.PointsMaterial({ map: new THREE.CanvasTexture(dotC), size: 0.04, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true, color: C_BRIGHT }), C_BRIGHT));
  orbGroup.add(dustPoints);

  // SCANNING RINGS
  function makeScanRing(radius, thickness = 0.015) {
    const mesh = new THREE.Mesh(new THREE.RingGeometry(radius - thickness, radius + thickness, 120),
      reg(new THREE.MeshBasicMaterial({ color: C_BRIGHT, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false }), C_BRIGHT));
    mesh.rotation.x = Math.PI / 2; return mesh;
  }
  const scanRing1 = makeScanRing(R1, 0.01), scanRing2 = makeScanRing(R1 * 0.7, 0.008);
  orbGroup.add(scanRing1, scanRing2);

  // HEX NODES
  for (let i = 0; i < 15; i++) {
    const phi = Math.acos(2 * Math.random() - 1), theta = Math.random() * Math.PI * 2, r = R1 + 0.02;
    const hex = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.CircleGeometry(0.03 + Math.random() * 0.02, 6)), lineMat(C_MID, 0.5));
    hex.position.set(r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta));
    hex.lookAt(0, 0, 0); outerShell.add(hex);
  }

  // CAMERA CONTROL
  const sphericalScratch = new THREE.Spherical(), offsetScratch = new THREE.Vector3();
  function rotateBy(deltaTheta, deltaPhi) {
    offsetScratch.copy(camera.position).sub(controls.target);
    sphericalScratch.setFromVector3(offsetScratch);
    sphericalScratch.theta -= deltaTheta;
    sphericalScratch.phi = THREE.MathUtils.clamp(sphericalScratch.phi - deltaPhi, 0.05, Math.PI - 0.05);
    sphericalScratch.makeSafe();
    offsetScratch.setFromSpherical(sphericalScratch);
    camera.position.copy(controls.target).add(offsetScratch);
    camera.lookAt(controls.target);
    if (!motion) frame();
  }
  function zoomBy(factor) {
    offsetScratch.copy(camera.position).sub(controls.target);
    offsetScratch.setLength(THREE.MathUtils.clamp(offsetScratch.length() * factor, MIN_DISTANCE, MAX_DISTANCE));
    camera.position.copy(controls.target).add(offsetScratch);
    if (!motion) frame();
  }
  function resetView() { camera.position.copy(HOME_POSITION); controls.target.set(0, 0, 0); camera.lookAt(controls.target); controls.update(); if (!motion) frame(); }

  // ——— FRIDAY: voice level + state ———
  let textPhase = 0;
  let level = 0, levelTarget = 0, tone = 0, toneTarget = 0, flash = 0, state = "idle", paletteName = "amber";
  function pulse() { flash = 1; if (!motion) frame(); }        // recognition: one swell of the whole core
  const coreColor = new THREE.Color(C_HOT), alertColor = new THREE.Color(C_ALERT), hotColor = new THREE.Color(C_HOT);
  function setLevel(v) { levelTarget = Math.max(0, Math.min(1, +v || 0)); }
  function setTone(v) { toneTarget = Math.max(0, Math.min(1, +v || 0)); }   // 0 = low/slow voice, 1 = high/fast
  function setState(s) { state = s || "idle"; }
  function setPalette(name) {
    const pal = PALETTES[name]; if (!pal) return;
    paletteName = name;
    for (const [mat, key] of tinted) mat.color.setHex(pal[key]);
    for (const mat of textLayers) { mat.uniforms.uBright.value.setHex(pal.bright); mat.uniforms.uMid.value.setHex(pal.mid); }
    hotColor.setHex(pal.hot);
    if (!motion) frame();
  }

  // ANIMATION
  const clock = new THREE.Clock();
  let flickerTimer = 0, rafId = 0, disposed = false, paused = false, inFrame = false;
  let motion = !reduce;                       // runtime switch; see setMotion()
  controls.enableDamping = motion;            // damping needs a running loop
  function frame() {
    if (inFrame) return;                      // controls.update() fires "change" -> frame(); never recurse
    inFrame = true;
    try { frameBody(); } finally { inFrame = false; }
  }
  function frameBody() {
    const t = clock.getElapsedTime();
    level += (levelTarget - level) * 0.25;
    tone += (toneTarget - tone) * 0.2;
    const think = state === "thinking" ? 1 : 0, gate = state === "gate" ? 1 : 0, listen = state === "listening" ? 1 : 0;
    const spin = 1 + level * 3 + tone * 2.5 + think * 1.6;   // faster, higher voice -> faster shells
    flash *= 0.955;
    const grow = 1 + level * 0.16 + flash * 0.22;             // the sphere itself swells with speech (and once on recognition)
    orbGroup.scale.set(grow, grow, grow);
    chromaticPass.uniforms.uIntensity.value = 0.003 + level * 0.004 + tone * 0.003;

    outerShell.rotation.y += 0.0015 * spin; outerShell.rotation.x = Math.sin(t * 0.08) * 0.05;
    panelGroup.rotation.y += 0.0018 * spin; panelGroup.rotation.x = Math.sin(t * 0.08 + 0.5) * 0.04;
    shell2.rotation.y -= 0.001 * spin; shell2.rotation.z = Math.sin(t * 0.12) * 0.03;
    innerCore.rotation.y -= 0.005 * (1 + think * 2 + level * 2); innerCore.rotation.z += 0.002; innerCore.rotation.x = Math.cos(t * 0.1) * 0.08;
    icoWire.rotation.x += 0.008 * (1 + think); icoWire.rotation.y += 0.012 * (1 + think);

    const wave1 = Math.sin(t * 1.2);
    const wave3 = Math.pow(Math.max(0, Math.sin(t * 0.4)), 5);
    const wave4 = Math.pow(Math.max(0, Math.sin(t * 0.7 + 2)), 8);
    const fadeOut = Math.pow(Math.max(0, Math.sin(t * 0.25)), 3) * (1 - level);
    const breathe = listen * (0.5 + 0.5 * Math.sin(t * 2.2)) * 0.35;
    const surge = wave3 * 1.5 + wave4 * 2.0 + level * 2.6 + breathe;
    coreSphere.scale.setScalar(1 + surge + Math.sin(t * 5) * 0.05);
    const coreOpacity = Math.max(0, (0.08 + wave1 * 0.05 + surge * 0.2) * (1 - fadeOut * 0.95));
    coreSphereMat.opacity = Math.min(0.6, coreOpacity + level * 0.25);
    glowSphere.scale.setScalar(1 + surge * 0.8);
    glowSphereMat.opacity = Math.max(0, (0.03 + surge * 0.08) * (1 - fadeOut * 0.9));
    icoWire.scale.setScalar(1 + surge * 0.6);
    icoWireMat.opacity = Math.min(1, 0.5 + surge * 0.4);
    coreColor.copy(hotColor).lerp(alertColor, gate * (0.6 + 0.4 * Math.sin(t * 6)));
    coreSphereMat.color.copy(coreColor); icoWireMat.color.copy(coreColor);

    for (const d of debris) {
      const u = d.userData, a = t * u.speed * (1 + level * 0.6) + u.phase;
      d.position.set(u.orbitR * Math.cos(a) * Math.cos(u.tiltX), u.orbitR * Math.sin(u.tiltX) * Math.sin(a * 0.8) + Math.sin(a * 0.3 + u.tiltZ) * 0.2, u.orbitR * Math.sin(a) * Math.cos(u.tiltZ));
      d.rotation.x += 0.015; d.rotation.z += 0.01;
    }
    textPhase += 1 + level;                                  // the orbit itself runs on the GPU
    for (const mat of textLayers) { mat.uniforms.uPhase.value = textPhase; mat.uniforms.uScale.value = grow; }
    const scanSpeed = 1 + think * 2;
    const scanY1 = Math.sin(t * 0.4 * scanSpeed) * R1; scanRing1.position.y = scanY1;
    const scanS1 = Math.sqrt(Math.max(0, R1 * R1 - scanY1 * scanY1)) / R1; scanRing1.scale.set(scanS1, scanS1, 1); scanRing1.material.opacity = 0.2 * scanS1;
    const scanY2 = Math.sin(t * 0.6 * scanSpeed + 2) * R3; scanRing2.position.y = scanY2;
    const scanS2 = Math.sqrt(Math.max(0, R3 * R3 - scanY2 * scanY2)) / R3; scanRing2.scale.set(scanS2, scanS2, 1); scanRing2.material.opacity = 0.15 * scanS2;
    dustPoints.rotation.y += 0.0002 * spin;

    flickerTimer += 0.016;
    if (flickerTimer > 0.1) { flickerTimer = 0; for (const p of panelGroup.children) if (Math.random() > 0.95) p.visible = !p.visible; }

    bloom.strength = 1.6 + Math.sin(t * 0.8) * 0.3 + level * 1.1 + flash * 1.4;
    chromaticPass.uniforms.uTime.value = t;
    controls.update();
    composer.render();
  }
  let lastT = 0;
  function animate() {
    if (disposed || paused || !motion) return;
    rafId = requestAnimationFrame(animate);
    const now = performance.now();
    if (lastT) frameEma += (Math.min(100, now - lastT) - frameEma) * 0.1;
    lastT = now; adapt(now);
    frame();
  }
  // Static mode: one frame, then re-render only when the camera moves (drag,
  // scroll, gestures, zoom buttons). The guard above makes that safe.
  controls.addEventListener("change", () => { if (!motion) frame(); });
  function setMotion(on) {
    on = !!on;
    if (on === motion) return;
    motion = on;
    controls.enableDamping = on;
    cancelAnimationFrame(rafId);
    if (on) { clock.start(); animate(); } else frame();
  }
  if (motion) animate(); else frame();

  function onResize() {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h); composer.setSize(w, h);
    if (!motion) frame();
  }
  const ro = new ResizeObserver(onResize); ro.observe(container);

  function dispose() {
    disposed = true; cancelAnimationFrame(rafId); ro.disconnect(); controls.dispose();
    scene.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose();
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const m of mats) { if (!m) continue; if (m.map) m.map.dispose(); m.dispose(); }
    });
    composer.dispose(); renderer.dispose(); renderer.domElement.remove();
  }

  return {
    rotateBy, zoomBy, zoomIn: () => zoomBy(0.65), zoomOut: () => zoomBy(1.55), resetView, dispose,
    setLevel, setTone, setState, setMotion, setPalette, pulse, render: frame,
    get palette() { return paletteName; },
    get motion() { return motion; },
    pause() { paused = true; cancelAnimationFrame(rafId); },
    resume() { if (paused) { paused = false; if (motion) animate(); else frame(); } },
  };
}

/* ─────────────────────────── hand tracking (MediaPipe) ─────────────────────────── */
const WASM_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm";
const VISION_BUNDLE = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/vision_bundle.mjs";
const MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const WRIST = 0, THUMB_TIP = 4, INDEX_TIP = 8, MIDDLE_MCP = 9;
const PINCH_ON = 0.32, PINCH_OFF = 0.45, ROTATE_SPEED = 5.0, SMOOTHING = 0.4;
const dist2d = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

export class HandTracker {
  constructor(video, overlay, callbacks) {
    this.video = video; this.overlay = overlay; this.callbacks = callbacks;
    this.landmarker = null; this.stream = null; this.rafId = 0; this.running = false; this.lastVideoTime = -1; this.lastRun = 0; this.interval = 90;
    this.small = document.createElement("canvas"); this.small.width = 320; this.small.height = 240;
    this.smallCtx = this.small.getContext("2d", { alpha: false, willReadFrequently: false });
    this.handStates = new Map(); this.prevMode = "idle"; this.prevSpinGrab = null; this.prevZoomDist = null;
    this.lastStatus = { hands: 0, mode: "idle" };
    this.loop = this.loop.bind(this);
  }
  async start() {
    const stage = (s) => { if (this.callbacks.onStage) this.callbacks.onStage(s); };
    stage("camera");
    if (this.video.srcObject) { this.stream = null; }                     // shared camera: the page owns it
    else { this.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false }); this.video.srcObject = this.stream; }
    if (this.video.paused) await this.video.play();
    stage("model");
    const { FilesetResolver, HandLandmarker } = await import(VISION_BUNDLE);   // fetched only when gestures are switched on
    const fileset = await FilesetResolver.forVisionTasks(WASM_CDN);
    const options = { baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" }, runningMode: "VIDEO", numHands: 2,
      minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.6, minTrackingConfidence: 0.6 };
    try { this.landmarker = await HandLandmarker.createFromOptions(fileset, options); }
    catch { this.landmarker = await HandLandmarker.createFromOptions(fileset, { ...options, baseOptions: { ...options.baseOptions, delegate: "CPU" } }); }
    stage("ready");
    this.running = true; this.loop();
  }
  stop() {
    this.running = false; cancelAnimationFrame(this.rafId);
    if (this.landmarker) this.landmarker.close(); this.landmarker = null;
    if (this.stream) { this.stream.getTracks().forEach((t) => t.stop()); this.video.srcObject = null; } this.stream = null;
    this.handStates.clear(); this.prevMode = "idle"; this.prevSpinGrab = null; this.prevZoomDist = null;
    const ctx = this.overlay.getContext("2d"); if (ctx) ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
    this.emitStatus({ hands: 0, mode: "idle" });
  }
  loop() {
    if (!this.running) return;
    this.rafId = requestAnimationFrame(this.loop);
    if (!this.landmarker || this.video.readyState < 2) return;
    if (this.video.currentTime === this.lastVideoTime) return;
    const now = performance.now();
    if (now - this.lastRun < this.interval) return;          // ~11 fps while a hand is in view, 3 fps while idle
    this.lastRun = now;
    this.lastVideoTime = this.video.currentTime;
    this.smallCtx.drawImage(this.video, 0, 0, this.small.width, this.small.height);
    const result = this.landmarker.detectForVideo(this.small, now);
    this.interval = result.landmarks.length ? 90 : 330;
    this.processHands(result.landmarks, result.handedness.map((h) => (h[0] && h[0].categoryName) || "?"));
    this.drawOverlay(result.landmarks);
  }
  processHands(landmarks, labels) {
    const pinchedGrabs = [], seen = new Set();
    landmarks.forEach((lm, i) => {
      const label = labels[i]; seen.add(label);
      const handScale = dist2d(lm[WRIST], lm[MIDDLE_MCP]); if (handScale < 1e-6) return;
      const pinchRatio = dist2d(lm[THUMB_TIP], lm[INDEX_TIP]) / handScale;
      const raw = { x: 1 - (lm[THUMB_TIP].x + lm[INDEX_TIP].x) / 2, y: (lm[THUMB_TIP].y + lm[INDEX_TIP].y) / 2 };
      let st = this.handStates.get(label);
      if (!st) { st = { pinching: false, grab: raw }; this.handStates.set(label, st); }
      if (st.pinching && pinchRatio > PINCH_OFF) st.pinching = false;
      else if (!st.pinching && pinchRatio < PINCH_ON) st.pinching = true;
      st.grab = { x: st.grab.x + (raw.x - st.grab.x) * SMOOTHING, y: st.grab.y + (raw.y - st.grab.y) * SMOOTHING };
      if (st.pinching) pinchedGrabs.push(st.grab);
    });
    for (const key of this.handStates.keys()) if (!seen.has(key)) this.handStates.delete(key);
    const mode = pinchedGrabs.length >= 2 ? "zoom" : pinchedGrabs.length === 1 ? "spin" : "idle";
    if (mode !== this.prevMode) { this.prevSpinGrab = null; this.prevZoomDist = null; this.prevMode = mode; }
    if (mode === "spin") {
      const grab = pinchedGrabs[0];
      if (this.prevSpinGrab) {
        const dx = grab.x - this.prevSpinGrab.x, dy = grab.y - this.prevSpinGrab.y;
        if (Math.abs(dx) > 1e-4 || Math.abs(dy) > 1e-4) this.callbacks.onRotate(dx * ROTATE_SPEED, dy * ROTATE_SPEED);
      }
      this.prevSpinGrab = grab;
    } else if (mode === "zoom") {
      const d = Math.hypot(pinchedGrabs[0].x - pinchedGrabs[1].x, pinchedGrabs[0].y - pinchedGrabs[1].y);
      if (this.prevZoomDist && d > 1e-4) this.callbacks.onZoom(Math.min(1.18, Math.max(0.85, this.prevZoomDist / d)));
      this.prevZoomDist = d;
    }
    this.emitStatus({ hands: landmarks.length, mode });
  }
  emitStatus(status) {
    if (status.hands !== this.lastStatus.hands || status.mode !== this.lastStatus.mode) { this.lastStatus = status; this.callbacks.onStatus(status); }
  }
  drawOverlay(landmarks) {
    const ctx = this.overlay.getContext("2d"); if (!ctx) return;
    const { width, height } = this.overlay; ctx.clearRect(0, 0, width, height);
    for (const lm of landmarks) {
      const thumb = lm[THUMB_TIP], index = lm[INDEX_TIP];
      const tx = (1 - thumb.x) * width, ty = thumb.y * height, ix = (1 - index.x) * width, iy = index.y * height;
      const handScale = dist2d(lm[WRIST], lm[MIDDLE_MCP]);
      const pinched = handScale > 1e-6 && dist2d(thumb, index) / handScale < PINCH_ON;
      ctx.strokeStyle = pinched ? "#ffcc66" : "rgba(255,170,48,0.5)"; ctx.lineWidth = pinched ? 2 : 1;
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(ix, iy); ctx.stroke();
      ctx.fillStyle = pinched ? "#ffcc66" : "rgba(255,170,48,0.7)";
      for (const [x, y] of [[tx, ty], [ix, iy]]) { ctx.beginPath(); ctx.arc(x, y, pinched ? 5 : 3, 0, Math.PI * 2); ctx.fill(); }
    }
  }
}
