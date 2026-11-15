// ブラウザ版シミュレータビューア
//
// デーモンと同じ MJCF を WASM 版 MuJoCo に読み込み、デーモンが ctrl に入れている目標値
// （target_head_joints / target_antennas_position）を WebSocket で受け取って同じ式で ctrl に入れ、
// ブラウザ側でも mj_step を回す「影のシミュレーション」。パラレルリンクの受動関節は
// equality connect によって追従する。

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import loadMujoco from "mujoco";

// reachy_mini/reachy_mini.py と daemon/backend/mujoco/backend.py の初期姿勢
const SLEEP_HEAD_JOINT_POSITIONS = [
  0, -0.9848156658225817, 1.2624661884298831, -0.24390294527381684,
  0.20555342557667577, -1.2363885150358267, 1.0032234352772091,
];
const SLEEP_ANTENNAS_JOINT_POSITIONS_MJ = [3.05, -3.05]; // MuJoCo 側は左右が逆
const TIMESTEP = 0.002; // デーモンと同じ 500Hz
const MAX_STEPS_PER_FRAME = 50;

const statusEl = document.getElementById("status");
const state = { phase: "読み込み中", connected: false, messages: 0, rate: 0, fps: 0 };

function renderStatus() {
  const conn = state.connected
    ? `<span class="ok">● デーモンに接続中</span>（${state.rate.toFixed(0)} msg/s）`
    : `<span class="ng">● デーモン未接続</span>（再接続を試行中）`;
  statusEl.innerHTML = `${state.phase}\n${conn}\n描画 ${state.fps.toFixed(0)} fps`;
}

// ---- MuJoCo の読み込み ------------------------------------------------------

const mujoco = await loadMujoco();
const manifest = await (await fetch("/manifest.json")).json();

state.phase = `モデルを取得中（${manifest.files.length} ファイル）`;
renderStatus();

await Promise.all(
  manifest.files.map(async (path) => {
    const buf = new Uint8Array(await (await fetch(`/mjcf/${path}`)).arrayBuffer());
    const dir = `/mjcf/${path}`.split("/").slice(0, -1).join("/");
    mujoco.FS.mkdirTree(dir);
    mujoco.FS.writeFile(`/mjcf/${path}`, buf);
  }),
);

const model = mujoco.MjModel.from_xml_path(`/mjcf/scenes/${manifest.scene}.xml`);
const data = new mujoco.MjData(model);
model.opt.timestep = TIMESTEP;

initializeLikeDaemon();

// ---- three.js のシーン ------------------------------------------------------

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x4d7fb3);
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.8);
sun.position.set(0.5, -1, 3);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 50);
camera.up.set(0, 0, 1); // MuJoCo は Z 軸が上
const controls = new OrbitControls(camera, renderer.domElement);
placeCamera();

const geomObjects = buildGeoms();

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

state.phase = `シーン: ${manifest.scene}`;
window.reachyViewer = { mujoco, model, data }; // デバッグ用（DevTools から参照）
connect();
requestAnimationFrame(loop);

// ---- 関数 -------------------------------------------------------------------

// デーモンの MujocoBackend.run() の起動手順をなぞる
function initializeLikeDaemon() {
  const initial = [...SLEEP_HEAD_JOINT_POSITIONS, ...SLEEP_ANTENNAS_JOINT_POSITIONS_MJ];
  for (let i = 0; i < model.nu; i++) {
    const jointId = model.actuator_trnid[i * 2];
    data.qpos[model.jnt_qposadr[jointId]] = initial[i];
    data.ctrl[i] = initial[i];
  }

  // 名前のない（ロボットの）衝突形状は、初期化中だけ衝突を切る
  const disabled = [];
  for (let g = 0; g < model.ngeom; g++) {
    if (model.geom_contype[g] !== 0 && !mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM.value, g)) {
      disabled.push(g);
      model.geom_contype[g] = 0;
      model.geom_conaffinity[g] = 0;
    }
  }
  mujoco.mj_forward(model, data);
  for (let i = 0; i < 100; i++) mujoco.mj_step(model, data);
  for (const g of disabled) {
    model.geom_contype[g] = 1;
    model.geom_conaffinity[g] = 1;
  }
  for (let i = 0; i < 101; i++) mujoco.mj_step(model, data);
}

