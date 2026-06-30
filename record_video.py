"""Record a video from the Reachy Mini camera via GStreamer (no daemon needed).

Captures MJPEG from the USB camera, decodes it, encodes to H.264, and writes an
MP4 — driving GStreamer directly through PyGObject (``gi``).

Why not OpenCV?  The OpenCV in ``.reachy_mini_env`` is built **without** GStreamer
support (``cv2.getBuildInformation()`` shows ``GStreamer: NO``), so
``cv2.VideoCapture(..., cv2.CAP_GSTREAMER)`` cannot open the camera there.  ``gi``
GStreamer 1.24 *is* available in the venv, so this script works in both
``.reachy_mini_env`` and the system ``python3``.

Run this only while the Reachy Mini daemon is NOT running — the daemon owns the
camera device and will not release it.

Supported MJPEG modes on the Reachy Mini camera:
    1920x1080 @ 60   (default)
    3840x2592 @ 30
    3840x2160 @ 30
    3264x2448 @ 30

Note: H.264 software encoding (openh264) of 1920x1080 is CPU-heavy on the Jetson,
so the effective framerate may be below the camera's 60fps.  Playback speed stays
correct (timestamps are real-time); the video just contains fewer frames.

Examples:
    python record_video.py
    python record_video.py --duration 30 --output clip.mp4
    python record_video.py --width 3840 --height 2160 --fps 30
"""

import argparse
import os
import signal
import sys
import time

# 1.24 plugins live on the default /usr/local path; the Rust + PipeWire plugins
# live here.  setdefault lets an existing GST_PLUGIN_PATH (~/.bashrc) win.
os.environ.setdefault("GST_PLUGIN_PATH", "/opt/gst-plugins-rs/lib/aarch64-linux-gnu")

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402


def build_pipeline_desc(device: str, width: int, height: int, fps: int, output: str) -> str:
    """v4l2 MJPEG -> decode -> H.264 (openh264) -> MP4 file."""
    return (
        f"v4l2src device={device} ! "
        f"image/jpeg,width={width},height={height},framerate={fps}/1 ! "
        "jpegdec ! videoconvert ! "
        "openh264enc name=enc ! h264parse ! mp4mux ! "
        f"filesink location={output}"
    )


def main(device: str, duration: int, output: str, width: int, height: int, fps: int) -> None:
    Gst.init(None)

    desc = build_pipeline_desc(device, width, height, fps, output)
    print(f"Opening camera {device} ({width}x{height}@{fps}fps) -> {output}")
    try:
        pipeline = Gst.parse_launch(desc)
    except GLib.Error as e:
        sys.exit(
            f"Error: could not build the GStreamer pipeline: {e.message}\n"
            "  - A required element may be missing (jpegdec / openh264enc / mp4mux)."
        )

    loop = GLib.MainLoop()
    state = {"frames": 0, "error": None}

    # Count encoded frames to report the effective framerate.
    enc = pipeline.get_by_name("enc")
    if enc is not None:
        sink_pad = enc.get_static_pad("sink")
        if sink_pad is not None:
            sink_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                lambda pad, info: (state.__setitem__("frames", state["frames"] + 1), Gst.PadProbeReturn.OK)[1],
            )

    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(_bus, msg: Gst.Message) -> None:
        if msg.type == Gst.MessageType.EOS:
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            state["error"] = err.message
            loop.quit()

    bus.connect("message", on_message)

    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        sys.exit(
            f"Error: could not start the camera at {device}.\n"
            "  - Is the Reachy Mini daemon running? Stop it first (it holds the camera).\n"
            f"  - Is {width}x{height}@{fps} a supported camera mode? (see this file's docstring)"
        )

    print(f"Recording {duration}s ... press Ctrl-C to stop early.")
    start = time.time()

    # Send EOS after `duration` so the MP4 is finalized cleanly (moov atom written).
    def send_eos() -> bool:
        pipeline.send_event(Gst.Event.new_eos())
        return False  # one-shot

    GLib.timeout_add_seconds(duration, send_eos)

    def on_sigint(_signum, _frame) -> None:
        print("\nStopping early ...")
        pipeline.send_event(Gst.Event.new_eos())

    signal.signal(signal.SIGINT, on_sigint)

    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)

    elapsed = time.time() - start
    if state["error"]:
        msg = state["error"]
        hint = ""
        if any(w in msg.lower() for w in ("busy", "resource", "allocate", "device")):
            hint = "\n  -> Camera busy. Stop the Reachy Mini daemon first (it owns the camera)."
        sys.exit(f"Error: {msg}{hint}")

    frames = state["frames"]
    eff = frames / elapsed if elapsed > 0 else 0.0
    print(f"Saved {output}: {frames} frames in {elapsed:.1f}s (~{eff:.1f} fps effective).")
    if fps and eff < fps * 0.6:
        print(
            f"  Note: effective fps (~{eff:.1f}) is below the camera's {fps}fps because\n"
            "  software H.264 encoding can't keep up at this resolution. Playback speed is\n"
            "  still correct; lower --width/--height for a higher frame count."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Record a video from the Reachy Mini camera via GStreamer (PyGObject).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--device", default="/dev/video0", help="Camera device.")
    parser.add_argument("--duration", type=int, default=10, help="Recording duration in seconds.")
    parser.add_argument("--output", default="reachy_mini_video.mp4", help="Output filename.")
    parser.add_argument("--width", type=int, default=1920, help="Capture width.")
    parser.add_argument("--height", type=int, default=1080, help="Capture height.")
    parser.add_argument("--fps", type=int, default=60, help="Capture framerate.")
    args = parser.parse_args()

    main(args.device, args.duration, args.output, args.width, args.height, args.fps)
