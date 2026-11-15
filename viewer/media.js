// ブラウザのマイク・スピーカー・カメラを、Codespace 内のデーモンにつなぐ（サーバー側は media_bridge.py）。
// どれもボタンを押したときに始める（ブラウザは、ユーザー操作なしに音を出したりマイクを使ったりできない）。

const MIC_RATE = 16000;
const SPEAKER_RATE = 24000;
const CAMERA_SIZE = [1280, 720];
const CAMERA_FPS = 10;

const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;

let audioContext = null;
async function getAudioContext() {
  if (!audioContext) {
    audioContext = new AudioContext();
    await audioContext.audioWorklet.addModule("/static/audio-worklets.js");
  }
  if (audioContext.state === "suspended") await audioContext.resume();
  return audioContext;
}

// ---- マイク ------------------------------------------------------------------
const mic = {
  stream: null, source: null, node: null, ws: null,
  async start(onLevel) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = await getAudioContext();
    this.ws = new WebSocket(wsUrl("/mic"));
    this.ws.binaryType = "arraybuffer";
    this.source = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, "mic-capture", { processorOptions: { rate: MIC_RATE } });
    this.node.port.onmessage = (e) => {
      onLevel(e.data.peak);
      if (this.ws.readyState === WebSocket.OPEN) this.ws.send(e.data.pcm);
    };
    this.source.connect(this.node);
    return this.ws;
  },
  stop() {
    this.source?.disconnect();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ws?.close();
    this.stream = this.source = this.node = this.ws = null;
  },
};

// ---- スピーカー --------------------------------------------------------------
const speaker = {
  node: null, ws: null,
  async start() {
    const ctx = await getAudioContext();
    this.node = new AudioWorkletNode(ctx, "speaker-player", {
      numberOfInputs: 0,
      outputChannelCount: [2],
      processorOptions: { rate: SPEAKER_RATE },
    });
    this.node.connect(ctx.destination);
    this.ws = new WebSocket(wsUrl("/speaker"));
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (e) => this.node.port.postMessage(e.data, [e.data]);
    return this.ws;
  },
  stop() {
    this.node?.disconnect();
    this.ws?.close();
    this.node = this.ws = null;
  },
};

// ---- カメラ ------------------------------------------------------------------
const camera = {
  stream: null, ws: null, timer: null,
  async start(video) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: CAMERA_SIZE[0] }, height: { ideal: CAMERA_SIZE[1] } },
    });
    video.srcObject = this.stream;
    await video.play();
    this.ws = new WebSocket(wsUrl("/cam"));
    this.ws.binaryType = "arraybuffer";

    // デーモンのカメラと同じ 1280×720 に、縦横比を保って中央を切り出す
    const canvas = document.createElement("canvas");
    [canvas.width, canvas.height] = CAMERA_SIZE;
    const g = canvas.getContext("2d");
    const send = () => {
      // 回線が詰まっているときは送らない（古い画像がたまって遅れるのを防ぐ）
      if (this.ws?.readyState !== WebSocket.OPEN || this.ws.bufferedAmount > 512 * 1024) return;
      const vw = video.videoWidth, vh = video.videoHeight;
      if (!vw || !vh) return;
      const scale = Math.max(canvas.width / vw, canvas.height / vh);
      const sw = canvas.width / scale, sh = canvas.height / scale;
      g.drawImage(video, (vw - sw) / 2, (vh - sh) / 2, sw, sh, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => blob && this.ws?.readyState === WebSocket.OPEN && this.ws.send(blob), "image/jpeg", 0.75);
    };
    this.timer = setInterval(send, 1000 / CAMERA_FPS);
    return this.ws;
  },
  stop(video) {
    clearInterval(this.timer);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ws?.close();
    video.srcObject = null;
    this.stream = this.ws = this.timer = null;
  },
};

// ---- 操作パネル --------------------------------------------------------------
const panel = document.getElementById("media");
const video = document.getElementById("camera-preview");
const meter = document.getElementById("mic-level");
const note = document.getElementById("media-note");

function bindToggle(button, device, { start, stop }) {
  // オン・オフを素早く繰り返しても、前の接続の close が今の接続を止めないよう、回ごとに番号を振る
  let session = 0;
  let on = false;
  const setUi = (state, message = "") => {
    button.dataset.state = state;
    note.textContent = message;
  };
  const turnOff = (id, message = "") => {
    if (!on || id !== session) return;
    on = false;
    stop();
    setUi("off", message);
  };
  button.addEventListener("click", async () => {
    if (button.dataset.state === "pending") return; // 許可ダイアログの表示中などは受け付けない
    if (on) return turnOff(session);
    const id = ++session;
    on = true;
    setUi("pending");
    try {
      const ws = await start();
      ws.addEventListener("open", () => id === session && on && setUi("on"));
      ws.addEventListener("close", (e) => turnOff(id, e.reason || `${device}の接続が切れました`));
    } catch (err) {
      turnOff(id, describeError(device, err));
    }
  });
}

function describeError(device, err) {
  if (!window.isSecureContext) return `${device}は https か localhost のページでしか使えません`;
  if (err?.name === "NotAllowedError") return `${device}の使用が許可されていません（アドレスバーの設定から許可してください）`;
  if (err?.name === "NotFoundError") return `${device}が見つかりません`;
  return `${device}を開始できません: ${err?.message ?? err}`;
}

const manifest = await (await fetch("/manifest.json")).json();

const micButton = document.getElementById("mic-toggle");
const speakerButton = document.getElementById("speaker-toggle");
if (manifest.audio_bridge) {
  bindToggle(micButton, "マイク", {
    start: () => mic.start((peak) => meter.style.setProperty("--level", Math.min(1, peak * 1.5).toFixed(3))),
    stop: () => {
      mic.stop();
      meter.style.setProperty("--level", 0);
    },
  });
  bindToggle(speakerButton, "スピーカー", {
    start: () => speaker.start(),
    stop: () => speaker.stop(),
  });
} else {
  // macOS などのローカル環境では、デーモンが PC のマイク・スピーカーを直接使うので中継は不要
  for (const b of [micButton, speakerButton]) {
    b.disabled = true;
    b.title = "この環境ではデーモンが PC のマイク・スピーカーを直接使います（中継は Codespaces 用）";
  }
  meter.hidden = true;
}

const cameraButton = document.getElementById("camera-toggle");
if (manifest.camera === "browser") {
  bindToggle(cameraButton, "カメラ", {
    start: async () => {
      panel.classList.add("with-video");
      return camera.start(video);
    },
    stop: () => {
      camera.stop(video);
      panel.classList.remove("with-video");
    },
  });
} else {
  cameraButton.disabled = true;
  cameraButton.title = "シミュレータのカメラを使う設定です（REACHY_MINI_CAMERA=sim）";
}

// どれも使えない環境（ローカルで普通に使う場合）では、パネルごと隠す
if ([micButton, speakerButton, cameraButton].every((b) => b.disabled)) panel.hidden = true;