function applyTargets(msg) {
  const head = msg.target_head_joints;
  const antennas = msg.target_antennas_position;
  if (head) for (let i = 0; i < 7; i++) data.ctrl[i] = head[i];
  if (antennas) {
    // デーモンと同じく ctrl[-2:] = -target_antenna_joint_positions
    data.ctrl[model.nu - 2] = -antennas[0];
    data.ctrl[model.nu - 1] = -antennas[1];
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { state.connected = true; renderStatus(); };
  ws.onmessage = (event) => {
    state.messages++;
    applyTargets(JSON.parse(event.data));
  };
  ws.onclose = () => {
    state.connected = false;
    renderStatus();
    setTimeout(connect, 2000);
  };
}

function placeCamera() {
  const headSite = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE.value, "head");
  const target = headSite >= 0
    ? new THREE.Vector3(...data.site_xpos.slice(headSite * 3, headSite * 3 + 3))
    : new THREE.Vector3(0, 0, 0.2);
  target.z -= 0.08;
  // MJCF の <global azimuth="160" elevation="-20"> に合わせる
  const az = THREE.MathUtils.degToRad(160);
  const el = THREE.MathUtils.degToRad(-20);
  const dist = 1.0;
  const forward = new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el));
  camera.position.copy(target).addScaledVector(forward, -dist);
  controls.target.copy(target);
  controls.update();
}

function buildGeoms() {
  const GEOM = { PLANE: 0, SPHERE: 2, CAPSULE: 3, ELLIPSOID: 4, CYLINDER: 5, BOX: 6, MESH: 7 };
  const textures = new Map();
  const objects = [];

  for (let g = 0; g < model.ngeom; g++) {
    if (model.geom_group[g] > 2) continue; // group 3 は衝突用（MuJoCo のビューアと同じく非表示）

    const matId = model.geom_matid[g];
    const rgba = matId >= 0
      ? model.mat_rgba.slice(matId * 4, matId * 4 + 4)
      : model.geom_rgba.slice(g * 4, g * 4 + 4);
    if (rgba[3] === 0) continue;

    const size = model.geom_size.slice(g * 3, g * 3 + 3);
    let geometry;
    switch (model.geom_type[g]) {
      case GEOM.PLANE:
        geometry = new THREE.PlaneGeometry(size[0] > 0 ? size[0] * 2 : 10, size[1] > 0 ? size[1] * 2 : 10);
        break;
      case GEOM.SPHERE:
        geometry = new THREE.SphereGeometry(size[0], 24, 16);
        break;
      case GEOM.ELLIPSOID:
        geometry = new THREE.SphereGeometry(1, 24, 16).scale(size[0], size[1], size[2]);
        break;
      case GEOM.CAPSULE:
        geometry = new THREE.CapsuleGeometry(size[0], size[1] * 2, 8, 16).rotateX(Math.PI / 2);
        break;
      case GEOM.CYLINDER:
        geometry = new THREE.CylinderGeometry(size[0], size[0], size[1] * 2, 24).rotateX(Math.PI / 2);
        break;
      case GEOM.BOX:
        geometry = new THREE.BoxGeometry(size[0] * 2, size[1] * 2, size[2] * 2);
        break;
      case GEOM.MESH:
        geometry = meshGeometry(model.geom_dataid[g]);
        break;
      default:
        continue;
    }

    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(rgba[0], rgba[1], rgba[2]),
      transparent: rgba[3] < 1,
      opacity: rgba[3],
      roughness: 0.7,
      metalness: 0.0,
    });
    if (matId >= 0 && geometry.getAttribute("uv")) {
      const texId = model.mat_texid[matId * mujoco.mjtTextureRole.mjNTEXROLE.value + mujoco.mjtTextureRole.mjTEXROLE_RGB.value];
      if (texId >= 0) {
        if (!textures.has(texId)) textures.set(texId, makeTexture(texId));
        material.map = textures.get(texId);
      }
    }

    const mesh = new THREE.Mesh(geometry, material);
    mesh.matrixAutoUpdate = false;
    scene.add(mesh);
    objects.push({ g, mesh });
  }
  return objects;
}

