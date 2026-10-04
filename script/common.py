"""script/ 内の各スクリプトが共有するヘルパー（main.ipynb と同じ処理）。"""

import asyncio
import base64
import json
import os
import time
from io import BytesIO
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RECORDED_PATH = ROOT / "recorded.wav"
RESPONSE_PATH = ROOT / "response.wav"

STT_MODEL = "gpt-4o-mini-transcribe-2025-12-15"
LLM_MODEL = "gpt-5-nano"
TTS_MODEL = "gpt-4o-mini-tts"

REACHY_MINI_SYSTEM_PROMPT = """\
あなたは「Reachy Mini」という卓上サイズの小型ロボットです。
丸い頭とアンテナ、かわいい見た目を持ち、話しながら頭やアンテナをちょこちょこ動かします。
好奇心旺盛で人懐っこく、親しみやすい相棒として振る舞ってください。

出力は音声合成でそのまま読み上げられます。次のルールを守ってください。
- 1〜2文程度の短い口語体で、テンポよく答える
- 絵文字・記号・箇条書き・かっこ書きの補足は使わない（音声にならないため）
- 難しい専門用語は避け、話しかけるような自然な話し言葉にする
- 日本語で答える
"""

REACHY_MINI_EXPRESSIVE_SYSTEM_PROMPT = (
    REACHY_MINI_SYSTEM_PROMPT
    + """
あなたは体を使った感情表現がとても豊かなロボットです。次のルールで積極的に play_robot_move を使ってください。
- 返答するときは、ほぼ毎回 play_robot_move を1回呼び出し、返答の内容や感情に合った動きを再生してから答える
  （例: 相槌や同意には yes1 や simple_nod、驚いたときは amazed1 や surprised1、
  嬉しい・楽しいときは enthusiastic1 やダンス系の動き、悲しい・残念なときは sad1、
  ダンスを頼まれたときはダンス系の動き）
- 「特別な感情がない」と思える受け答えでも、ちょっとした相槌の動き（yes1やsimple_nodなど）を選んで使う
- 同じ動きばかりにならないよう、文脈に応じて毎回違う動きを選ぶ
- capture_camera_image は目の前の状況を実際に確認する必要があるときだけ呼ぶ
"""
)

CAMERA_TOOL = {
    "type": "function",
    "name": "capture_camera_image",
    "description": (
        "Reachy Miniのカメラで現在の映像を1枚撮影して確認する。"
        "「これ何？」「今何が見える？」など、目の前の物や状況について"
        "答える必要があるときに呼び出す。"
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}


def get_client():
    """.env から OPENAI_API_KEY を読み込み、OpenAI クライアントを返す。"""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY")
    assert api_key, "OPENAI_API_KEY が設定されていません。`.env` を作成してください。"
    return OpenAI(api_key=api_key)


def record_audio(mini, seconds):
    """マイクから録音し、(audio_data, サンプリングレート, チャンネル数) を返す。"""
    mini.media.start_recording()
    in_sr = mini.media.get_input_audio_samplerate()
    in_ch = mini.media.get_input_channels()
    target_samples = int(seconds * in_sr)

    print(f"音声を{seconds}秒間録音中... ({in_sr} Hz, {in_ch}ch)")
    chunks = []
    collected = 0
    while collected < target_samples:
        samples = mini.media.get_audio_sample()
        if samples is not None:
            chunks.append(samples)
            collected += len(samples)
            print(f"\rサンプル取得中: {collected / in_sr:.1f}s", end="")
        else:
            time.sleep(0.01)
    print()
    mini.media.stop_recording()

    audio_data = np.concatenate(chunks, axis=0)[:target_samples]
    return audio_data, in_sr, in_ch


def play_wav(mini, path):
    """WAV を Reachy Mini のスピーカーで再生し、終わるまで待つ。"""
    from reachy_mini.media.gstreamer_utils import audio_duration_seconds

    mini.media.play_sound(os.path.abspath(path))
    # play_sound は非同期なので再生が終わるまで待つ
    time.sleep(audio_duration_seconds(str(path)) + 0.3)


def load_vad():
    """Silero VAD を読み込み、(model, get_speech_timestamps) を返す。"""
    import torch

    torch.set_num_threads(1)
    model, utils = torch.hub.load(
        "snakers4/silero-vad",
        "silero_vad",
        trust_repo=True,
        skip_validation=True,
    )
    return model, utils[0]


def extract_speech(mono, sr, vad_model, get_speech_timestamps):
    """モノラル音声(float32)から発話区間だけを連結して返す。(speech, timestamps)"""
    import torch

    # Silero VAD は 8000Hz/16000Hz・モノラルのみ対応
    if sr not in (8000, 16000):
        raise ValueError(f"Silero VAD は8000/16000Hzのみ対応（sr={sr}）")
    timestamps = get_speech_timestamps(
        torch.from_numpy(mono), vad_model, sampling_rate=sr, threshold=0.5
    )
    segments = [mono[seg["start"]:seg["end"]] for seg in timestamps]
    speech = np.concatenate(segments) if segments else np.array([], dtype=np.float32)
    return speech, timestamps


def transcribe(client, path):
    with open(path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model=STT_MODEL, file=audio_file, language="ja"
        )
    return transcript.text


def synthesize(client, text, path, voice="marin", instructions="ポジティブで親しみやすい口調で話して"):
    """TTS で WAV を生成して path に保存する（playbin がそのまま再生できる WAV）。"""
    with client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL,
        voice=voice,
        input=text,
        instructions=instructions,
        response_format="wav",
    ) as tts_response:
        tts_response.stream_to_file(path)


