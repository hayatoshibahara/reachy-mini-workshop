#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent
SPEECH_TO_SPEECH_SRC = REPO_ROOT / "speech-to-speech" / "src"
DEFAULT_WAV = REPO_ROOT / "japanese_sample_gakko.wav"
DEFAULT_STT_WAV = REPO_ROOT / "vad_for_stt_whisper_large_v3_turbo.wav"
SAMPLE_SOURCE = "https://commons.wikimedia.org/wiki/File:Jp-gakk%C5%8D.ogg"

sys.path.insert(0, str(SPEECH_TO_SPEECH_SRC))

from speech_to_speech.VAD.vad_iterator import VADIterator  # noqa: E402


class RecordingVADModel:
    def __init__(self, model: torch.nn.Module) -> None:
        self.model = model
        self.probabilities: list[float] = []

    def reset_states(self) -> None:
        self.model.reset_states()

    def __call__(self, x: torch.Tensor, sampling_rate: int) -> torch.Tensor:
        probability = self.model(x, sampling_rate)
        self.probabilities.append(float(probability.item()))
        return probability


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run speech-to-speech VADIterator against a WAV file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "wav_path",
        nargs="?",
        type=Path,
        default=DEFAULT_WAV,
        help="16-bit PCM WAV file to process.",
    )
    parser.add_argument("--threshold", type=float, default=0.5, help="Speech probability threshold.")
    parser.add_argument("--min-silence-ms", type=int, default=300, help="Silence duration that closes a speech segment.")
    parser.add_argument("--speech-pad-ms", type=int, default=30, help="Audio retained before VAD triggers.")
    parser.add_argument("--flush-silence-ms", type=int, default=1000, help="Silence appended after the WAV to flush VAD.")
    parser.add_argument(
        "--stt-output",
        type=Path,
        default=DEFAULT_STT_WAV,
        help="Output WAV containing VAD-detected speech for STT.",
    )
    parser.add_argument(
        "--stt-gap-ms",
        type=int,
        default=200,
        help="Silence inserted between multiple VAD segments in the STT output WAV.",
    )
    return parser.parse_args()


def load_wav_mono_int16(path: Path) -> tuple[int, np.ndarray]:
    if not path.exists():
        raise FileNotFoundError(path)

    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
        pcm = wav_file.readframes(frames)

    if sample_width != 2:
        raise ValueError(f"{path} must be 16-bit PCM WAV; got sample width {sample_width} bytes")
    if sample_rate not in (8000, 16000):
        raise ValueError(f"{path} must be 8000 Hz or 16000 Hz for Silero VAD; got {sample_rate} Hz")

    samples = np.frombuffer(pcm, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return sample_rate, samples


def chunk_audio(audio: np.ndarray, chunk_samples: int) -> list[np.ndarray]:
    chunks: list[np.ndarray] = []
    for start in range(0, len(audio), chunk_samples):
        chunk = audio[start : start + chunk_samples]
        if len(chunk) < chunk_samples:
            chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
        chunks.append(chunk.astype(np.float32, copy=False))
    return chunks


def format_ms(samples: int, sample_rate: int) -> str:
    return f"{samples / sample_rate * 1000:.0f}ms"


def float_to_int16(audio: np.ndarray) -> np.ndarray:
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * np.iinfo(np.int16).max).astype("<i2")


def write_wav_mono_int16(path: Path, sample_rate: int, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = float_to_int16(audio)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def combine_segments_for_stt(segments: list[np.ndarray], sample_rate: int, gap_ms: int) -> np.ndarray:
    if not segments:
        return np.array([], dtype=np.float32)

    gap = np.zeros(int(sample_rate * gap_ms / 1000), dtype=np.float32)
    parts: list[np.ndarray] = []
    for index, segment in enumerate(segments):
        if index > 0 and len(gap) > 0:
            parts.append(gap)
        parts.append(segment.astype(np.float32, copy=False))
    return np.concatenate(parts)


def main() -> int:
    args = parse_args()
    wav_path = args.wav_path.resolve()
    stt_output_path = args.stt_output.resolve()

    sample_rate, audio_int16 = load_wav_mono_int16(wav_path)
    audio_float32 = audio_int16.astype(np.float32) / 32768.0
    flush_silence = np.zeros(int(sample_rate * args.flush_silence_ms / 1000), dtype=np.float32)
    stream = np.concatenate([audio_float32, flush_silence])
    chunk_samples = 512 if sample_rate == 16000 else 256

    print(f"Audio: {wav_path}")
    if wav_path == DEFAULT_WAV.resolve():
        print(f"Sample source: {SAMPLE_SOURCE}")
    print(
        "Config: "
        f"sample_rate={sample_rate}Hz "
        f"chunk_samples={chunk_samples} "
        f"threshold={args.threshold:.2f} "
        f"min_silence_ms={args.min_silence_ms} "
        f"speech_pad_ms={args.speech_pad_ms}"
    )
    print("Loading Silero VAD model with torch.hub...")

    torch.set_num_threads(1)
    model, _ = torch.hub.load(
        "snakers4/silero-vad",
        "silero_vad",
        trust_repo=True,
        skip_validation=True,
    )
    recording_model = RecordingVADModel(model)
    iterator = VADIterator(
        recording_model,
        threshold=args.threshold,
        sampling_rate=sample_rate,
        min_silence_duration_ms=args.min_silence_ms,
        speech_pad_ms=args.speech_pad_ms,
    )

    segments: list[tuple[int, int, int, int]] = []
    stt_segments: list[np.ndarray] = []
    processed_samples = 0
    for chunk in chunk_audio(stream, chunk_samples):
        vad_output = iterator(torch.from_numpy(chunk))
        processed_samples += len(chunk)
        if vad_output is None:
            continue

        segment_audio = torch.cat(vad_output).cpu().numpy().astype(np.float32, copy=False)
        segment_samples = len(segment_audio)
        active_samples = iterator.last_utterance_active_speech_samples
        start_sample = max(0, processed_samples - segment_samples)
        segments.append((start_sample, processed_samples, segment_samples, active_samples))
        stt_segments.append(segment_audio)

    probabilities = recording_model.probabilities
    print(
        "Processed: "
        f"{format_ms(len(audio_float32), sample_rate)} audio + "
        f"{args.flush_silence_ms}ms trailing silence in {len(probabilities)} chunks"
    )
    if probabilities:
        above_threshold = sum(prob >= args.threshold for prob in probabilities)
        print(
            "Speech probabilities: "
            f"max={max(probabilities):.3f} "
            f"mean={float(np.mean(probabilities)):.3f} "
            f"chunks_at_or_above_threshold={above_threshold}/{len(probabilities)}"
        )

    if not segments:
        print("Detected speech segments: none")
        return 1

    print("Detected speech segments:")
    for index, (start, end, duration, active) in enumerate(segments, start=1):
        print(
            f"  {index}. start={format_ms(start, sample_rate)} "
            f"end={format_ms(end, sample_rate)} "
            f"duration={format_ms(duration, sample_rate)} "
            f"active={format_ms(active, sample_rate)}"
        )

    stt_audio = combine_segments_for_stt(stt_segments, sample_rate, args.stt_gap_ms)
    write_wav_mono_int16(stt_output_path, sample_rate, stt_audio)
    print(
        "STT input WAV: "
        f"{stt_output_path} "
        f"({format_ms(len(stt_audio), sample_rate)}, mono 16-bit PCM, target model=whisper-large-v3-turbo)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
