"""Silero VAD で recorded.wav から発話区間を抽出する（先に 04_record.py を実行）。"""

import numpy as np
import soundfile as sf

from common import RECORDED_PATH, extract_speech, load_vad

audio_data, in_sr = sf.read(str(RECORDED_PATH), dtype="float32", always_2d=True)
mono = audio_data.mean(axis=1).astype(np.float32)

vad_model, get_speech_timestamps = load_vad()
speech_only, speech_timestamps = extract_speech(mono, in_sr, vad_model, get_speech_timestamps)

print(f"検出された発話区間: {len(speech_timestamps)} 件")
for i, seg in enumerate(speech_timestamps, start=1):
    start_s = seg["start"] / in_sr
    end_s = seg["end"] / in_sr
    print(f"  {i}. {start_s:.2f}s 〜 {end_s:.2f}s （{end_s - start_s:.2f}秒）")

print(f"発話区間だけの音声: {len(speech_only) / in_sr:.2f}秒（元の録音: {len(mono) / in_sr:.2f}秒）")
