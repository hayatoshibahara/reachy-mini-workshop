# VAD連動モーション: 発話中は傾聴系、無音時は待機系の記録済みモーションを再生
#
# - VAD: Silero VAD (torch.hub) でロボットのマイク入力を監視
# - モーション: pollen-robotics/reachy-mini-emotions-library から RecordedMoves で取得
# - 構成: VADスレッドが speaking フラグを更新し、メインのモーションループが
#   状態に応じたプールからモーションを再生。状態切替時は cancel_move() で即中断。
#
# 詳細は plan.md を参照。

from __future__ import annotations

import argparse
import logging
import queue
import random
import re
import threading
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path

import mlx_whisper
import numpy as np
import torch
from dotenv import load_dotenv
from openai import OpenAI

from reachy_mini import ReachyMini
from reachy_mini.motion.move import Move
from reachy_mini.motion.recorded_move import RecordedMoves
from reachy_mini.utils import create_head_pose

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("vad_motion")

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"

# 発話中プール(傾聴・相槌系)
TALKING_POOL = ("attentive1", "attentive2", "yes1", "understanding2")
# 待機はカスタムの IdleMove(ゆっくり顔を動かす+アンテナスウェイ)を使用

# VAD パラメータ(test_vad_wav.py / conversation_app に準拠)
VAD_SAMPLE_RATE = 16000  # Silero の要件
VAD_CHUNK_SIZE = 512  # 32ms @ 16kHz
VAD_THRESHOLD = 0.7  # 発話とみなす確率(誤検出が多い場合はさらに上げる)
VAD_ATTACK_FRAMES = 4  # 連続でこのフレーム数(4×32ms≈128ms)超えたら発話開始
VAD_RELEASE_S = 0.6  # 無音がこの秒数続いたら待機へ (min_silence 300ms + idle delay 300ms)
VAD_LOG_INTERVAL_S = 2.0  # 確率の統計ログ出力間隔(チューニング用)
VAD_PREROLL_S = 0.2  # ATTACK判定前の音を取りこぼさないためのプリロール長

# STT(Whisper)パラメータ
STT_MODEL = "mlx-community/whisper-large-v3-turbo"
STT_LANGUAGE = "ja"
STT_MIN_SEGMENT_S = 0.3  # これより短い発話区間は誤検出/ノイズとして捨てる
STT_MAX_SEGMENT_S = 30.0  # Whisperの入力上限・メモリ保護のためこの長さで強制分割
STT_MIN_RMS = 0.005  # これ未満は無音とみなしてWhisperに投げない(幻覚対策)
STT_NO_SPEECH_PROB_MAX = 0.6  # これを超えて avg_logprob も低ければ幻覚として破棄
STT_AVG_LOGPROB_MIN = -1.0
STT_COMPRESSION_RATIO_MAX = 2.4  # Whisper本家のデフォルト閾値。同じ文字/パターンの繰り返しほど高くなる
STT_REPEAT_PATTERN = re.compile(r"(.{1,4})\1{9,}")  # 1〜4文字の塊が10回以上連続する「ピピピピ...」的な繰り返し
STT_HALLUCINATION_PHRASES = (
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "チャンネル登録",
    "字幕",
    "Thanks for watching",
)
STT_DUMP_DIR = "stt_segments"  # --dump-segments 指定時の保存先ディレクトリ

# LLM(応答生成)パラメータ
LLM_MODEL = "gpt-5.4-mini"
LLM_MAX_OUTPUT_TOKENS = 150
LLM_TIMEOUT_S = 10.0
LLM_HISTORY_TURNS = 5  # 保持する往復数(user/assistant ペア)。deque(maxlen=LLM_HISTORY_TURNS*2)
LLM_SYSTEM_PROMPT = """\
あなたは福岡のエンジニアカフェにいる小型ロボット「Reachy Mini」たい。
来場者と博多弁で気さくに雑談する。

ルール:
- 必ず博多弁で話す。「〜と?」「〜ばい」「〜たい」「〜けん」「〜っちゃん」
  「〜しとう」「よかよ」などを自然に使う。わざとらしい誇張はせん
- 音声で読み上げる前提。1〜2文、50文字以内で短く話し言葉で答える
- 箇条書き・記号・絵文字・英語は使わない
- 入力は音声認識の結果やけん、誤変換があっても文脈で補って解釈する
- わからんことは正直に「わからんばい」と言う

例:
ユーザー「今日は何ができると?」
→「おしゃべりできるばい。なんか聞きたいことあると?」
"""

