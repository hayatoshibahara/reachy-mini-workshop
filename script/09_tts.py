"""テキストから音声を生成し、Reachy Mini のスピーカーで再生する。

使い方: python script/09_tts.py [テキスト]
"""

import sys

from reachy_mini import ReachyMini

from common import RESPONSE_PATH, get_client, play_wav, synthesize

text = " ".join(sys.argv[1:]) or "こんにちは、僕はReachy Miniです。よろしくね！"

client = get_client()

# 声の種類（marin と cedar が公式の推奨）
# alloy ash ballad coral echo fable nova onyx sage shimmer verse marin cedar
synthesize(client, text, RESPONSE_PATH, voice="marin",
           instructions="ポジティブで親しみやすい口調で話して")
print(f"生成しました: {RESPONSE_PATH}")

with ReachyMini(media_backend="default") as mini:
    play_wav(mini, RESPONSE_PATH)
