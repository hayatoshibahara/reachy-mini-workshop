"""recorded.wav を gpt-4o-transcribe 系モデルでテキストに変換する（先に 04_record.py を実行）。"""

from common import RECORDED_PATH, get_client, transcribe

client = get_client()
print(transcribe(client, RECORDED_PATH))