# モーション再生パラメータ
INITIAL_GOTO_DURATION = 0.5  # モーション開始位置への補間時間
# 発話中モーション間のポーズ(秒)。連続再生するとサーボ音をマイクが拾い続け、
# VADが下がる隙がなくなって talking から抜けられなくなるため必ず間を空ける。
TALKING_PAUSE_RANGE = (0.5, 1.2)


# ---------------------------------------------------------------------------
# Silero VAD ラッパ
# ---------------------------------------------------------------------------
class SileroVAD:
    """Silero VAD のロードとチャンク単位の発話確率判定。"""

    def __init__(self) -> None:
        logger.info("Loading Silero VAD model...")
        self.model, _ = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            trust_repo=True,
        )
        self.model.eval()

    def prob(self, chunk: np.ndarray) -> float:
        """512サンプル(16kHz, float32, [-1,1])の発話確率を返す。"""
        with torch.no_grad():
            tensor = torch.from_numpy(chunk)
            return float(self.model(tensor, VAD_SAMPLE_RATE).item())

    def reset(self) -> None:
        self.model.reset_states()


def resample_to_16k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """線形補間で 16kHz にリサンプリング。"""
    if src_rate == VAD_SAMPLE_RATE:
        return audio
    n_dst = int(len(audio) * VAD_SAMPLE_RATE / src_rate)
    if n_dst == 0:
        return np.empty(0, dtype=np.float32)
    x_src = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_dst = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(x_dst, x_src, audio).astype(np.float32)


def to_mono(audio: np.ndarray, channels: int = 1) -> np.ndarray:
    """float32 配列をモノラル 1-D に変換。

    (n, ch) の2次元、または 1-D インターリーブ(n*ch)の両方に対応。
    インターリーブをモノラル扱いすると音声が壊れて VAD が誤検出するため重要。
    """
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        return audio.mean(axis=1)
    if channels > 1 and audio.size % channels == 0:
        return audio.reshape(-1, channels).mean(axis=1)
    return audio.reshape(-1)


def normalize(audio: np.ndarray) -> np.ndarray:
    """int16 スケールの値が来た場合に [-1, 1] へ正規化。"""
    if audio.size and np.abs(audio).max() > 1.5:
        audio = audio / 32768.0
    return audio


# ---------------------------------------------------------------------------
# 待機モーション(カスタム)
# ---------------------------------------------------------------------------
class IdleMove(Move):
    """待機用のシンプルなループモーション。

    ゆっくり顔を左右に振りながら軽く上下し、アンテナを逆位相でスウェイさせる。
    emotions library の記録済みモーション(発話中に使用)と比べて
    明らかに遅く小さい動きにすることで、状態の違いを見た目で分かるようにする。
    再生は play_move 任せ: duration が無限なので、cancel されるまで動き続ける。
    """

    # 周期は互いに割り切れない値にして、単調な繰り返しに見えないようにする
    YAW_FREQ = 0.05  # Hz (左右の首振り: 20秒で1往復)
    YAW_AMP_DEG = 15.0
    PITCH_FREQ = 0.08  # Hz (上下)
    PITCH_AMP_DEG = 4.0
    Z_FREQ = 0.1  # Hz (呼吸のような上下動)
    Z_AMP_MM = 4.0
    ANTENNA_FREQ = 0.3  # Hz (アンテナスウェイ)
    ANTENNA_AMP_DEG = 12.0

    @property
    def duration(self) -> float:
        return float("inf")

    def evaluate(
        self, t: float
    ) -> tuple[np.ndarray | None, np.ndarray | None, float | None]:
        two_pi = 2.0 * np.pi
        head = create_head_pose(
            z=self.Z_AMP_MM * np.sin(two_pi * self.Z_FREQ * t),
            yaw=self.YAW_AMP_DEG * np.sin(two_pi * self.YAW_FREQ * t),
            pitch=self.PITCH_AMP_DEG * np.sin(two_pi * self.PITCH_FREQ * t),
            mm=True,
            degrees=True,
        )
        sway = np.deg2rad(self.ANTENNA_AMP_DEG) * np.sin(two_pi * self.ANTENNA_FREQ * t)
        antennas = np.array([sway, -sway], dtype=np.float64)
        return head, antennas, 0.0


