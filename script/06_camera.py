"""カメラで1枚撮影して表示する。"""

import matplotlib.pyplot as plt
from reachy_mini import ReachyMini

with ReachyMini(media_backend="default") as mini:
    frame = mini.media.get_frame()

plt.imshow(frame[:, :, ::-1])  # BGR -> RGB に変換して表示
plt.axis("off")
plt.title(f"frame {frame.shape}, {frame.dtype}")
plt.show()
