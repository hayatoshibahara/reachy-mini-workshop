"""会話デモ（録音 → VAD → STT → Responses API → TTS）を複数ターン繰り返す。

感情表現豊かなシステムプロンプトを使い、Head Wobbling（喋っている間の頭の揺れ）も有効化する。
"""

import numpy as np
import soundfile as sf
from reachy_mini import ReachyMini

from common import (
    CAMERA_TOOL,
    LLM_MODEL,
    REACHY_MINI_EXPRESSIVE_SYSTEM_PROMPT,
    ROOT,
    build_move_tool,
    extract_speech,
    get_client,
    load_vad,
    play_wav,
    record_audio,
    respond_with_tools,
    run,
    synthesize,
    transcribe,
)

N_TURNS = 3
RECORD_SECONDS = 4

client = get_client()
vad_model, get_speech_timestamps = load_vad()
registry, play_move_tool = build_move_tool()


async def main(mini):
    # 以降 play_sound で再生する音声に合わせて頭が揺れる
    mini.enable_wobbling()
    print("Head wobbling を有効化しました。")

    previous_response_id = None

    for turn in range(1, N_TURNS + 1):
        print(f"\n=== ターン {turn}/{N_TURNS} ===")

        # 1. 録音
        print(f"{RECORD_SECONDS}秒間、話しかけてください...")
        turn_audio, in_sr, _ = record_audio(mini, RECORD_SECONDS)
        turn_mono = turn_audio.mean(axis=1).astype(np.float32)

        # 2. VAD で発話区間だけ抽出
        turn_speech, timestamps = extract_speech(turn_mono, in_sr, vad_model, get_speech_timestamps)
        if not timestamps:
            print("発話が検出されませんでした。このターンはスキップします。")
            continue

        turn_wav_path = ROOT / f"turn_{turn}.wav"
        sf.write(str(turn_wav_path), turn_speech, in_sr)

        # 3. STT
        text = transcribe(client, turn_wav_path)
        print(f"あなた: {text}")

        # 4. Responses API（previous_response_id で過去の会話を積み上げる）
        response = client.responses.create(
            model=LLM_MODEL,
            instructions=REACHY_MINI_EXPRESSIVE_SYSTEM_PROMPT,
            input=text,
            previous_response_id=previous_response_id,
            tools=[CAMERA_TOOL, play_move_tool],
        )
        response = await respond_with_tools(client, mini, response, registry)
        previous_response_id = response.id
        print(f"Reachy Mini: {response.output_text}")

        # 5. TTS + 再生
        turn_speech_path = ROOT / f"turn_{turn}_response.wav"
        synthesize(
            client, response.output_text, turn_speech_path, voice="marin",
            instructions="明るく元気に、少し早口で話してください。ささやくような優しいトーンで。",
        )
        play_wav(mini, turn_speech_path)

    print("\nデモ終了")


with ReachyMini(media_backend="default") as mini:
    run(main(mini))