def cancel_current_move(mini: ReachyMini) -> None:
    """再生中のモーションだけを中断する。

    mini.cancel_move() は使わない: 内部で media_manager.stop_playing() を呼び、
    録音と再生が共有する GStreamer パイプラインを NULL にするため、
    マイク録音まで止まって VAD が沈黙する。
    play_move のループは _move_cancelled フラグしか見ていないので、
    フラグを立てるだけで十分。
    """
    mini._move_cancelled = True


# ---------------------------------------------------------------------------
# VAD ワーカースレッド
# ---------------------------------------------------------------------------
def vad_worker(
    mini: ReachyMini,
    vad: SileroVAD,
    speaking: threading.Event,
    stop: threading.Event,
    stt_queue: "queue.Queue[np.ndarray] | None" = None,
) -> None:
    """マイク入力を監視し、speaking フラグを更新する。

    状態が切替わったら cancel_move() で再生中のモーションを中断し、
    モーションループに即座に反映させる。
    stt_queue が渡された場合は、発話区間の音声を蓄積して確定時に put する。
    """
    src_rate = mini.media.get_input_audio_samplerate()
    channels = mini.media.get_input_channels()
    logger.info("Mic samplerate: %d Hz, channels: %d", src_rate, channels)

    buffer = np.empty(0, dtype=np.float32)
    last_speech_time = 0.0
    consecutive_speech = 0  # 閾値超えの連続フレーム数(アタック判定用)
    window_max_prob = 0.0  # チューニング用の統計
    window_max_amp = 0.0  # チューニング用: 生の振幅(音が本当に届いているかの切り分け用)
    last_log_time = time.monotonic()

    # 発話区間の切り出し用(VADIterator の prefix_buffer / buffer と同じ考え方)
    preroll_frames = int(VAD_PREROLL_S * VAD_SAMPLE_RATE / VAD_CHUNK_SIZE) + 1
    preroll: deque[np.ndarray] = deque(maxlen=preroll_frames)
    segment: list[np.ndarray] = []
    segment_samples = 0

    def flush_segment(*, force: bool = False) -> None:
        """蓄積中の segment を stt_queue に put して空にする。

        force=True は30秒超過による強制分割用: 短すぎてもフィルタせず送る。
        """
        nonlocal segment_samples
        if stt_queue is None or not segment:
            segment.clear()
            segment_samples = 0
            return
        audio = np.concatenate(segment)
        segment.clear()
        segment_samples = 0
        if not force and len(audio) < STT_MIN_SEGMENT_S * VAD_SAMPLE_RATE:
            return
        stt_queue.put(audio)

    while not stop.is_set():
        sample = mini.media.get_audio_sample()
        if sample is None:
            time.sleep(0.01)
            continue

        logger.debug("Sample: %s", sample.shape)

        chunk = resample_to_16k(normalize(to_mono(sample, channels)), src_rate)
        buffer = np.concatenate([buffer, chunk])

        while len(buffer) >= VAD_CHUNK_SIZE:
            frame, buffer = buffer[:VAD_CHUNK_SIZE], buffer[VAD_CHUNK_SIZE:]
            now = time.monotonic()

            # 発話区間切り出し: speaking 中は segment に、そうでなければ preroll に積む。
            # (この時点の speaking はまだ今フレームの判定を反映していない = 直前の状態)
            was_speaking = speaking.is_set()
            if stt_queue is not None:
                if was_speaking:
                    segment.append(frame)
                    segment_samples += len(frame)
                else:
                    preroll.append(frame)

            prob = vad.prob(frame)
            window_max_prob = max(window_max_prob, prob)
            window_max_amp = max(window_max_amp, float(np.abs(frame).max()) if frame.size else 0.0)

            # チューニング用: 直近ウィンドウの最大確率を定期出力
            if now - last_log_time >= VAD_LOG_INTERVAL_S:
                logger.info(
                    "VAD max prob (last %.0fs): %.2f  amp=%.4f  [speaking=%s]",
                    VAD_LOG_INTERVAL_S, window_max_prob, window_max_amp, speaking.is_set(),
                )
                window_max_prob = 0.0
                window_max_amp = 0.0
                last_log_time = now

            if prob > VAD_THRESHOLD:
                consecutive_speech += 1
                # 重要: last_speech_time は「連続フレームで発話が確定した時」のみ更新する。
                # 単発スパイク(サーボ音など)で更新すると release タイマーが
                # リセットされ続け、talking から永遠に抜けられなくなる。
                if consecutive_speech >= VAD_ATTACK_FRAMES:
                    last_speech_time = now
                    if not was_speaking:
                        logger.info("Speech detected -> talking motions")
                        speaking.set()
                        cancel_current_move(mini)
                        if stt_queue is not None:
                            # ATTACK確定前の音(preroll、今フレーム含む)を segment の先頭にする
                            segment = list(preroll)
                            segment_samples = sum(len(f) for f in segment)
                            preroll.clear()
            else:
                consecutive_speech = 0
                if was_speaking and now - last_speech_time > VAD_RELEASE_S:
                    logger.info("Silence -> idle motions")
                    speaking.clear()
                    vad.reset()
                    cancel_current_move(mini)
                    flush_segment()

            if stt_queue is not None and segment_samples >= STT_MAX_SEGMENT_S * VAD_SAMPLE_RATE:
                logger.info("Utterance exceeds %.0fs, force-splitting for STT", STT_MAX_SEGMENT_S)
                flush_segment(force=True)


