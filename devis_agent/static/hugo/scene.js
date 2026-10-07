/* Hugo · scène 3D : le devis flotte au-dessus d'un plan de chantier et se remplit en direct.
   window.HugoScene.update(données) redessine la feuille ; boost() lance le balayage « Hugo calcule ». */
(function () {
  var canvas = document.getElementById('hugo-scene');
  if (!canvas || !window.THREE) return;
  var renderer;
  try { renderer = new THREE.WebGLRenderer({canvas: canvas, antialias: true, alpha: true}); } catch (e) { return; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var body = document.body;

  var scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x050a14, 0.035);
  var camera = new THREE.PerspectiveCamera(36, 1, 0.1, 200);
  scene.add(new THREE.HemisphereLight(0x7a96d0, 0x0a0f1a, 0.7));
  var key = new THREE.DirectionalLight(0xfff1d6, 1.0); key.position.set(-6, 10, 8); scene.add(key);
  var gold = new THREE.PointLight(0xffc94d, 1.4, 16); gold.position.set(3, 2, 4); scene.add(gold);

  function lineMat(c, o) { return new THREE.LineBasicMaterial({color: c, transparent: true, opacity: o == null ? 1 : o}); }
  function segs(a, m) { var g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(a, 3)); return new THREE.LineSegments(g, m); }

  var world = new THREE.Group(); scene.add(world);
  var grid = new THREE.GridHelper(60, 60, 0x1d3a66, 0x10213d); grid.material.transparent = true; grid.material.opacity = 0.6; grid.position.y = -3.2; world.add(grid);

  // maison filaire en arrière-plan (le chantier qu'on chiffre)
  var house = new THREE.Group(); house.position.set(-5.5, -3.2, -6); world.add(house);
  house.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(5, 3, 4)), lineMat(0x8fb3e8, 0.55)));
  house.children[0].position.y = 1.5;
  house.add(segs([-2.5, 3, -2, 0, 4.8, -2, 0, 4.8, -2, 2.5, 3, -2, -2.5, 3, 2, 0, 4.8, 2, 0, 4.8, 2, 2.5, 3, 2, 0, 4.8, -2, 0, 4.8, 2,
                  -0.5, 0, 2.01, -0.5, 2, 2.01, -0.5, 2, 2.01, 0.6, 2, 2.01, 0.6, 2, 2.01, 0.6, 0, 2.01,
                  1.2, 1.2, 2.01, 2, 1.2, 2.01, 2, 1.2, 2.01, 2, 2.2, 2.01, 2, 2.2, 2.01, 1.2, 2.2, 2.01, 1.2, 2.2, 2.01, 1.2, 1.2, 2.01], lineMat(0x8fb3e8, 0.55)));
  // cotes orange
  house.add(segs([-2.5, 0.02, 2.6, 2.5, 0.02, 2.6, -2.5, 0.02, 2.4, -2.5, 0.02, 2.8, 2.5, 0.02, 2.4, 2.5, 0.02, 2.8], lineMat(0xff7a1a, 0.8)));

  /* ---------- la feuille de devis (texture redessinée en direct) ---------- */
  var TW = 1024, TH = 1400;
  var tc = document.createElement('canvas'); tc.width = TW; tc.height = TH;
  var ctx = tc.getContext('2d');
  var tex = new THREE.CanvasTexture(tc); tex.encoding = THREE.sRGBEncoding; tex.anisotropy = 8;
  var state = {entreprise: 'Votre entreprise', client: 'Client', lignes: [], ttc: '0,00 €', tva: '', numero: 'DEVIS'};
  var scanY = -1;

  function wrap(text, max) { return text.length > max ? text.slice(0, max - 1) + '…' : text; }
  function draw() {
    ctx.fillStyle = '#f7f8fb'; ctx.fillRect(0, 0, TW, TH);
    ctx.fillStyle = '#ffc94d'; ctx.fillRect(0, 0, TW, 22);
    ctx.fillStyle = '#14202e'; ctx.font = 'bold 76px Arial'; ctx.fillText('DEVIS', 70, 150);
    ctx.font = '30px Arial'; ctx.fillStyle = '#5f6b7a'; ctx.fillText(wrap(state.numero, 30), 70, 200);
    ctx.textAlign = 'right'; ctx.fillStyle = '#14202e'; ctx.font = 'bold 34px Arial'; ctx.fillText(wrap(state.entreprise, 26), TW - 70, 130);
    ctx.font = '28px Arial'; ctx.fillStyle = '#5f6b7a'; ctx.fillText('Client : ' + wrap(state.client, 28), TW - 70, 180); ctx.textAlign = 'left';
    ctx.fillStyle = '#e3e7ee'; ctx.fillRect(70, 250, TW - 140, 4);
    ctx.font = 'bold 24px Arial'; ctx.fillStyle = '#8a95a5';
    ctx.fillText('DÉSIGNATION', 70, 300); ctx.textAlign = 'right'; ctx.fillText('TOTAL HT', TW - 70, 300); ctx.textAlign = 'left';
    var y = 360;
    var rows = state.lignes.length ? state.lignes.slice(0, 9) : [];
    if (!rows.length) {
      ctx.fillStyle = '#c7cdd6';
      for (var k = 0; k < 6; k++) { ctx.fillRect(70, y - 22 + k * 82, 480 - (k % 3) * 90, 22); ctx.fillRect(TW - 230, y - 22 + k * 82, 160, 22); }
    }
    rows.forEach(function (l) {
      ctx.fillStyle = '#14202e'; ctx.font = '32px Arial'; ctx.fillText(wrap(l.libelle, 34), 70, y);
      ctx.fillStyle = '#8a95a5'; ctx.font = '24px Arial'; ctx.fillText(l.detail || '', 70, y + 34);
      ctx.textAlign = 'right'; ctx.fillStyle = '#14202e'; ctx.font = 'bold 32px Arial'; ctx.fillText(l.total || '', TW - 70, y); ctx.textAlign = 'left';
      ctx.fillStyle = '#eceff4'; ctx.fillRect(70, y + 52, TW - 140, 2);
      y += 92;
    });
    if (state.lignes.length > 9) { ctx.fillStyle = '#8a95a5'; ctx.font = '26px Arial'; ctx.fillText('+ ' + (state.lignes.length - 9) + ' autres lignes', 70, y); }
    // total
    ctx.fillStyle = '#14202e'; ctx.fillRect(TW - 520, TH - 250, 450, 130);
    ctx.fillStyle = '#ffc94d'; ctx.font = 'bold 26px Arial'; ctx.fillText(state.tva || 'TOTAL', TW - 490, TH - 205);
    ctx.textAlign = 'right'; ctx.fillStyle = '#ffffff'; ctx.font = 'bold 60px Arial'; ctx.fillText(state.ttc, TW - 100, TH - 145); ctx.textAlign = 'left';
    ctx.strokeStyle = '#14202e'; ctx.lineWidth = 3; ctx.strokeRect(70, TH - 250, 380, 130);
    ctx.fillStyle = '#8a95a5'; ctx.font = '22px Arial'; ctx.fillText('Bon pour accord', 90, TH - 210);
    if (scanY >= 0) {
      var g = ctx.createLinearGradient(0, scanY - 60, 0, scanY + 10);
      g.addColorStop(0, 'rgba(255,201,77,0)'); g.addColorStop(1, 'rgba(255,201,77,0.55)');
      ctx.fillStyle = g; ctx.fillRect(0, scanY - 60, TW, 70);
    }
    tex.needsUpdate = true;
  }
  draw();

  var docGroup = new THREE.Group(); world.add(docGroup);
  var W = 3.4, H = W * TH / TW;
  var sheet = new THREE.Mesh(new THREE.BoxGeometry(W, H, 0.03), [
    new THREE.MeshStandardMaterial({color: 0xdfe4ec}), new THREE.MeshStandardMaterial({color: 0xdfe4ec}),
    new THREE.MeshStandardMaterial({color: 0xdfe4ec}), new THREE.MeshStandardMaterial({color: 0xdfe4ec}),
    new THREE.MeshStandardMaterial({map: tex, roughness: 0.7, emissive: 0x1a1f28, emissiveIntensity: 0.4}),
    new THREE.MeshStandardMaterial({color: 0xcfd6e0})
  ]);
  docGroup.add(sheet);
  var frame = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(W + 0.25, H + 0.25)), lineMat(0xffc94d, 0.8));
  frame.position.z = -0.05; docGroup.add(frame);
  // feuilles empilées derrière
  for (var i = 1; i <= 3; i++) {
    var back = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshStandardMaterial({color: 0xb9c3d2, transparent: true, opacity: 0.55 - i * 0.12, side: THREE.DoubleSide}));
    back.position.set(i * 0.18, -i * 0.12, -i * 0.25); back.rotation.z = i * 0.03; docGroup.add(back);
  }

  /* ---------- outils qui gravitent : mètre ruban et crayon ---------- */
  var tools = new THREE.Group(); world.add(tools);
  var ruler = new THREE.Group();
  ruler.add(new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.32, 0.04), new THREE.MeshStandardMaterial({color: 0xffc94d, metalness: 0.3, roughness: 0.4})));
  var tk = []; for (var r = -1.5; r <= 1.5; r += 0.1) { var big = Math.abs((r * 10) % 5) < 0.01; tk.push(r, 0.16, 0.025, r, big ? 0.02 : 0.09, 0.025); }
  ruler.add(segs(tk, lineMat(0x1d1400, 0.9)));
  tools.add(ruler);
  var pencil = new THREE.Group();
  var body1 = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 2.0, 6), new THREE.MeshStandardMaterial({color: 0xff7a1a, roughness: 0.5}));
  var tip = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.3, 6), new THREE.MeshStandardMaterial({color: 0xe8d2b0}));
  tip.position.y = -1.15; tip.rotation.x = Math.PI;
  pencil.add(body1, tip); tools.add(pencil);
  // pastilles « € » dorées
  var coinMat = new THREE.MeshStandardMaterial({color: 0xffc94d, metalness: 0.9, roughness: 0.25, emissive: 0x3a2800});
  var coins = [];
  for (var cI = 0; cI < 7; cI++) {
    var coin = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.05, 32), coinMat);
    coin.userData = {a: cI / 7 * Math.PI * 2, r: 3 + (cI % 3) * 0.5, y: -1.5 + (cI % 4) * 0.9};
    world.add(coin); coins.push(coin);
  }
  // poussière dorée
  var N = 180, dg = new THREE.BufferGeometry(), dp = new Float32Array(N * 3);
  for (var p = 0; p < N; p++) { dp[p * 3] = (Math.random() - 0.5) * 22; dp[p * 3 + 1] = Math.random() * 10 - 3; dp[p * 3 + 2] = (Math.random() - 0.5) * 16; }
  dg.setAttribute('position', new THREE.BufferAttribute(dp, 3));
  var dust = new THREE.Points(dg, new THREE.PointsMaterial({color: 0xffe0a0, size: 0.04, transparent: true, opacity: 0.6, depthWrite: false})); world.add(dust);

  var narrow = false;
  function resize() {
    var w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; narrow = w < 1000;
    camera.fov = narrow ? 44 : 36; camera.updateProjectionMatrix();
    if (!narrow && !body.classList.contains('scene-dim')) camera.setViewOffset(w, h, -w * 0.25, 0, w, h); else camera.clearViewOffset();
  }
  window.addEventListener('resize', resize); resize();
  var mx = 0, my = 0, tmx = 0, tmy = 0;
  window.addEventListener('pointermove', function (e) { tmx = e.clientX / window.innerWidth - 0.5; tmy = e.clientY / window.innerHeight - 0.5; }, {passive: true});

  var boost = 0, clock = 0, last = performance.now(), running = true;
  window.HugoScene = {
    update: function (d) { for (var k in d) state[k] = d[k]; draw(); },
    boost: function () { boost = 1; scanY = 0; }
  };

  function frameFn(now) {
    var dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!reduce) clock += dt;
    var t = clock;
    if (scanY >= 0) { scanY += dt * TH * 0.9; if (scanY > TH + 80) scanY = boost > 0.2 ? 0 : -1; draw(); }
    boost = Math.max(0, boost - dt * 0.25);
    docGroup.position.set(0, 0.3 + Math.sin(t * 0.8) * 0.12, 0);
    docGroup.rotation.set(-0.12 + my * 0.15, -0.38 + Math.sin(t * 0.25) * 0.12 + mx * 0.4, 0.03);
    tools.position.copy(docGroup.position);
    ruler.position.set(Math.cos(t * 0.35) * 3.2, -1.6 + Math.sin(t * 0.7) * 0.3, Math.sin(t * 0.35) * 2.2);
    ruler.rotation.set(0.4, t * 0.35 + 1.2, Math.sin(t * 0.5) * 0.3);
    pencil.position.set(Math.cos(t * 0.35 + 2.6) * 3.0, 1.6 + Math.sin(t * 0.9) * 0.25, Math.sin(t * 0.35 + 2.6) * 2.0);
    pencil.rotation.set(0.6, t * 0.5, 0.9);
    coins.forEach(function (c, j) {
      var a = c.userData.a + t * 0.25 * (1 + boost * 3);
      c.position.set(Math.cos(a) * c.userData.r, c.userData.y + Math.sin(t + j) * 0.2, Math.sin(a) * c.userData.r * 0.7);
      c.rotation.set(Math.PI / 2, 0, t * 1.5 + j);
    });
    gold.intensity = 1.2 + boost * 2.5;
    dust.rotation.y = t * 0.02;
    mx += (tmx - mx) * 0.05; my += (tmy - my) * 0.05;
    camera.position.set(Math.sin(0.25 + mx * 0.3) * 13, 2.2 - my * 1.5, Math.cos(0.25 + mx * 0.3) * 13);
    camera.lookAt(0, 0.2, 0);
    renderer.render(scene, camera);
  }
  function loop(now) { if (!running) return; frameFn(now); requestAnimationFrame(loop); }
  document.addEventListener('visibilitychange', function () { running = !document.hidden; if (running) { last = performance.now(); requestAnimationFrame(loop); } });
  if (reduce) { frameFn(performance.now()); window.HugoScene.update = function (d) { for (var k in d) state[k] = d[k]; draw(); frameFn(performance.now()); }; }
  else requestAnimationFrame(loop);
})();
