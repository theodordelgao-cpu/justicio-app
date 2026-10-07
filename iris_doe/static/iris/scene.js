/* Iris · scène 3D « chantier de nuit »
   Un immeuble se construit étage par étage, une grue apporte les documents,
   l'œil d'Iris balaie le bâtiment d'un rayon de scan.
   data-progress sur <body> (0 → 1) fixe la hauteur atteinte ; absent = animation en boucle. */
(function () {
  var canvas = document.getElementById('iris-scene');
  if (!canvas || !window.THREE) return;
  var renderer;
  try { renderer = new THREE.WebGLRenderer({canvas: canvas, antialias: true, alpha: true}); }
  catch (e) { return; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;

  var C = {safety: 0xff7a1a, iris: 0x5cc8ff, steel: 0x8da2c0, slab: 0x7fa6d6, night: 0x050a14};
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var body = document.body;
  var fixedProgress = body.dataset.progress != null ? Math.max(0, Math.min(1, parseFloat(body.dataset.progress))) : null;

  var scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(C.night, 0.028);
  var camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);

  scene.add(new THREE.HemisphereLight(0x6f8fc8, 0x0a0f1a, 0.55));
  var moon = new THREE.DirectionalLight(0xbcd4ff, 0.7); moon.position.set(-10, 18, 8); scene.add(moon);

  var site = new THREE.Group(); scene.add(site);

  function lineMat(color, opacity) { return new THREE.LineBasicMaterial({color: color, transparent: true, opacity: opacity == null ? 1 : opacity}); }
  function segs(arr, mat) { var g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3)); return new THREE.LineSegments(g, mat); }

  /* ---------- sol : plan bleu + dalle ---------- */
  var grid = new THREE.GridHelper(80, 80, 0x1d3a66, 0x10213d); grid.material.transparent = true; grid.material.opacity = 0.7; site.add(grid);
  var pad = new THREE.Mesh(new THREE.BoxGeometry(9, 0.2, 7), new THREE.MeshStandardMaterial({color: 0x1a2436, roughness: 0.9}));
  pad.position.y = 0.1; site.add(pad);
  // tracé d'implantation au sol
  var trace = [];
  [[-6, -5, 6, -5], [6, -5, 6, 5], [6, 5, -6, 5], [-6, 5, -6, -5]].forEach(function (l) { trace.push(l[0], 0.02, l[1], l[2], 0.02, l[3]); });
  for (var t = -6; t <= 6; t += 0.6) trace.push(t, 0.02, -5.4, t + 0.3, 0.02, -5.4);
  site.add(segs(trace, lineMat(C.safety, 0.55)));

  /* ---------- immeuble en construction ---------- */
  var FLOORS = 9, FH = 1.05, BW = 6, BD = 4.4;
  var building = new THREE.Group(); building.position.y = 0.2; site.add(building);
  var slabMat = new THREE.MeshStandardMaterial({color: C.slab, transparent: true, opacity: 0.3, metalness: 0.2, roughness: 0.6, depthWrite: false});
  var edgeMat = lineMat(0xcfe4ff, 0.95), colMat = lineMat(0xa9bddb, 0.7);
  var floors = [];
  for (var f = 0; f < FLOORS; f++) {
    var fl = new THREE.Group();
    var slab = new THREE.Mesh(new THREE.BoxGeometry(BW, 0.12, BD), slabMat);
    var edges = new THREE.LineSegments(new THREE.EdgesGeometry(slab.geometry), edgeMat);
    fl.add(slab, edges);
    var cols = [];
    for (var cx = -BW / 2; cx <= BW / 2 + 0.01; cx += BW / 4) for (var cz = -BD / 2; cz <= BD / 2 + 0.01; cz += BD / 2) cols.push(cx, 0, cz, cx, FH, cz);
    // fenêtres (cadres) sur la façade
    for (var wx = -BW / 2 + 0.4; wx < BW / 2 - 0.3; wx += 0.75) {
      var y0 = 0.3, y1 = 0.85, z = BD / 2 + 0.01;
      cols.push(wx, y0, z, wx + 0.45, y0, z, wx + 0.45, y0, z, wx + 0.45, y1, z, wx + 0.45, y1, z, wx, y1, z, wx, y1, z, wx, y0, z);
    }
    fl.add(segs(cols, colMat));
    // quelques fenêtres éclairées
    var lit = new THREE.Group();
    for (var k = 0; k < 3; k++) {
      var win = new THREE.Mesh(new THREE.PlaneGeometry(0.45, 0.55), new THREE.MeshBasicMaterial({color: k % 2 ? 0xffc26b : 0x9fdcff, transparent: true, opacity: 0}));
      win.position.set(-BW / 2 + 0.625 + Math.floor(Math.random() * 7) * 0.75, 0.575, BD / 2 + 0.02); lit.add(win);
    }
    fl.add(lit);
    fl.position.y = f * FH; fl.userData = {lit: lit, shown: 0};
    building.add(fl); floors.push(fl);
  }
  // échafaudage sur le côté
  var scaf = [];
  for (var sy = 0; sy <= FLOORS * FH; sy += FH / 2) { scaf.push(BW / 2 + 0.5, sy, -BD / 2, BW / 2 + 0.5, sy, BD / 2); }
  for (var sz = -BD / 2; sz <= BD / 2 + 0.01; sz += BD / 4) { scaf.push(BW / 2 + 0.5, 0, sz, BW / 2 + 0.5, FLOORS * FH, sz); }
  for (var sd = 0; sd < FLOORS * 2; sd++) { var a = sd * FH / 2; scaf.push(BW / 2 + 0.5, a, -BD / 2, BW / 2 + 0.5, a + FH / 2, -BD / 2 + BD / 4); }
  var scaffold = segs(scaf, lineMat(C.safety, 0.35)); building.add(scaffold);

  /* ---------- grue à tour ---------- */
  var crane = new THREE.Group(); crane.position.set(-1.6, 0.2, -5.2); site.add(crane);
  var MH = 11.5, mast = [];
  var s = 0.32;
  for (var my = 0; my < MH; my += 0.8) {
    var y2 = my + 0.8;
    [[-s, -s], [s, -s], [s, s], [-s, s]].forEach(function (p) { mast.push(p[0], my, p[1], p[0], y2, p[1]); });
    mast.push(-s, my, -s, s, y2, -s, s, my, -s, s, y2, s, s, my, s, -s, y2, s, -s, my, s, -s, y2, -s);
    mast.push(-s, y2, -s, s, y2, -s, s, y2, -s, s, y2, s, s, y2, s, -s, y2, s, -s, y2, s, -s, y2, -s);
  }
  crane.add(segs(mast, lineMat(C.safety, 0.9)));
  var slew = new THREE.Group(); slew.position.y = MH; crane.add(slew);
  var jib = [], JL = 11, CJ = 3.5;
  for (var jx = -CJ; jx < JL; jx += 0.7) {
    var x2 = jx + 0.7;
    jib.push(jx, 0, -0.22, x2, 0, -0.22, jx, 0, 0.22, x2, 0, 0.22, jx, 0.5, 0, x2, 0.5, 0);
    jib.push(jx, 0, -0.22, x2, 0.5, 0, jx, 0, 0.22, x2, 0.5, 0, jx, 0, -0.22, jx, 0, 0.22);
  }
  jib.push(0, 0.5, 0, 0, 2.2, 0, 0, 2.2, 0, JL * 0.75, 0.5, 0, 0, 2.2, 0, -CJ, 0.5, 0);
  slew.add(segs(jib, lineMat(C.safety, 0.9)));
  var cab = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.7, 0.7), new THREE.MeshStandardMaterial({color: C.safety, emissive: 0x5a2400, roughness: 0.5}));
  cab.position.set(0.6, -0.35, 0.55); slew.add(cab);
  var cw = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.8, 0.8), new THREE.MeshStandardMaterial({color: 0x3a4456, roughness: 0.8}));
  cw.position.set(-CJ + 0.6, -0.2, 0); slew.add(cw);
  var beacon = new THREE.Mesh(new THREE.SphereGeometry(0.09, 12, 8), new THREE.MeshBasicMaterial({color: 0xff3b2f}));
  beacon.position.set(0, 2.3, 0); slew.add(beacon);
  var trolley = new THREE.Group(); slew.add(trolley);
  trolley.add(new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.18, 0.5), new THREE.MeshStandardMaterial({color: 0x2a3446})));
  var cableGeo = new THREE.BufferGeometry(); cableGeo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, -1, 0], 3));
  trolley.add(new THREE.Line(cableGeo, lineMat(0xd8e4f5, 0.8)));
  var hook = new THREE.Group(); trolley.add(hook);
  // document suspendu
  function docTexture() {
    var c = document.createElement('canvas'); c.width = 256; c.height = 340; var x = c.getContext('2d');
    x.fillStyle = '#eef5ff'; x.fillRect(0, 0, 256, 340);
    x.fillStyle = '#ff7a1a'; x.fillRect(0, 0, 256, 18);
    x.fillStyle = '#1b2a44'; x.font = 'bold 22px sans-serif'; x.fillText('DOE', 22, 56);
    x.fillStyle = '#8fa6c6'; for (var l = 0; l < 9; l++) x.fillRect(22, 82 + l * 24, 212 - (l % 3) * 46, 8);
    x.strokeStyle = '#5cc8ff'; x.lineWidth = 4; x.strokeRect(150, 270, 84, 50);
    var tx = new THREE.CanvasTexture(c); tx.encoding = THREE.sRGBEncoding; return tx;
  }
  var docTex = docTexture();
  var payload = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.2, 0.04), [
    new THREE.MeshStandardMaterial({color: 0xdfe8f5}), new THREE.MeshStandardMaterial({color: 0xdfe8f5}),
    new THREE.MeshStandardMaterial({color: 0xdfe8f5}), new THREE.MeshStandardMaterial({color: 0xdfe8f5}),
    new THREE.MeshStandardMaterial({map: docTex, emissive: 0x223044}), new THREE.MeshStandardMaterial({map: docTex, emissive: 0x223044})
  ]);
  payload.position.y = -0.75; hook.add(payload);
  var sling = segs([0, 0, 0, -0.42, -0.15, 0, 0, 0, 0, 0.42, -0.15, 0], lineMat(0xd8e4f5, 0.8)); hook.add(sling);

  /* ---------- l'œil d'Iris + rayon de scan ---------- */
  var eye = new THREE.Group(); site.add(eye);
  var ring = new THREE.Mesh(new THREE.TorusGeometry(0.9, 0.06, 16, 96), new THREE.MeshStandardMaterial({color: 0x2a3a55, metalness: 0.9, roughness: 0.25, emissive: 0x0a1a2a}));
  var ring2 = new THREE.Mesh(new THREE.TorusGeometry(1.15, 0.015, 8, 120), new THREE.MeshBasicMaterial({color: C.iris, transparent: true, opacity: 0.7}));
  var pupil = new THREE.Mesh(new THREE.CircleGeometry(0.62, 48), new THREE.MeshBasicMaterial({color: C.iris, transparent: true, opacity: 0.85, side: THREE.DoubleSide}));
  var core = new THREE.Mesh(new THREE.SphereGeometry(0.2, 24, 16), new THREE.MeshBasicMaterial({color: 0xeaf8ff}));
  // lamelles
  var blades = new THREE.Group();
  for (var b = 0; b < 8; b++) {
    var bl = new THREE.Mesh(new THREE.CircleGeometry(0.5, 3, 0, Math.PI * 0.55), new THREE.MeshStandardMaterial({color: 0x1d2a40, metalness: 0.9, roughness: 0.3, side: THREE.DoubleSide}));
    var piv = new THREE.Group(); piv.rotation.z = b / 8 * Math.PI * 2; bl.position.x = 0.62; piv.add(bl); blades.add(piv);
  }
  eye.add(ring, ring2, pupil, blades, core);
  var eyeLight = new THREE.PointLight(C.iris, 1.6, 14); eye.add(eyeLight);
  var beamMat = new THREE.MeshBasicMaterial({color: C.iris, transparent: true, opacity: 0.08, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending});
  var beam = new THREE.Mesh(new THREE.ConeGeometry(4.2, 1, 48, 1, true), beamMat); site.add(beam);
  var scanMat = new THREE.MeshBasicMaterial({color: C.iris, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending});
  var scanPlane = new THREE.Mesh(new THREE.PlaneGeometry(BW + 0.6, BD + 0.6), scanMat); scanPlane.rotation.x = -Math.PI / 2; site.add(scanPlane);
  var scanEdge = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(BW + 0.6, BD + 0.6)), lineMat(C.iris, 0.9)); scanEdge.rotation.x = -Math.PI / 2; site.add(scanEdge);

  /* ---------- projecteurs, barrières, poussière ---------- */
  function flood(x, z) {
    var g = new THREE.Group(); g.position.set(x, 0, z);
    g.add(segs([0, 0, 0, 0, 3.2, 0], lineMat(0x6c7a90, 0.9)));
    var head = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.3, 0.15), new THREE.MeshBasicMaterial({color: 0xfff1d6})); head.position.y = 3.25; g.add(head);
    var light = new THREE.PointLight(0xffb36b, 1.1, 12); light.position.y = 3; g.add(light);
    site.add(g);
  }
  flood(5.5, 4.5); flood(-4.5, 5);
  (function () {
    var c = document.createElement('canvas'); c.width = c.height = 128; var x = c.getContext('2d');
    var g = x.createRadialGradient(64, 64, 0, 64, 64, 64); g.addColorStop(0, 'rgba(255,170,90,0.55)'); g.addColorStop(1, 'rgba(255,170,90,0)');
    x.fillStyle = g; x.fillRect(0, 0, 128, 128);
    var tx = new THREE.CanvasTexture(c);
    [[5.5, 4.5], [-4.5, 5], [0, 0]].forEach(function (p, i) {
      var m = new THREE.Mesh(new THREE.PlaneGeometry(i === 2 ? 16 : 9, i === 2 ? 14 : 9), new THREE.MeshBasicMaterial({map: tx, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: i === 2 ? 0.35 : 0.8, color: i === 2 ? 0x5cc8ff : 0xffffff}));
      m.rotation.x = -Math.PI / 2; m.position.set(p[0], 0.03, p[1]); site.add(m);
    });
  })();
  function stripeTex() {
    var c = document.createElement('canvas'); c.width = 128; c.height = 32; var x = c.getContext('2d');
    x.fillStyle = '#f5f1e8'; x.fillRect(0, 0, 128, 32); x.fillStyle = '#ff5a1f';
    for (var i = -2; i < 10; i++) { x.beginPath(); x.moveTo(i * 16, 32); x.lineTo(i * 16 + 8, 32); x.lineTo(i * 16 + 24, 0); x.lineTo(i * 16 + 16, 0); x.fill(); }
    var tx = new THREE.CanvasTexture(c); tx.encoding = THREE.sRGBEncoding; return tx;
  }
  var stripe = stripeTex();
  [[3.5, 5.8, 0], [0.8, 6.1, 0.15], [-2, 6, -0.1]].forEach(function (p) {
    var g = new THREE.Group(); g.position.set(p[0], 0, p[1]); g.rotation.y = p[2];
    var bar = new THREE.Mesh(new THREE.BoxGeometry(2, 0.28, 0.08), new THREE.MeshStandardMaterial({map: stripe, roughness: 0.6, emissive: 0x221008})); bar.position.y = 0.8; g.add(bar);
    g.add(segs([-0.85, 0, 0, -0.85, 0.95, 0, 0.85, 0, 0, 0.85, 0.95, 0], lineMat(0x9aa8bc, 0.9)));
    site.add(g);
  });
  var N = 260, dg = new THREE.BufferGeometry(), dp = new Float32Array(N * 3);
  for (var i = 0; i < N; i++) { dp[i * 3] = (Math.random() - 0.5) * 30; dp[i * 3 + 1] = Math.random() * 14; dp[i * 3 + 2] = (Math.random() - 0.5) * 30; }
  dg.setAttribute('position', new THREE.BufferAttribute(dp, 3));
  var dust = new THREE.Points(dg, new THREE.PointsMaterial({color: 0xffd9b0, size: 0.05, transparent: true, opacity: 0.55, depthWrite: false}));
  site.add(dust);

  /* ---------- cadrage ---------- */
  var narrow = false;
  function resize() {
    var w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h;
    narrow = w < 900;
    camera.fov = narrow ? 50 : 38; camera.updateProjectionMatrix();
    if (!narrow && !body.classList.contains('scene-dim')) camera.setViewOffset(w, h, -w * 0.22, 0, w, h); else camera.clearViewOffset();
  }
  window.addEventListener('resize', resize); resize();

  var mx = 0, my = 0, tmx = 0, tmy = 0;
  window.addEventListener('pointermove', function (e) { tmx = e.clientX / window.innerWidth - 0.5; tmy = e.clientY / window.innerHeight - 0.5; }, {passive: true});

  var boost = 0, shown = 0, clock = 0, last = performance.now(), running = true;
  window.IrisScene = {
    boost: function () { boost = 1; },
    setProgress: function (p) { fixedProgress = Math.max(0, Math.min(1, p)); }
  };

  function frame(now) {
    var dt = Math.min(0.05, (now - last) / 1000); last = now;
    var speed = 1 + boost * 3; boost = Math.max(0, boost - dt * 0.05);
    if (!reduce) clock += dt * speed;
    var t = clock;

    // hauteur atteinte : fixée par la page, ou boucle de construction
    var target = fixedProgress != null ? 0.12 + fixedProgress * 0.88 : (0.15 + 0.85 * ((t * 0.06) % 1));
    if (fixedProgress == null && ((t * 0.06) % 1) > 0.94) target = 0.15;
    shown += (target * FLOORS - shown) * Math.min(1, dt * (fixedProgress != null ? 1.5 : 2.5));
    if (reduce) shown = target * FLOORS;
    floors.forEach(function (fl, i) {
      var k = Math.max(0, Math.min(1, shown - i));
      fl.visible = k > 0.01;
      fl.scale.set(1, Math.max(0.01, k), 1);
      fl.position.y = i * FH + (1 - k) * 1.5;
      fl.userData.lit.children.forEach(function (w, j) { w.material.opacity = k > 0.95 ? 0.35 + 0.35 * Math.sin(t * 0.7 + i + j * 2) : 0; });
    });
    var topY = 0.2 + shown * FH;
    scaffold.scale.y = Math.max(0.02, shown / FLOORS);

    // grue : pivote vers l'immeuble, chariot qui va et vient, crochet qui descend sur le toit
    var cyc = (t * 0.12) % 1;
    var swing = Math.sin(cyc * Math.PI * 2);
    slew.rotation.y = -1.28 + swing * 0.3;
    var tr = 5.0 + swing * 1.4;
    trolley.position.x = tr;
    var drop = MH - (topY + 1.4) - 0.6 * (0.5 + 0.5 * Math.cos(cyc * Math.PI * 4));
    hook.position.y = -Math.max(1.5, drop);
    cableGeo.attributes.position.setY(1, hook.position.y); cableGeo.attributes.position.needsUpdate = true;
    hook.rotation.y = t * 0.6; hook.rotation.z = Math.sin(t * 1.3) * 0.04;
    beacon.material.color.setHex(Math.sin(t * 4) > 0 ? 0xff3b2f : 0x40100c);

    // œil d'Iris au-dessus du bâtiment, rayon de scan qui balaie les étages
    var eyeY = Math.max(6.5, topY + 3.2);
    eye.position.set(0.3, eyeY + Math.sin(t * 0.9) * 0.15, 0.4);
    eye.lookAt(camera.position);
    ring2.rotation.z = t * 0.6;
    var open = 0.5 + 0.5 * Math.sin(t * 0.8);
    blades.children.forEach(function (p) { p.children[0].position.x = 0.45 + open * 0.35; });
    core.scale.setScalar(0.8 + open * 0.5);
    var scanY = 0.3 + (0.5 + 0.5 * Math.sin(t * 0.9)) * Math.max(0.5, topY - 0.3);
    scanPlane.position.set(0, scanY, 0); scanEdge.position.copy(scanPlane.position);
    scanMat.opacity = 0.12 + 0.1 * Math.sin(t * 6) + boost * 0.2;
    var bh = eyeY - scanY; beam.scale.set(1, bh, 1); beam.position.set(0.15, scanY + bh / 2, 0.2);
    beamMat.opacity = 0.05 + boost * 0.08;
    eyeLight.intensity = 1.2 + open + boost * 2;

    dust.rotation.y = t * 0.01;
    var pos = dust.geometry.attributes.position;
    for (var q = 0; q < N; q++) { var y = pos.getY(q) + dt * 0.15 * speed; pos.setY(q, y > 14 ? 0 : y); }
    pos.needsUpdate = true;

    // caméra en orbite lente
    mx += (tmx - mx) * 0.04; my += (tmy - my) * 0.04;
    var ang = 0.75 + Math.sin(t * 0.05) * 0.35 + mx * 0.4;
    var R = narrow ? 31 : 24;
    camera.position.set(Math.sin(ang) * R, 8.5 + my * 4 + Math.sin(t * 0.07), Math.cos(ang) * R);
    camera.lookAt(0, Math.max(4.6, topY * 0.5 + 2.6), 0);
    renderer.render(scene, camera);
  }
  function loop(now) { if (!running) return; frame(now); requestAnimationFrame(loop); }
  document.addEventListener('visibilitychange', function () { running = !document.hidden; if (running) { last = performance.now(); requestAnimationFrame(loop); } });
  if (reduce) { clock = 20; frame(performance.now()); window.addEventListener('resize', function () { frame(performance.now()); }); }
  else requestAnimationFrame(loop);
})();
