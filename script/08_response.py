"""recorded.wav を文字起こしし、Responses API で回答を生成する（先に 04_record.py を実行）。

使い方: python script/08_response.py [テキスト]  （テキストを渡すと文字起こしを省略）
"""

import sys

from common import (
    LLM_MODEL,
    RECORDED_PATH,
    REACHY_MINI_SYSTEM_PROMPT,
    get_client,
    transcribe,
)

client = get_client()

text = " ".join(sys.argv[1:]) or transcribe(client, RECORDED_PATH)
print(f"あなた: {text}")

response = client.responses.create(
    model=LLM_MODEL,
    instructions=REACHY_MINI_SYSTEM_PROMPT,
    input=text,
)
print(f"Reachy Mini: {response.output_text}")