# ---------------------------------------------------------------------------
# STT(Whisper)ワーカースレッド
# ---------------------------------------------------------------------------
def stt_warmup() -> None:
    """モデルロードを起動時に済ませる(初回 transcribe はロードで数十秒かかる)。"""
    logger.info("Loading Whisper STT model: %s", STT_MODEL)
    dummy = np.zeros(VAD_SAMPLE_RATE, dtype=np.float32)
    mlx_whisper.transcribe(
        dummy,
        path_or_hf_repo=STT_MODEL,
        language=STT_LANGUAGE,
        condition_on_previous_text=False,
    )
    logger.info("Whisper STT model ready")


def _is_hallucination(text: str, result: dict) -> bool:
    """幻覚(無音・ノイズ区間での定型文出力・同一文字の暴走)らしき結果かどうかを判定する。"""
    if not text:
        return True
    if any(phrase in text for phrase in STT_HALLUCINATION_PHRASES):
        return True
    if STT_REPEAT_PATTERN.search(text):  # 「ピピピピ...」のような同一パターンの暴走的な繰り返し
        return True
    segments = result.get("segments") or []
    if segments:
        no_speech_probs = [s.get("no_speech_prob", 0.0) for s in segments]
        avg_logprobs = [s.get("avg_logprob", 0.0) for s in segments]
        compression_ratios = [s.get("compression_ratio", 0.0) for s in segments]
        if max(no_speech_probs) > STT_NO_SPEECH_PROB_MAX and min(avg_logprobs) < STT_AVG_LOGPROB_MIN:
            return True
        if max(compression_ratios) > STT_COMPRESSION_RATIO_MAX:
            return True
    return False


def save_segment_wav(path: Path, audio: np.ndarray, sample_rate: int = VAD_SAMPLE_RATE) -> None:
    """float32 [-1, 1] の音声を 16bit PCM WAV として保存する(--dump-segments 検証用)。"""
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm16.tobytes())


def stt_worker(
    stt_queue: "queue.Queue[np.ndarray | None]",
    stop: threading.Event,
    on_result=None,
    dump_dir: Path | None = None,
) -> None:
    """stt_queue から発話区間を受け取り、Whisper で日本語書き起こしする。

    番兵(None)が届くまで動き続ける専用スレッド。VADスレッドをブロックしないよう、
    重い transcribe 呼び出しはここに隔離する。
    dump_dir が指定された場合、STTに渡す前の音声をそのまま WAV として保存する
    (フィルタで捨てられる区間も含めて確認できるように、フィルタより前に保存する)。
    """
    if dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)

    while True:
        segment = stt_queue.get()
        if segment is None:  # 番兵: 終了合図
            break
        if stop.is_set():
            continue

        if dump_dir is not None:
            filename = f"segment_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
            try:
                save_segment_wav(dump_dir / filename, segment)
            except Exception as e:
                logger.warning("Failed to dump segment WAV: %s", e)

        rms = float(np.sqrt(np.mean(np.square(segment)))) if segment.size else 0.0
        if rms < STT_MIN_RMS:
            logger.debug("STT: segment too quiet (rms=%.4f), skipped", rms)
            continue

        started = time.monotonic()
        try:
            result = mlx_whisper.transcribe(
                segment,
                path_or_hf_repo=STT_MODEL,
                language=STT_LANGUAGE,
                condition_on_previous_text=False,
            )
        except Exception as e:
            logger.warning("STT transcribe failed: %s", e)
            continue
        elapsed = time.monotonic() - started

        text = result["text"].strip()
        if _is_hallucination(text, result):
            logger.debug("STT: discarded likely hallucination: %r (%.2fs)", text, elapsed)
            continue

        logger.info("STT: %s (%.2fs)", text, elapsed)
        if on_result is not None:
            on_result(text)


