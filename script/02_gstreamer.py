"""GStreamer の初期化と、必要なプラグインの確認。"""

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)
print(Gst.version_string())

REQUIRED = {
    "webrtcsink":        "WebRTC配信（daemon → 遠隔クライアント）",
    "webrtcbin":         "WebRTC下位実装",
    "webrtcdsp":         "エコーキャンセル(AEC)",
    "webrtcechoprobe":   "AEC参照信号プローブ",
    "unixfdsink":        "IPC送信（LOCALバックエンド, mac/Linux）",
    "unixfdsrc":         "IPC受信（LOCALバックエンド, mac/Linux）",
    "equalizer-10bands": "スピーカーEQ",
    "audiodynamic":      "EQ後のリミッタ",
    "appsink":           "GStreamer → numpy",
    "appsrc":            "numpy → GStreamer",
    "jpegdec":           "USBカメラのMJPEGデコード",
}

for name, desc in REQUIRED.items():
    f = Gst.ElementFactory.find(name)
    mark = "✅" if f else "❌"
    plugin = f" (plugin: {f.get_plugin_name()})" if f else ""
    print(f"{mark} {name:<18} {desc}{plugin}")
