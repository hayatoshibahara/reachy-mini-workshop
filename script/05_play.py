"""recorded.wav を Reachy Mini のスピーカーで再生する（先に 04_record.py を実行）。"""

import soundfile as sf
from reachy_mini import ReachyMini

from common import RECORDED_PATH, play_wav

info = sf.info(str(RECORDED_PATH))

with ReachyMini(media_backend="default") as mini:
    print(f"再生中... ({info.duration:.1f}s, {info.samplerate} Hz)")
    play_wav(mini, RECORDED_PATH)
    print("再生完了")
