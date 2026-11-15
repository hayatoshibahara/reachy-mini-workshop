"""ブラウザ版シミュレータビューアの配信サーバー。

- ビューア本体（index.html / main.js）を配信する
- インストール済み reachy_mini パッケージの MJCF とメッシュを配信する（デーモンと必ず一致させるため）
- デーモンの状態配信 WebSocket を同一オリジンの /ws に中継する（Codespaces の転送ポート対策）
- ブラウザのマイク・スピーカー・カメラをデーモンにつなぐ（/mic・/speaker・/cam。media_bridge.py）

使い方:
    python viewer/server.py [--port 8080] [--daemon localhost:8000] [--camera browser|sim]
"""

import argparse
import logging
from importlib.resources import files
from pathlib import Path

import aiohttp
from aiohttp import web

import reachy_mini
from media_bridge import setup as setup_media

VIEWER_DIR = Path(__file__).parent
MJCF_DIR = Path(str(files(reachy_mini).joinpath("descriptions/reachy_mini/mjcf")))
MJCF_EXTENSIONS = {".xml", ".stl", ".obj", ".png"}

# デーモンが ctrl に入れている目標値だけを受け取る（backend/mujoco/backend.py の制御ループと同じ値）
STATE_QUERY = (
    "frequency=30"
    "&with_head_pose=false&with_body_yaw=false&with_antenna_positions=false"
    "&with_target_head_joints=true&with_target_antenna_positions=true"
)


def mjcf_manifest() -> list[str]:
    """ブラウザの仮想ファイルシステムに書き込むファイルの一覧。"""
    return sorted(
        p.relative_to(MJCF_DIR).as_posix()
        for p in MJCF_DIR.rglob("*")
        if p.is_file() and p.suffix in MJCF_EXTENSIONS
    )


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(VIEWER_DIR / "index.html")


async def manifest(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "scene": request.app["scene"],
            "reachy_mini_version": reachy_mini.__version__
            if hasattr(reachy_mini, "__version__")
            else None,
            "files": mjcf_manifest(),
            "camera": request.app["camera"],
            "audio_bridge": request.app["audio_bridge"],
        }
    )


async def state_ws(request: web.Request) -> web.WebSocketResponse:
    """デーモンの /api/state/ws/full をそのまま中継する。"""
    browser = web.WebSocketResponse(heartbeat=10)
    await browser.prepare(request)

    url = f"ws://{request.app['daemon']}/api/state/ws/full?{STATE_QUERY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as daemon:
                async for msg in daemon:
                    if msg.type != aiohttp.WSMsgType.TEXT or browser.closed:
                        break
                    await browser.send_str(msg.data)
    except (aiohttp.ClientError, ConnectionResetError) as e:
        if not browser.closed:
            await browser.close(code=1011, message=f"daemon: {e}".encode()[:120])
    return browser


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--daemon", default="localhost:8000", help="デーモンの host:port")
    parser.add_argument("--scene", default="minimal", help="デーモンと同じシーン名")
    parser.add_argument(
        "--camera",
        choices=["browser", "sim"],
        default="sim",
        help="browser: ブラウザのカメラを UDP 5005 に流す（デーモンを --headless で起動したとき）。"
        "sim: シミュレータのカメラをそのまま使う（既定。5005 には何も送らない）",
    )
    parser.add_argument("--mic-sink", default="browser_mic", help="マイク音声を流す PulseAudio の sink")
    parser.add_argument(
        "--speaker-source", default="speaker.monitor", help="デーモンの再生音を拾う PulseAudio の source"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    app = web.Application()
    app["daemon"] = args.daemon
    app["scene"] = args.scene
    app.router.add_get("/", index)
    app.router.add_static("/static", VIEWER_DIR)
    app.router.add_get("/manifest.json", manifest)
    app.router.add_static("/mjcf", MJCF_DIR)
    app.router.add_get("/ws", state_ws)
    setup_media(app, args.camera, args.mic_sink, args.speaker_source)

    print(f"ビューア: http://localhost:{args.port}  （デーモン: {args.daemon}、カメラ: {args.camera}）")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