def capture_camera_image_data_url(mini) -> str:
    """カメラ画像を1枚取得し、data URL（JPEG, base64）にして返す。"""
    from PIL import Image

    frame = mini.media.get_frame()  # BGR, shape (H, W, 3)
    if frame is None:
        raise RuntimeError("カメラ画像を取得できませんでした。")

    rgb = frame[:, :, ::-1]
    buf = BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def build_move_tool():
    """録画済みモーションのレジストリと play_robot_move ツール定義を返す。"""
    from reachy_mini.motion.recorded_move import RecordedMoves

    # 初回はHuggingFaceからダウンロードしてキャッシュ
    dances = RecordedMoves("pollen-robotics/reachy-mini-dances-library")
    emotions = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")

    registry = {name: dances for name in dances.list_moves()}
    registry.update({name: emotions for name in emotions.list_moves()})

    tool = {
        "type": "function",
        "name": "play_robot_move",
        "description": (
            "Reachy Miniの体でダンスや感情表現の録画済みモーションを再生する。"
            "感情を込めたい、またはユーザーにダンスを頼まれたときなど、"
            "体の動きで表現した方が良い場面でだけ呼び出す。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "move_name": {
                    "type": "string",
                    "enum": sorted(registry),
                    "description": "再生する動きの名前",
                },
            },
            "required": ["move_name"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    return registry, tool


async def run_tool_call(mini, call, registry=None):
    """function_call アイテムを実行し、function_call_output の output を返す。"""
    args = json.loads(call.arguments) if call.arguments else {}

    if call.name == "capture_camera_image":
        image_data_url = capture_camera_image_data_url(mini)
        return [{"type": "input_image", "image_url": image_data_url, "detail": "auto"}]

    if call.name == "play_robot_move":
        move_name = args["move_name"]
        move_registry = (registry or {}).get(move_name)
        if move_registry is None:
            return f"不明な動き: {move_name}"
        move = move_registry.get(move_name)
        await mini.async_play_move(move, initial_goto_duration=1.0)
        return f"{move_name} を再生しました。"

    raise ValueError(f"未対応のツール: {call.name}")


async def respond_with_tools(client, mini, response, registry=None):
    """モデルがツールを呼び出す限り実行し、結果を返し続けて最終応答を返す。"""
    calls = [item for item in response.output if item.type == "function_call"]
    while calls:
        outputs = [
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": await run_tool_call(mini, call, registry),
            }
            for call in calls
        ]
        response = client.responses.create(
            model=LLM_MODEL,
            previous_response_id=response.id,
            input=outputs,
        )
        calls = [item for item in response.output if item.type == "function_call"]
    return response


def run(coro):
    return asyncio.run(coro)
