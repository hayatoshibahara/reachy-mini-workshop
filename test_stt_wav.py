#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent
SPEECH_TO_SPEECH_SRC = REPO_ROOT / "speech-to-speech" / "src"
DEFAULT_INPUT_WAV = REPO_ROOT / "vad_for_stt_whisper_large_v3_turbo.wav"
DEFAULT_MODEL = "openai/whisper-large-v3-turbo"
NUMPY_FIX_COMMAND = "python -m pip install --force-reinstall 'numpy==1.26.4'"
DEFAULT_CUDA_ALLOC_CONF = "expandable_segments:True"

sys.path.insert(0, str(SPEECH_TO_SPEECH_SRC))

from speech_to_speech.pipeline.messages import Transcription, VADAudio  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark speech-to-speech Whisper STT against the VAD output WAV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "wav_path",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_WAV,
        help="VAD output WAV to transcribe.",
    )
    parser.add_argument("--model-name", default=DEFAULT_MODEL, help="Transformers Whisper model id or local path.")
    parser.add_argument("--language", default="ja", help="Whisper language code.")
    parser.add_argument("--device", default="cuda", help="Torch device for low-latency STT.")
    parser.add_argument("--dtype", default="float16", help="Torch dtype name for STT.")
    parser.add_argument("--max-new-tokens", type=int, default=64, help="Maximum generated transcript tokens.")
    parser.add_argument("--num-beams", type=int, default=1, help="Whisper beam count.")
    parser.add_argument("--runs", type=int, default=3, help="Measured transcription runs.")
    parser.add_argument(
        "--real-audio-warmups",
        type=int,
        default=1,
        help="Unmeasured warmup transcriptions on the real VAD audio before measuring.",
    )
    parser.add_argument(
        "--compile-mode",
        default=None,
        choices=("default", "reduce-overhead", "max-autotune"),
        help="Optional torch.compile mode passed to WhisperSTTHandler.",
    )
    parser.add_argument(
        "--hf-home",
        type=Path,
        default=None,
        help="Optional Hugging Face cache directory. By default, use the current environment/default cache.",
    )
    parser.add_argument(
        "--offline",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use only locally cached model files.",
    )
    parser.add_argument(
        "--warmup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the handler setup warmup step before measured transcription.",
    )
    parser.add_argument(
        "--fast-process",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use one fixed-language generate call. Disable to use WhisperSTTHandler.process exactly.",
    )
    parser.add_argument(
        "--transcript-output",
        type=Path,
        default=None,
        help="Optional path to write the final transcript text.",
    )
    parser.add_argument(
        "--cuda-alloc-conf",
        default=DEFAULT_CUDA_ALLOC_CONF,
        help="PYTORCH_CUDA_ALLOC_CONF value set before importing torch. Use an empty string to leave it unset.",
    )
    return parser.parse_args()


def load_wav_mono_float32(path: Path) -> tuple[int, np.ndarray]:
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
    if sample_rate != 16000:
        raise ValueError(f"{path} must be 16000 Hz for WhisperSTTHandler; got {sample_rate} Hz")

    samples = np.frombuffer(pcm, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return sample_rate, samples.astype(np.float32) / 32768.0


def format_ms(samples: int, sample_rate: int) -> str:
    return f"{samples / sample_rate * 1000:.0f}ms"


def summarize_latencies(latencies: list[float]) -> str:
    values = np.array(latencies, dtype=np.float64)
    return (
        f"min={values.min():.3f}s "
        f"p50={np.percentile(values, 50):.3f}s "
        f"mean={values.mean():.3f}s "
        f"max={values.max():.3f}s"
    )


def numpy_major_version() -> int | None:
    try:
        return int(np.__version__.split(".", maxsplit=1)[0])
    except (AttributeError, ValueError):
        return None


def format_gib(num_bytes: int) -> str:
    return f"{num_bytes / 1024**3:.2f}GiB"


def looks_like_cuda_allocation_error(exc: RuntimeError) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "out of memory",
            "cudacachingallocator",
            "nvml_success",
            "cuda error",
            "nvmem",
            "nvmap",
        )
    )


