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
import random
import threading
import time

import numpy as np
import torch

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
) -> None:
    """マイク入力を監視し、speaking フラグを更新する。

    状態が切替わったら cancel_move() で再生中のモーションを中断し、
    モーションループに即座に反映させる。
    """
    src_rate = mini.media.get_input_audio_samplerate()
    channels = mini.media.get_input_channels()
    logger.info("Mic samplerate: %d Hz, channels: %d", src_rate, channels)

    buffer = np.empty(0, dtype=np.float32)
    last_speech_time = 0.0
    consecutive_speech = 0  # 閾値超えの連続フレーム数(アタック判定用)
    window_max_prob = 0.0  # チューニング用の統計
    last_log_time = time.monotonic()

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

            prob = vad.prob(frame)
            window_max_prob = max(window_max_prob, prob)

            # チューニング用: 直近ウィンドウの最大確率を定期出力
            if now - last_log_time >= VAD_LOG_INTERVAL_S:
                logger.info(
                    "VAD max prob (last %.0fs): %.2f  [speaking=%s]",
                    VAD_LOG_INTERVAL_S, window_max_prob, speaking.is_set(),
                )
                window_max_prob = 0.0
                last_log_time = now

            if prob > VAD_THRESHOLD:
                consecutive_speech += 1
                # 重要: last_speech_time は「連続フレームで発話が確定した時」のみ更新する。
                # 単発スパイク(サーボ音など)で更新すると release タイマーが
                # リセットされ続け、talking から永遠に抜けられなくなる。
                if consecutive_speech >= VAD_ATTACK_FRAMES:
                    last_speech_time = now
                    if not speaking.is_set():
                        logger.info("Speech detected -> talking motions")
                        speaking.set()
                        cancel_current_move(mini)
            else:
                consecutive_speech = 0
                if speaking.is_set() and now - last_speech_time > VAD_RELEASE_S:
                    logger.info("Silence -> idle motions")
                    speaking.clear()
                    vad.reset()
                    cancel_current_move(mini)


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
    args = parser.parse_args()

    vad = SileroVAD()

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

    with ReachyMini() as mini:
        logger.info("Connected to Reachy Mini")

        mini.media.start_recording()
        vad_thread = threading.Thread(
            target=vad_worker, args=(mini, vad, speaking, stop), daemon=True
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
            # ニュートラル姿勢へ戻して終了
            mini.goto_target(head=create_head_pose(), antennas=[0.0, 0.0], duration=1.0)


if __name__ == "__main__":
    main()