# ---------------------------------------------------------------------------
# LLM(応答生成)ワーカースレッド
# ---------------------------------------------------------------------------
def _drain_latest(llm_queue: "queue.Queue[str | None]", first: str | None) -> str | None:
    """キューに溜まった古いリクエストを捨て、最新の1件だけ残す(割り込み対応)。

    LLM応答待ちの間に新しい発話が来たら、古い方を処理しても無駄になるため。
    途中で番兵(None)が来た場合は終了合図として None を返す。
    """
    latest = first
    while True:
        try:
            item = llm_queue.get_nowait()
        except queue.Empty:
            return latest
        if item is None:
            return None
        latest = item


def llm_worker(
    llm_queue: "queue.Queue[str | None]",
    stop: threading.Event,
    on_result=None,
) -> None:
    """llm_queue から書き起こしテキストを受け取り、OpenAI APIで博多弁の応答を生成する。

    番兵(None)が届くまで動き続ける専用スレッド。STTスレッドをブロックしないよう、
    ネットワーク待ちが発生する API 呼び出しはここに隔離する。
    """
    client = OpenAI()
    history: deque[dict] = deque(maxlen=LLM_HISTORY_TURNS * 2)

    while True:
        item = llm_queue.get()
        if item is None:  # 番兵: 終了合図
            break
        text = _drain_latest(llm_queue, item)
        if text is None:
            break
        if stop.is_set():
            continue

        started = time.monotonic()
        try:
            resp = client.responses.create(
                model=LLM_MODEL,
                instructions=LLM_SYSTEM_PROMPT,
                input=[*history, {"role": "user", "content": text}],
                # gpt-5.4-mini は reasoning.effort に "minimal" 非対応(none/low/medium/high/xhigh のみ)。
                # 雑談用途でレイテンシを削りたいので最も軽い "none" を指定する。
                reasoning={"effort": "none"},
                max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
                timeout=LLM_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("LLM request failed: %s", e)
            continue
        elapsed = time.monotonic() - started

        reply = resp.output_text.strip()
        if not reply:
            logger.warning("LLM: empty response, skipped")
            continue

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})

        logger.info("LLM: %s (%.2fs)", reply, elapsed)
        if on_result is not None:
            on_result(reply)


# ---------------------------------------------------------------------------
# モーションループ
# ---------------------------------------------------------------------------
def interruptible_sleep(
    duration: float,
    speaking: threading.Event,
    stop: threading.Event,
    was_speaking: bool,
) -> None:
    """speaking の状態が was_speaking から変化するか stop されるまで最大 duration 秒待つ。"""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        if stop.is_set() or speaking.is_set() != was_speaking:
            return
        time.sleep(0.05)