def main() -> int:
    args = parse_args()
    wav_path = args.wav_path.resolve()
    if args.runs < 1:
        raise ValueError("--runs must be at least 1")
    if args.real_audio_warmups < 0:
        raise ValueError("--real-audio-warmups must be non-negative")

    os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
    if args.cuda_alloc_conf:
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", args.cuda_alloc_conf)
    if args.hf_home is not None:
        os.environ["HF_HOME"] = str(args.hf_home.resolve())
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    if numpy_major_version() is not None and numpy_major_version() >= 2:
        print(f"NumPy {np.__version__} is installed, but this Jetson PyTorch build needs NumPy 1.x.")
        print("Transformers Whisper feature extraction calls torch.from_numpy(), which fails in this state.")
        print(f"Fix this virtualenv with: {NUMPY_FIX_COMMAND}")
        return 2

    from huggingface_hub.constants import HF_HOME as effective_hf_home
    import torch

    from speech_to_speech.STT.whisper_stt_handler import WhisperSTTHandler

    resolved_device = args.device
    uses_cuda = resolved_device.startswith("cuda")
    if uses_cuda and not torch.cuda.is_available():
        print("CUDA is not available to PyTorch in this environment.")
        print("For latency testing on Jetson, install/use a CUDA-enabled PyTorch build and rerun this script.")
        print("Temporary CPU fallback: python test_stt_wav.py --device cpu --dtype float32 --no-warmup --runs 1")
        return 2

    if uses_cuda:
        torch.backends.cudnn.benchmark = True
        try:
            free_bytes, total_bytes = torch.cuda.mem_get_info()
            print(f"CUDA memory before model load: free={format_gib(free_bytes)} total={format_gib(total_bytes)}")
        except RuntimeError as exc:
            print(f"CUDA memory before model load: unavailable ({exc})")

    def sync_device() -> None:
        if uses_cuda:
            torch.cuda.synchronize()

    class LatencyWhisperSTTHandler(WhisperSTTHandler):
        def __init__(self, *, run_warmup: bool, fast_process: bool) -> None:
            self.run_warmup = run_warmup
            self.fast_process = fast_process

        def warmup(self) -> None:
            if self.run_warmup:
                super().warmup()

        def process(self, vad_audio: VADAudio):  # type: ignore[override]
            if not self.fast_process:
                yield from super().process(vad_audio)
                return

            input_features = self.prepare_model_inputs(vad_audio.audio)
            with torch.inference_mode():
                pred_ids = self.model.generate(input_features, **self.gen_kwargs)
            pred_text = self.processor.batch_decode(
                pred_ids,
                skip_special_tokens=True,
                decode_with_timestamps=False,
            )[0].strip()
            if pred_text:
                yield Transcription(
                    text=pred_text,
                    language_code=self.start_language,
                    turn_id=vad_audio.turn_id,
                    turn_revision=vad_audio.turn_revision,
                    speech_stopped_at_s=vad_audio.created_at_s,
                )

    sample_rate, audio = load_wav_mono_float32(wav_path)
    print(f"Audio: {wav_path}")
    print(f"Audio format: {format_ms(len(audio), sample_rate)}, mono float32, {sample_rate}Hz")
    print(
        "STT config: "
        f"model={args.model_name} "
        f"language={args.language} "
        f"device={resolved_device} "
        f"dtype={args.dtype} "
        f"num_beams={args.num_beams} "
        f"max_new_tokens={args.max_new_tokens} "
        f"fast_process={args.fast_process}"
    )
    print(f"HF_HOME: {effective_hf_home}")
    print(f"Offline cache only: {args.offline}")
    if uses_cuda and args.cuda_alloc_conf:
        print(f"PYTORCH_CUDA_ALLOC_CONF: {os.environ.get('PYTORCH_CUDA_ALLOC_CONF')}")
    print("Loading Whisper STT handler...")

    handler = LatencyWhisperSTTHandler(
        run_warmup=args.warmup,
        fast_process=args.fast_process,
    )
    setup_started = time.perf_counter()
    try:
        handler.setup(
            model_name=args.model_name,
            device=resolved_device,
            torch_dtype=args.dtype,
            compile_mode=args.compile_mode,
            language=args.language,
            gen_kwargs={
                "max_new_tokens": args.max_new_tokens,
                "num_beams": args.num_beams,
                "return_timestamps": False,
                "task": "transcribe",
            },
        )
    except RuntimeError as exc:
        if uses_cuda and looks_like_cuda_allocation_error(exc):
            print()
            print("CUDA allocation failed while loading the Whisper model.")
            print("On Jetson Orin Nano, openai/whisper-large-v3-turbo can exceed available contiguous CUDA memory.")
            print("Try after reboot/closing other GPU users, or test STT with a smaller cached model:")
            print("  python test_stt_wav.py --model-name openai/whisper-small --no-warmup --real-audio-warmups 0 --runs 1")
            print("For the full speech-to-speech demo, use the same smaller STT model or a quantized faster-whisper path.")
            return 2
        raise
    print(f"Model ready in {time.perf_counter() - setup_started:.2f}s")

    def transcribe_once(run_label: str) -> tuple[Transcription | None, float]:
        vad_audio = VADAudio(audio=audio, mode="final", turn_id=run_label, turn_revision=0)
        sync_device()
        transcription_started = time.perf_counter()
        outputs = list(handler.process(vad_audio))
        sync_device()
        elapsed = time.perf_counter() - transcription_started
        transcriptions = [output for output in outputs if isinstance(output, Transcription)]
        if not transcriptions:
            return None, elapsed
        return transcriptions[-1], elapsed

    for index in range(args.real_audio_warmups):
        transcription, elapsed = transcribe_once(f"warmup_{index + 1}")
        text = transcription.text if transcription is not None else "none"
        print(f"Warmup {index + 1}: {elapsed:.3f}s -> {text}")

    measured: list[tuple[Transcription, float]] = []
    for index in range(args.runs):
        transcription, elapsed = transcribe_once(f"measured_{index + 1}")
        if transcription is None:
            print(f"Run {index + 1}: {elapsed:.3f}s -> none")
            continue
        measured.append((transcription, elapsed))
        print(f"Run {index + 1}: {elapsed:.3f}s -> {transcription.text}")

    if not measured:
        print("Transcription: none")
        return 1

    latencies = [elapsed for _transcription, elapsed in measured]
    transcription = measured[-1][0]
    print(f"Latency summary: {summarize_latencies(latencies)}")
    print(f"Final transcription: {transcription.text}")
    if transcription.language_code:
        print(f"Detected language: {transcription.language_code}")

    if args.transcript_output is not None:
        transcript_output = args.transcript_output.resolve()
        transcript_output.parent.mkdir(parents=True, exist_ok=True)
        transcript_output.write_text(transcription.text + "\n", encoding="utf-8")
        print(f"Transcript written: {transcript_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
