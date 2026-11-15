"""カメラで1枚撮影し、正常に撮影できたかを表示する（ヘッドレスでも動作）。"""

import numpy as np
from reachy_mini import ReachyMini

from common import get_frame

with ReachyMini(media_backend="default") as mini:
    frame = get_frame(mini)

if frame is None:
    print("❌ カメラ画像を取得できませんでした（None）")
    raise SystemExit(1)

h, w, c = frame.shape
print(f"shape      : {frame.shape}  (高さ {h} x 幅 {w}, {c}ch)")
print(f"dtype      : {frame.dtype}")
print(f"画素値     : min={frame.min()}  max={frame.max()}  mean={frame.mean():.1f}")
print(f"ch別の平均 : B={frame[:, :, 0].mean():.1f}  G={frame[:, :, 1].mean():.1f}  R={frame[:, :, 2].mean():.1f}")

if not np.any(frame):
    print("❌ 完全な黒画像（全画素が 0）→ カメラから映像が届いていません")
elif frame.std() < 1.0:
    print("⚠️  ほぼ単色の画像 → レンズが覆われているか、別デバイスの可能性")
else:
    print("✅ 正常に撮影されました")
