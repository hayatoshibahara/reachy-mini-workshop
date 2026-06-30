"""Record a short video through the Reachy Mini daemon media path.

Unlike ``record_video.py``, this script does not open ``/dev/video0`` directly.
It connects through the Reachy Mini SDK and reads frames from the daemon-backed
media manager with ``mini.media.get_frame()``.

Examples:
    python record_video_with_daemon.py
    python record_video_with_daemon.py --duration 3 --output clip.mp4
    python record_video_with_daemon.py --backend webrtc --connection-mode network
"""

import argparse
import importlib.util
import pkgutil
import sys
import time
from typing import NoReturn


if not hasattr(pkgutil, "get_loader"):
    # PyGObject versions used by Reachy Mini still import pkgutil.get_loader(),
    # which was removed in Python 3.14. Recreate the tiny part gi needs.
    def _get_loader(module_name: str):
        spec = importlib.util.find_spec(module_name)
        return None if spec is None else spec.loader

    pkgutil.get_loader = _get_loader  # type: ignore[attr-defined]

try:
    import cv2
except ImportError:
    print("Error: OpenCV is required for this script but not installed.")
    print("Install it with: pip install reachy_mini[opencv]")
    sys.exit(1)

from reachy_mini import ReachyMini


def die(message: str) -> NoReturn:
    """Print an error message and exit."""
    sys.exit(f"Error: {message}")


def resolve_connection_mode(backend: str, connection_mode: str | None) -> str:
    """Resolve the SDK connection mode for the selected media backend."""
    if connection_mode is not None:
        return connection_mode
    if backend == "local":
        return "localhost_only"
    return "network"


def wait_for_first_frame(mini: ReachyMini, timeout: float):
    """Wait until the daemon media path produces a frame."""
    print(f"Waiting up to {timeout:.1f}s for the first camera frame...")
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        frame = mini.media.get_frame()
        if frame is not None:
            return frame
        time.sleep(0.05)

    die(f"no camera frame received within {timeout:.1f}s")


def validate_frame(frame) -> tuple[int, int]:
    """Return ``(width, height)`` after validating an OpenCV BGR frame."""
    if len(frame.shape) != 3 or frame.shape[2] != 3:
        die(f"expected a BGR frame with 3 channels, got shape {frame.shape}")
    height, width = frame.shape[:2]
    return width, height


def open_video_writer(output: str, fps: float, size: tuple[int, int]):
    """Open an MP4 writer for BGR frames."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output, fourcc, fps, size)
    if not writer.isOpened():
        writer.release()
        die(
            f"could not open video writer for {output!r}. "
            "Check the output path and OpenCV/FFmpeg installation."
        )
    return writer


def main(
    backend: str,
    duration: float,
    output: str,
    fps: float,
    timeout: float,
    host: str,
    port: int,
    connection_mode: str | None,
) -> None:
    """Record a video using SDK frames served by the Reachy Mini daemon."""
    if duration <= 0:
        die("--duration must be greater than 0")
    if fps <= 0:
        die("--fps must be greater than 0")
    if timeout <= 0:
        die("--timeout must be greater than 0")

    resolved_connection_mode = resolve_connection_mode(backend, connection_mode)
    print(
        "Connecting to Reachy Mini daemon "
        f"(media_backend={backend}, connection_mode={resolved_connection_mode}, "
        f"host={host}, port={port})"
    )

    with ReachyMini(
        media_backend=backend,
        connection_mode=resolved_connection_mode,
        host=host,
        port=port,
    ) as mini:
        frame = wait_for_first_frame(mini, timeout)
        size = validate_frame(frame)
        writer = open_video_writer(output, fps, size)

        target_frames = max(1, round(duration * fps))
        frame_period = 1.0 / fps
        last_frame = frame
        frames_written = 0
        start_time = time.monotonic()

        print(
            f"Recording {duration:.1f}s at {fps:.1f}fps "
            f"({size[0]}x{size[1]}) -> {output}"
        )

        try:
            for frame_index in range(target_frames):
                scheduled_time = start_time + frame_index * frame_period
                sleep_for = scheduled_time - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)

                frame = mini.media.get_frame()
                if frame is not None:
                    last_frame = frame

                writer.write(last_frame)
                frames_written += 1
        finally:
            writer.release()

    elapsed = time.monotonic() - start_time
    effective_fps = frames_written / elapsed if elapsed > 0 else 0.0
    print(
        f"Saved {output}: {frames_written} frames in {elapsed:.1f}s "
        f"(~{effective_fps:.1f} fps effective)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Record video through the Reachy Mini SDK/daemon media path.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--backend",
        choices=["local", "webrtc"],
        default="local",
        help="Daemon media backend to use. 'default' is intentionally omitted.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3.0,
        help="Recording duration in seconds.",
    )
    parser.add_argument(
        "--output",
        default="reachy_mini_video_with_daemon.mp4",
        help="Output MP4 filename.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Output video frame rate.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for the first daemon camera frame.",
    )
    parser.add_argument(
        "--host",
        default="reachy-mini.local",
        help="Daemon host, used for network/WebRTC connections.",
    )
    parser.add_argument("--port", type=int, default=8000, help="Daemon HTTP port.")
    parser.add_argument(
        "--connection-mode",
        choices=["localhost_only", "network", "auto"],
        default=None,
        help=(
            "SDK connection mode. If omitted, local uses localhost_only and "
            "webrtc uses network."
        ),
    )

    args = parser.parse_args()
    main(
        backend=args.backend,
        duration=args.duration,
        output=args.output,
        fps=args.fps,
        timeout=args.timeout,
        host=args.host,
        port=args.port,
        connection_mode=args.connection_mode,
    )
