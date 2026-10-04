"""シミュレーション（または実機）に接続し、頭とアンテナを動かす。"""

from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

# localhost で実行中のシミュレーションに接続
with ReachyMini(media_backend="default") as mini:
    print("シミュレーションに接続しました！")

    # 見上げて首を傾ける
    print("頭部を移動中...")
    mini.goto_target(
        head=create_head_pose(z=20, roll=10, mm=True, degrees=True),
        duration=1.0,
    )

    # アンテナを動かす
    print("アンテナを動かしています...")
    mini.goto_target(antennas=[0.6, -0.6], duration=0.3)
    mini.goto_target(antennas=[-0.6, 0.6], duration=0.3)

    # 初期位置に戻す
    mini.goto_target(
        head=create_head_pose(),
        antennas=[0, 0],
        duration=1.0,
    )
    print("初期位置に戻しました！")
