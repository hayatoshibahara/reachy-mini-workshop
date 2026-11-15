"""ブラウザのマイク・スピーカー・カメラを、Codespace 内のデーモンにつなぐ中継処理。

デーモンや SDK には手を入れず、デーモンが既定で使うデバイスの側を差し替える。

- マイク:   ブラウザ → /mic（16kHz・16bit・モノラル PCM）→ pacat → PulseAudio の null sink
            "browser_mic"。そのモニターを既定の入力にしておくと、デーモンはそこから録音する
- スピーカー: デーモンの再生先（既定の出力 = null sink "speaker"）のモニター → parec
            → /speaker（24kHz・16bit・モノラル PCM）→ ブラウザで再生
- カメラ:   ブラウザ → /cam（1280×720 の JPEG）→ GStreamerUDPCamera → UDP 5005
            （シミュレータのカメラと同じ経路。デーモンは --headless で起動し、MuJoCo の
            カメラ描画を止めておく）

PulseAudio のデバイスは .devcontainer/start.sh で用意する。
"""

import asyncio
import io
import logging
import shutil
import threading
import time

import numpy as np
from aiohttp import web

logger = logging.getLogger(__name__)

MIC_RATE = 16000
SPEAKER_RATE = 24000
SPEAKER_CHUNK_BYTES = SPEAKER_RATE // 50 * 2  # 20ms
CAMERA_SIZE = (1280, 720)  # デーモンのシミュレータ用カメラ設定（MujocoCameraSpecs）と同じ
CAMERA_FPS = 15
CAMERA_IDLE_SECONDS = 2.0  # これ以上ブラウザから画像が来なければ黒画像に戻す


NO_PULSEAUDIO = "PulseAudio（pacat / parec）が見つかりません。音声の中継は Codespaces（Linux）専用です"


async def mic_ws(request: web.Request) -> web.WebSocketResponse:
    """ブラウザから届く PCM を PulseAudio の browser_mic に流し込む。"""
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)

    try:
        proc = await asyncio.create_subprocess_exec(
            "pacat", "--playback", "--raw",
            f"--device={request.app['mic_sink']}",
            "--format=s16le", f"--rate={MIC_RATE}", "--channels=1",
            "--latency-msec=40",
            stdin=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        await ws.close(code=4001, message=NO_PULSEAUDIO.encode())
        return ws
    logger.info("マイク: 接続")
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.BINARY:
                continue
            proc.stdin.write(msg.data)
            await proc.stdin.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        proc.stdin.close()
        await _stop(proc)
        logger.info("マイク: 切断")
    return ws


async def speaker_ws(request: web.Request) -> web.WebSocketResponse:
    """デーモンが再生した音（speaker のモニター）をブラウザに送る。"""
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)

    try:
        proc = await asyncio.create_subprocess_exec(
            "parec", "--raw",
            f"--device={request.app['speaker_source']}",
            "--format=s16le", f"--rate={SPEAKER_RATE}", "--channels=1",
            "--latency-msec=20",
            stdout=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        await ws.close(code=4001, message=NO_PULSEAUDIO.encode())
        return ws
    logger.info("スピーカー: 接続")

    async def pump() -> None:
        while not ws.closed:
            chunk = await proc.stdout.read(SPEAKER_CHUNK_BYTES)
            if not chunk:
                break
            await ws.send_bytes(chunk)

    pump_task = asyncio.create_task(pump())
    try:
        async for _ in ws:  # ブラウザからは何も来ない。切断を待つだけ
            pass
    finally:
        pump_task.cancel()
        await _stop(proc)
        logger.info("スピーカー: 切断")
    return ws


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.kill()


class CameraBridge:
    """最新の画像を一定の間隔で UDP 5005 に送り続ける。

    デーモンのメディアサーバーは映像が途切れない前提なので、ブラウザのカメラが
    オフのあいだは黒画像を送る（06_camera.py は「映像が届いていません」と表示する）。
    """

    def __init__(self) -> None:
        # GStreamer（gi）はカメラを中継するときだけ読み込む
        from reachy_mini.media.gstreamer_udp_camera import GStreamerUDPCamera

        w, h = CAMERA_SIZE
        self._black = np.zeros((h, w, 3), np.uint8)
        self._frame = self._black
        self._last_update = 0.0
        self._lock = threading.Lock()
        self._sender = GStreamerUDPCamera(width=w, height=h)
        self._sender.start()
        threading.Thread(target=self._loop, daemon=True).start()

    def update_jpeg(self, data: bytes) -> None:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        if img.size != CAMERA_SIZE:
            img = img.resize(CAMERA_SIZE)
        frame = np.asarray(img, dtype=np.uint8)
        with self._lock:
            self._frame = frame
            self._last_update = time.monotonic()

    def _loop(self) -> None:
        interval = 1 / CAMERA_FPS
        while True:
            start = time.monotonic()
            with self._lock:
                if start - self._last_update > CAMERA_IDLE_SECONDS:
                    self._frame = self._black
                frame = self._frame
            try:
                self._sender.send_frame(frame)
            except Exception:  # 1 枚の失敗で中継を止めない
                logger.exception("カメラ: UDP への送信に失敗")
            time.sleep(max(0.0, interval - (time.monotonic() - start)))


async def cam_ws(request: web.Request) -> web.WebSocketResponse:
    """ブラウザから届く JPEG をカメラ映像としてデーモンに渡す。"""
    ws = web.WebSocketResponse(heartbeat=10, max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)

    bridge: CameraBridge | None = request.app["camera_bridge"]
    if bridge is None:
        await ws.close(code=4000, message="シミュレータのカメラを使う設定です".encode())
        return ws

    loop = asyncio.get_running_loop()
    logger.info("カメラ: 接続")
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.BINARY:
                continue
            try:
                await loop.run_in_executor(None, bridge.update_jpeg, msg.data)
            except Exception:
                logger.exception("カメラ: 画像を読み込めませんでした")
    finally:
        logger.info("カメラ: 切断")
    return ws


def setup(app: web.Application, camera: str, mic_sink: str, speaker_source: str) -> None:
    """/mic・/speaker・/cam をアプリに登録する。camera は "browser" か "sim"。"""
    app["camera"] = camera
    # 音声の中継は PulseAudio が前提（Codespaces）。macOS などではデーモンが PC のデバイスを直接使う
    app["audio_bridge"] = bool(shutil.which("pacat") and shutil.which("parec"))
    app["mic_sink"] = mic_sink
    app["speaker_source"] = speaker_source
    app["camera_bridge"] = CameraBridge() if camera == "browser" else None
    app.router.add_get("/mic", mic_ws)
    app.router.add_get("/speaker", speaker_ws)
    app.router.add_get("/cam", cam_ws)