// MuJoCo のメッシュは頂点・法線・テクスチャ座標ごとに別のインデックスを持つので、展開して作る
function meshGeometry(m) {
  const vertAdr = model.mesh_vertadr[m];
  const normalAdr = model.mesh_normaladr[m];
  const texAdr = model.mesh_texcoordadr[m];
  const faceAdr = model.mesh_faceadr[m];
  const faceNum = model.mesh_facenum[m];
  const { mesh_vert: vert, mesh_normal: normal, mesh_texcoord: texcoord } = model;
  const { mesh_face: face, mesh_facenormal: faceNormal, mesh_facetexcoord: faceTex } = model;

  const position = new Float32Array(faceNum * 9);
  const normals = new Float32Array(faceNum * 9);
  const uv = texAdr >= 0 ? new Float32Array(faceNum * 6) : null;
  for (let f = 0; f < faceNum; f++) {
    for (let k = 0; k < 3; k++) {
      const c = f * 3 + k;
      const vi = (vertAdr + face[(faceAdr + f) * 3 + k]) * 3;
      const ni = (normalAdr + faceNormal[(faceAdr + f) * 3 + k]) * 3;
      position.set([vert[vi], vert[vi + 1], vert[vi + 2]], c * 3);
      normals.set([normal[ni], normal[ni + 1], normal[ni + 2]], c * 3);
      if (uv) {
        const ti = (texAdr + faceTex[(faceAdr + f) * 3 + k]) * 2;
        uv[c * 2] = texcoord[ti];
        uv[c * 2 + 1] = texcoord[ti + 1];
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
  if (uv) geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return geometry;
}

function makeTexture(t) {
  const width = model.tex_width[t];
  const height = model.tex_height[t];
  const channels = model.tex_nchannel[t];
  const src = model.tex_data.subarray(model.tex_adr[t], model.tex_adr[t] + width * height * channels);
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    rgba[i * 4] = src[i * channels];
    rgba[i * 4 + 1] = src[i * channels + (channels > 1 ? 1 : 0)];
    rgba[i * 4 + 2] = src[i * channels + (channels > 2 ? 2 : 0)];
    rgba[i * 4 + 3] = channels === 4 ? src[i * 4 + 3] : 255;
  }
  const texture = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.needsUpdate = true;
  return texture;
}

function syncGeoms() {
  const pos = data.geom_xpos;
  const mat = data.geom_xmat;
  for (const { g, mesh } of geomObjects) {
    const p = g * 3;
    const r = g * 9;
    mesh.matrix.set(
      mat[r], mat[r + 1], mat[r + 2], pos[p],
      mat[r + 3], mat[r + 4], mat[r + 5], pos[p + 1],
      mat[r + 6], mat[r + 7], mat[r + 8], pos[p + 2],
      0, 0, 0, 1,
    );
  }
}

let lastTime = performance.now();
let statTime = lastTime;
let frames = 0;

function loop(now) {
  // 経過した実時間ぶんだけシミュレーションを進める（遅れたら追いつくのを諦める）
  const dt = Math.min((now - lastTime) / 1000, MAX_STEPS_PER_FRAME * TIMESTEP);
  lastTime = now;
  const steps = Math.round(dt / TIMESTEP);
  for (let i = 0; i < steps; i++) mujoco.mj_step(model, data);

  syncGeoms();
  controls.update();
  renderer.render(scene, camera);

  frames++;
  if (now - statTime > 1000) {
    state.fps = (frames * 1000) / (now - statTime);
    state.rate = (state.messages * 1000) / (now - statTime);
    frames = 0;
    state.messages = 0;
    statTime = now;
    renderStatus();
  }
  requestAnimationFrame(loop);
}
