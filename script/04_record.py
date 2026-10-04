"""音声を録音して検証し、recorded.wav に保存する。"""

import numpy as np
import soundfile as sf
from reachy_mini import ReachyMini

from common import RECORDED_PATH, record_audio

RECORD_SECONDS = 5


def to_db(v):
    return 20 * np.log10(v) if v > 0 else float("-inf")


with ReachyMini(media_backend="default") as mini:
    audio_data, in_sr, in_ch = record_audio(mini, RECORD_SECONDS)

print(f"Recorded audio: {len(audio_data)} samples at {in_sr} Hz, {in_ch}ch")

# 録音データの検証
x = audio_data.astype(np.float64)
peak = float(np.abs(x).max())
rms = float(np.sqrt(np.mean(x**2)))

print(f"shape       : {audio_data.shape}   dtype: {audio_data.dtype}")
print(f"長さ         : {len(x) / in_sr:.2f} 秒")
print(f"ピーク       : {peak:.6f}  ({to_db(peak):+.1f} dBFS)")
print(f"RMS          : {rms:.6f}  ({to_db(rms):+.1f} dBFS)")
print(f"非ゼロ比率   : {np.count_nonzero(x) / x.size:.1%}")
print(f"NaN / Inf    : {np.isnan(x).any()} / {np.isinf(x).any()}")

for ch in range(x.shape[1]):
    c = x[:, ch]
    print(f"  ch{ch}: peak={np.abs(c).max():.6f}  rms={np.sqrt(np.mean(c**2)):.6f}")

print()
if np.isnan(x).any() or np.isinf(x).any():
    print("❌ NaN / Inf が含まれています")
elif peak == 0.0:
    print("❌ 完全な無音（全サンプルが厳密に 0）→ マイクからデータが届いていません")
elif peak < 1e-3:
    print("⚠️  ほぼ無音（-60 dBFS 未満）→ ゲイン不足か別デバイスの可能性")
elif peak >= 1.0:
    print("⚠️  クリップしています（音が割れます）")
else:
    print("✅ 音声が録れています")

# 録音時のサンプリングレートで保存
sf.write(str(RECORDED_PATH), audio_data, in_sr)
print(f"保存しました: {RECORDED_PATH}")
print("💡 録音に失敗する場合、macOSでは「システム設定 → プライバシーとセキュリティ → マイク」で"
      "使用中のターミナル/エディタをオフ → オンにして再起動する。")