def motion_loop(
    mini: ReachyMini,
    moves: dict[str, object],
    speaking: threading.Event,
    stop: threading.Event,
) -> None:
    """現在の状態に応じたモーションを再生し続ける。

    - idle: IdleMove(無限ループ)を1回 play_move する。発話検出の cancel で戻ってくる。
    - talking: TALKING_POOL からランダムに選んで再生し、間にポーズを挟む。
    """
    idle_move = IdleMove()
    prev_name: str | None = None

    while not stop.is_set():
        if not speaking.is_set():
            # --- 待機: カスタムループモーション(cancel されるまで再生され続ける) ---
            logger.info("Playing idle loop")
            try:
                mini.play_move(idle_move, initial_goto_duration=1.0, sound=False)
            except Exception as e:
                logger.warning("idle play_move failed: %s", e)
                time.sleep(0.5)
            continue

        # --- 発話中: emotions library からランダム再生 ---
        candidates = [n for n in TALKING_POOL if n != prev_name] or list(TALKING_POOL)
        name = random.choice(candidates)
        prev_name = name

        logger.info("Playing %s (talking)", name)
        try:
            # sound=False: emotions 付属の効果音は鳴らさない
            mini.play_move(moves[name], initial_goto_duration=INITIAL_GOTO_DURATION, sound=False)
        except Exception as e:
            logger.warning("play_move failed for %s: %s", name, e)
            time.sleep(0.5)
            continue

        # モーション間にポーズを挟む(状態変化で即中断)。
        # サーボ静音の時間を作って VAD が無音を検出できるようにする意味もある。
        if not stop.is_set():
            interruptible_sleep(random.uniform(*TALKING_PAUSE_RANGE), speaking, stop, True)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="VAD-driven motion for Reachy Mini")
    parser.add_argument(
        "--no-motion",
        action="store_true",
        help="モーションを再生せず、VADの状態遷移ログだけを出す(検証用)",
    )
    parser.add_argument(
        "--no-stt",
        action="store_true",
        help="Whisperによる書き起こしを無効化し、従来のモーション動作のみ行う",
    )
    parser.add_argument(
        "--dump-segments",
        action="store_true",
        help=f"STTに渡す音声区間をWAVとして {STT_DUMP_DIR}/ に保存する(検証用)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="OpenAI APIによる応答生成を無効化し、STTまでの動作にとどめる",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent / ".env")

    vad = SileroVAD()

    if not args.no_stt:
        stt_warmup()

    moves: dict[str, object] = {}
    if not args.no_motion:
        logger.info("Loading emotions library: %s", EMOTIONS_DATASET)
        emotions = RecordedMoves(EMOTIONS_DATASET)
        available = set(emotions.list_moves())
        missing = [n for n in TALKING_POOL if n not in available]
        if missing:
            raise ValueError(f"Moves not found in {EMOTIONS_DATASET}: {missing}")
        moves = {name: emotions.get(name) for name in TALKING_POOL}
        logger.info("Loaded %d moves", len(moves))

    speaking = threading.Event()
    stop = threading.Event()

    llm_queue: "queue.Queue[str | None] | None" = None
    llm_thread: threading.Thread | None = None
    if not args.no_stt and not args.no_llm:
        llm_queue = queue.Queue()
        llm_thread = threading.Thread(
            target=llm_worker, args=(llm_queue, stop), daemon=True
        )
        llm_thread.start()

    stt_queue: "queue.Queue[np.ndarray | None] | None" = None
    stt_thread: threading.Thread | None = None
    if not args.no_stt:
        stt_queue = queue.Queue()
        dump_dir = Path(STT_DUMP_DIR) if args.dump_segments else None
        if dump_dir is not None:
            logger.info("Dumping STT input segments to: %s", dump_dir.resolve())
        on_stt_result = (lambda text: llm_queue.put(text)) if llm_queue is not None else None
        stt_thread = threading.Thread(
            target=stt_worker,
            args=(stt_queue, stop),
            kwargs={"dump_dir": dump_dir, "on_result": on_stt_result},
            daemon=True,
        )
        stt_thread.start()

    with ReachyMini() as mini:
        logger.info("Connected to Reachy Mini")

        mini.media.start_recording()
        vad_thread = threading.Thread(
            target=vad_worker, args=(mini, vad, speaking, stop, stt_queue), daemon=True
        )
        vad_thread.start()

        try:
            if args.no_motion:
                # 検証モード: ロボットは動かさない。
                # vad_worker のログ (Speech detected / Silence / VAD max prob) だけを観察する。
                logger.info("--no-motion: VAD verification mode (robot stays still)")
                while True:
                    time.sleep(0.5)
            else:
                motion_loop(mini, moves, speaking, stop)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            stop.set()
            cancel_current_move(mini)
            vad_thread.join(timeout=2.0)
            mini.media.stop_recording()
            if stt_queue is not None:
                stt_queue.put(None)  # 番兵で STT スレッドを終了させる
            if stt_thread is not None:
                stt_thread.join(timeout=5.0)
            if llm_queue is not None:
                llm_queue.put(None)  # 番兵で LLM スレッドを終了させる
            if llm_thread is not None:
                llm_thread.join(timeout=LLM_TIMEOUT_S + 2.0)
            # ニュートラル姿勢へ戻して終了
            mini.goto_target(head=create_head_pose(), antennas=[0.0, 0.0], duration=1.0)


if __name__ == "__main__":
    main()
