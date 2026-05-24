#!/usr/bin/env python3
"""a-term — follow-the-port serial terminal.

Watches a /dev/serial/by-id/ entry by substring match and transparently
reconnects when the device disappears and reappears (e.g. after an Arduino
upload, a reset, or a cable bump).
"""
from __future__ import annotations

import argparse
import os
import select
import signal
import sys
from typing import Optional

try:
    import serial
except ImportError:
    print("a-term: missing dependency 'pyserial'. Install with: "
          "pip install pyserial", file=sys.stderr)
    sys.exit(1)


BY_ID_DIR = "/dev/serial/by-id"
POLL_INTERVAL = 0.25
EOL_MAP = {"none": b"", "lf": b"\n", "cr": b"\r", "crlf": b"\r\n"}


def list_by_id() -> list[tuple[str, str]]:
    try:
        entries = sorted(os.listdir(BY_ID_DIR))
    except FileNotFoundError:
        return []
    out: list[tuple[str, str]] = []
    for name in entries:
        path = os.path.join(BY_ID_DIR, name)
        try:
            real = os.path.realpath(path)
            if not os.path.exists(real):
                real = "<broken>"
        except OSError:
            real = "<error>"
        out.append((path, real))
    return out


def find_matches(substring: str) -> list[str]:
    return [p for p, _ in list_by_id() if substring in os.path.basename(p)]


def print_status(msg: str) -> None:
    sys.stderr.write(f"[a-term] {msg}\n")
    sys.stderr.flush()


def _close(ser: Optional["serial.Serial"]) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="a-term",
        description="Tiny serial terminal that follows a /dev/serial/by-id/ "
                    "entry across reconnects.",
    )
    p.add_argument("substring", nargs="?",
                   help="Substring to match against /dev/serial/by-id/ entries")
    p.add_argument("--baud", type=int, default=115200,
                   help="Baud rate (default: 115200)")
    p.add_argument("--eol", choices=list(EOL_MAP), default="lf",
                   help="Line ending appended to outgoing lines (default: lf)")
    p.add_argument("--log", metavar="FILE",
                   help="Append received bytes to FILE (binary, unbuffered)")
    p.add_argument("--list", action="store_true",
                   help="List devices in /dev/serial/by-id/ and exit")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.list:
        entries = list_by_id()
        if not entries:
            print(f"(no devices in {BY_ID_DIR})")
            return 0
        width = max(len(os.path.basename(p)) for p, _ in entries)
        for path, real in entries:
            print(f"{os.path.basename(path):<{width}}  -> {real}")
        return 0

    if not args.substring:
        print("a-term: substring argument required (or use --list)",
              file=sys.stderr)
        return 2

    eol = EOL_MAP[args.eol]

    log_fp = open(args.log, "ab", buffering=0) if args.log else None

    r_sig, w_sig = os.pipe()
    os.set_blocking(r_sig, False)
    os.set_blocking(w_sig, False)
    stop = False

    def on_signal(_signum, _frame):
        nonlocal stop
        stop = True
        try:
            os.write(w_sig, b"x")
        except OSError:
            pass

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    initial_matches = find_matches(args.substring)
    if len(initial_matches) > 1:
        print(f"a-term: substring '{args.substring}' matches multiple devices:",
              file=sys.stderr)
        for m in initial_matches:
            print(f"  {os.path.basename(m)} -> {os.path.realpath(m)}",
                  file=sys.stderr)
        print("Refine the substring to pick exactly one.", file=sys.stderr)
        return 2

    # Lock the target by-id path once we've seen exactly one match. After
    # locking, we follow that specific device for the rest of the session,
    # even across cable pulls and uploads.
    target_path: Optional[str] = initial_matches[0] if initial_matches else None
    state = "WAITING"
    ser: Optional[serial.Serial] = None
    last_open_error: Optional[str] = None

    print_status(f"waiting for device matching '{args.substring}'...")

    try:
        while not stop:
            if state == "WAITING":
                if target_path is None:
                    matches = find_matches(args.substring)
                    if len(matches) > 1:
                        if last_open_error != "__multiple__":
                            print_status(
                                f"multiple devices match '{args.substring}'; "
                                "refine the substring or unplug extras")
                            last_open_error = "__multiple__"
                    elif len(matches) == 1:
                        target_path = matches[0]
                        last_open_error = None

                if target_path is not None and os.path.exists(target_path):
                    try:
                        ser = serial.Serial(target_path, baudrate=args.baud,
                                            timeout=0, exclusive=False)
                        state = "CONNECTED"
                        last_open_error = None
                        real = os.path.realpath(target_path)
                        print_status(
                            f"connected: {os.path.basename(target_path)} "
                            f"-> {real} @ {args.baud}")
                    except (serial.SerialException, OSError) as e:
                        msg = str(e)
                        if msg != last_open_error:
                            print_status(f"open failed: {msg}")
                            last_open_error = msg

            elif state == "CONNECTED":
                if target_path is None or not os.path.exists(target_path):
                    _close(ser)
                    ser = None
                    state = "WAITING"
                    print_status("disconnected, waiting for reappearance...")
                    continue

            fds = [sys.stdin.fileno(), r_sig]
            if state == "CONNECTED" and ser is not None:
                fds.append(ser.fileno())

            try:
                ready, _, _ = select.select(fds, [], [], POLL_INTERVAL)
            except (InterruptedError, OSError):
                continue

            if r_sig in ready:
                try:
                    while os.read(r_sig, 64):
                        pass
                except BlockingIOError:
                    pass

            if sys.stdin.fileno() in ready:
                line = sys.stdin.readline()
                if line == "":
                    stop = True
                    break
                if state == "CONNECTED" and ser is not None:
                    payload = (line.rstrip("\r\n")
                               .encode("utf-8", errors="replace") + eol)
                    try:
                        ser.write(payload)
                    except (serial.SerialException, OSError) as e:
                        print_status(f"write failed: {e}")
                        _close(ser)
                        ser = None
                        state = "WAITING"
                else:
                    print_status("not connected; discarded input")

            if (state == "CONNECTED" and ser is not None
                    and ser.fileno() in ready):
                try:
                    data = os.read(ser.fileno(), 4096)
                    if not data:
                        raise EOFError
                    sys.stdout.buffer.write(data)
                    sys.stdout.flush()
                    if log_fp is not None:
                        log_fp.write(data)
                except (OSError, serial.SerialException, EOFError):
                    _close(ser)
                    ser = None
                    state = "WAITING"
                    print_status("disconnected, waiting for reappearance...")
    finally:
        _close(ser)
        if log_fp is not None:
            log_fp.close()
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    return 0


if __name__ == "__main__":
    sys.exit(main())
