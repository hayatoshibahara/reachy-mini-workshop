"""Loop-play random dance (and emotion) motions on Reachy Mini.

Connects to a running daemon (real robot or simulation) and plays moves from
the HuggingFace recorded-move libraries forever, in a reshuffled order so the
same move is not repeated until every move has been played once.

Prerequisites
-------------
A daemon must already be running. For the MuJoCo simulation on macOS:

    mjpython -m reachy_mini.daemon.app.main --sim --scene minimal

Then, in another terminal:

    python dance_loop.py                 # dances + emotions, shuffled
    python dance_loop.py --dances-only   # only the 19 dance moves
    python dance_loop.py --no-sound      # mute the emotion sound effects
    python dance_loop.py --goto 1.5      # 1.5s smooth move into each motion

Stop with Ctrl+C.
"""

import argparse
import random
import threading
import time

from reachy_mini import ReachyMini
from reachy_mini.motion.recorded_move import RecordedMove, RecordedMoves

DANCES_DATASET = "pollen-robotics/reachy-mini-dances-library"
EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"


def build_playlist(use_dances: bool, use_emotions: bool) -> list[tuple[str, RecordedMoves]]:
    """Load the requested libraries and return a flat list of (move_name, library)."""
    playlist: list[tuple[str, RecordedMoves]] = []

    if use_dances:
        dances = RecordedMoves(DANCES_DATASET)
        playlist += [(name, dances) for name in dances.list_moves()]

    if use_emotions:
        emotions = RecordedMoves(EMOTIONS_DATASET)
        playlist += [(name, emotions) for name in emotions.list_moves()]

    return playlist


def run(reachy: ReachyMini, stop_event: threading.Event, args: argparse.Namespace) -> None:
    """Play moves forever in a reshuffled order until stop_event is set."""
    playlist = build_playlist(not args.emotions_only, not args.dances_only)
    if not playlist:
        print("No moves to play. Check --dances-only / --emotions-only flags.")
        return

    print(f"Loaded {len(playlist)} moves. Starting loop (Ctrl+C to stop)...\n")

    while not stop_event.is_set():
        # Reshuffle each full cycle so every move plays once before any repeat.
        random.shuffle(playlist)

        for move_name, library in playlist:
            if stop_event.is_set():
                break

            move: RecordedMove = library.get(move_name)
            print(f"  ▶ {move_name}")
            # play_move blocks until the motion finishes; initial_goto_duration
            # smoothly moves into the move's starting pose first.
            reachy.play_move(
                move,
                initial_goto_duration=args.goto,
                sound=not args.no_sound,
            )


def main() -> None:
    """Parse arguments, connect to the daemon, and run the loop."""
    parser = argparse.ArgumentParser(
        description="Loop-play random dance/emotion motions on Reachy Mini."
    )
    parser.add_argument(
        "--goto",
        type=float,
        default=1.0,
        help="Seconds to smoothly move into each motion's start pose (default: 1.0).",
    )
    parser.add_argument(
        "--no-sound",
        action="store_true",
        help="Mute the sound effects attached to emotion moves.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dances-only",
        action="store_true",
        help="Play only the dance library.",
    )
    group.add_argument(
        "--emotions-only",
        action="store_true",
        help="Play only the emotion library.",
    )
    args = parser.parse_args()

    print("Connecting to Reachy Mini daemon...")
    try:
        reachy = ReachyMini()
    except Exception as e:  # noqa: BLE001 - surface a friendly hint to the user
        print(f"Failed to connect: {e}")
        print(
            "Is the daemon running? For simulation start it with:\n"
            "  mjpython -m reachy_mini.daemon.app.main --sim --scene minimal"
        )
        return

    stop_event = threading.Event()
    try:
        run(reachy, stop_event, args)
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C)...")
        stop_event.set()
        reachy.cancel_move()  # interrupt the move currently playing
    finally:
        reachy.close()
        print("Disconnected. Bye!")


if __name__ == "__main__":
    main()
