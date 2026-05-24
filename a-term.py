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
import time
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
    p.add_argument("--no-timestamps", dest="timestamps", action="store_false",
                   help="Disable timestamp prefix on each received line "
                        "(default: enabled)")
    p.add_argument("--clock", action="store_true",
                   help="Use wall-clock HH:MM:SS.mmm format instead of "
                        "milliseconds since start")
    p.add_argument("--list", action="store_true",
                   help="List devices in /dev/serial/by-id/ and exit")
    p.add_argument("--gui", action="store_true",
                   help="Launch GUI mode (Tkinter) instead of CLI")
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

    if args.gui:
        return run_gui(args)

    if not args.substring:
        print("a-term: substring argument required (or use --list or --gui)",
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

    start_ts = time.monotonic()
    line_start = True

    def line_prefix() -> bytes:
        if args.clock:
            now = time.time()
            ms = int((now - int(now)) * 1000)
            return (time.strftime("[%H:%M:%S", time.localtime(now))
                    + f".{ms:03d}] ").encode("ascii")
        ms = int((time.monotonic() - start_ts) * 1000)
        return f"[{ms:>9}] ".encode("ascii")

    def emit_rx(data: bytes) -> None:
        nonlocal line_start
        if not data:
            return
        out = sys.stdout.buffer
        if not args.timestamps:
            out.write(data)
            sys.stdout.flush()
            return
        i = 0
        n = len(data)
        while i < n:
            if line_start:
                out.write(line_prefix())
                line_start = False
            nl = data.find(b"\n", i)
            if nl == -1:
                out.write(data[i:])
                break
            out.write(data[i:nl + 1])
            line_start = True
            i = nl + 1
        sys.stdout.flush()

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
                        line_start = True
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
                    emit_rx(data)
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


def run_gui(args: argparse.Namespace) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError:
        print("a-term: tkinter not available. On Debian/Ubuntu: "
              "sudo apt install python3-tk", file=sys.stderr)
        return 1
    import queue
    import threading

    rx_q: queue.Queue = queue.Queue()
    status_q: queue.Queue = queue.Queue()
    tx_q: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    cfg_lock = threading.Lock()
    cfg = {
        "substring": (args.substring or "").strip(),
        "baud": args.baud,
        "eol": EOL_MAP[args.eol],
        "log_path": args.log,
    }

    start_ts = time.monotonic()

    RESET_MARKER = b"\x00__RESET__\x00"

    def supervisor():
        ser: Optional[serial.Serial] = None
        target_path: Optional[str] = None
        state = "WAITING"
        last_open_error: Optional[str] = None
        last_substring: Optional[str] = None
        last_baud: Optional[int] = None
        log_fp = None
        log_path_open = None

        def close_ser():
            nonlocal ser
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None

        try:
            while not stop_event.is_set():
                with cfg_lock:
                    substring = cfg["substring"]
                    baud = cfg["baud"]
                    log_path = cfg["log_path"]

                if log_path != log_path_open:
                    if log_fp is not None:
                        try:
                            log_fp.close()
                        except Exception:
                            pass
                        log_fp = None
                    if log_path:
                        try:
                            log_fp = open(log_path, "ab", buffering=0)
                        except OSError as e:
                            status_q.put(("error", f"log open failed: {e}"))
                    log_path_open = log_path

                if substring != last_substring or baud != last_baud:
                    close_ser()
                    target_path = None
                    state = "WAITING"
                    last_open_error = None
                    last_substring = substring
                    last_baud = baud
                    if substring:
                        status_q.put(("waiting",
                                      f"waiting for '{substring}'..."))
                    else:
                        status_q.put(("idle", "no device selected"))

                if not substring:
                    stop_event.wait(0.25)
                    continue

                if state == "WAITING":
                    if target_path is None:
                        matches = find_matches(substring)
                        if len(matches) == 1:
                            target_path = matches[0]
                            last_open_error = None
                        elif len(matches) > 1:
                            if last_open_error != "__multi__":
                                status_q.put((
                                    "error",
                                    f"multiple devices match '{substring}'; "
                                    "refine substring"))
                                last_open_error = "__multi__"
                    if target_path is not None and os.path.exists(target_path):
                        try:
                            ser = serial.Serial(target_path, baudrate=baud,
                                                timeout=0, exclusive=False)
                            state = "CONNECTED"
                            last_open_error = None
                            real = os.path.realpath(target_path)
                            rx_q.put(RESET_MARKER)
                            status_q.put((
                                "connected",
                                f"connected: {os.path.basename(target_path)} "
                                f"-> {real} @ {baud}"))
                        except (serial.SerialException, OSError) as e:
                            msg = str(e)
                            if msg != last_open_error:
                                status_q.put(("error", f"open failed: {msg}"))
                                last_open_error = msg

                elif state == "CONNECTED":
                    if target_path is None or not os.path.exists(target_path):
                        close_ser()
                        state = "WAITING"
                        status_q.put(("waiting",
                                      "disconnected, waiting..."))
                        continue

                # Drain pending TX
                while True:
                    try:
                        chunk = tx_q.get_nowait()
                    except queue.Empty:
                        break
                    if state == "CONNECTED" and ser is not None:
                        try:
                            ser.write(chunk)
                        except (serial.SerialException, OSError) as e:
                            status_q.put(("error", f"write failed: {e}"))
                            close_ser()
                            state = "WAITING"
                            break

                # RX with short poll
                if state == "CONNECTED" and ser is not None:
                    try:
                        ready, _, _ = select.select(
                            [ser.fileno()], [], [], 0.05)
                    except (InterruptedError, OSError):
                        ready = []
                    if ready:
                        try:
                            data = os.read(ser.fileno(), 4096)
                            if not data:
                                raise EOFError
                            rx_q.put(data)
                            if log_fp is not None:
                                log_fp.write(data)
                        except (OSError, serial.SerialException, EOFError):
                            close_ser()
                            state = "WAITING"
                            status_q.put(("waiting",
                                          "disconnected, waiting..."))
                else:
                    stop_event.wait(0.1)
        finally:
            close_ser()
            if log_fp is not None:
                try:
                    log_fp.close()
                except Exception:
                    pass

    # ── Build window ─────────────────────────────────────────────────
    root = tk.Tk()
    root.title("a-term")
    root.geometry("960x600")

    top = ttk.Frame(root, padding=(8, 6, 8, 4))
    top.pack(fill=tk.X)

    ttk.Label(top, text="Device:").pack(side=tk.LEFT)
    device_var = tk.StringVar(value=cfg["substring"])
    device_combo = ttk.Combobox(top, textvariable=device_var, width=56)
    device_combo.pack(side=tk.LEFT, padx=(4, 8))

    ttk.Label(top, text="Baud:").pack(side=tk.LEFT)
    baud_var = tk.StringVar(value=str(cfg["baud"]))
    baud_entry = ttk.Entry(top, textvariable=baud_var, width=8)
    baud_entry.pack(side=tk.LEFT, padx=(4, 8))

    apply_btn = ttk.Button(top, text="Apply")
    apply_btn.pack(side=tk.LEFT, padx=(0, 8))

    status_var = tk.StringVar(value="idle")
    status_lbl = ttk.Label(top, textvariable=status_var, foreground="#666")
    status_lbl.pack(side=tk.LEFT, padx=(8, 0))

    rx_text = ScrolledText(root, wrap=tk.NONE,
                           font=("monospace", 10), state=tk.DISABLED,
                           background="#fafafa", foreground="#222")
    rx_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

    bottom = ttk.Frame(root, padding=(8, 4, 8, 8))
    bottom.pack(fill=tk.X)

    send_var = tk.StringVar()
    send_entry = ttk.Entry(bottom, textvariable=send_var)
    send_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

    eol_var = tk.StringVar(value=args.eol)
    eol_combo = ttk.Combobox(bottom, textvariable=eol_var,
                             values=list(EOL_MAP.keys()), width=6,
                             state="readonly")
    eol_combo.pack(side=tk.LEFT, padx=4)

    send_btn = ttk.Button(bottom, text="Send")
    send_btn.pack(side=tk.LEFT)

    ts_var = tk.BooleanVar(value=args.timestamps)
    clock_var = tk.BooleanVar(value=args.clock)

    line_start = True

    def line_prefix() -> str:
        if clock_var.get():
            now = time.time()
            ms = int((now - int(now)) * 1000)
            return time.strftime("[%H:%M:%S",
                                 time.localtime(now)) + f".{ms:03d}] "
        ms = int((time.monotonic() - start_ts) * 1000)
        return f"[{ms:>9}] "

    def append_rx(data: bytes) -> None:
        nonlocal line_start
        if not data:
            return
        text = data.decode("utf-8", errors="replace")
        rx_text.config(state=tk.NORMAL)
        try:
            if not ts_var.get():
                rx_text.insert(tk.END, text)
            else:
                i = 0
                n = len(text)
                while i < n:
                    if line_start:
                        rx_text.insert(tk.END, line_prefix())
                        line_start = False
                    nl = text.find("\n", i)
                    if nl == -1:
                        rx_text.insert(tk.END, text[i:])
                        break
                    rx_text.insert(tk.END, text[i:nl + 1])
                    line_start = True
                    i = nl + 1
            rx_text.see(tk.END)
        finally:
            rx_text.config(state=tk.DISABLED)

    def clear_rx() -> None:
        nonlocal line_start
        rx_text.config(state=tk.NORMAL)
        rx_text.delete("1.0", tk.END)
        rx_text.config(state=tk.DISABLED)
        line_start = True

    def refresh_devices() -> None:
        device_combo["values"] = [os.path.basename(p)
                                  for p, _ in list_by_id()]

    def apply_changes(*_: object) -> None:
        nonlocal line_start
        try:
            baud_val = int(baud_var.get())
            if baud_val <= 0:
                raise ValueError
        except ValueError:
            status_var.set("invalid baud")
            status_lbl.config(foreground="#a00")
            return
        with cfg_lock:
            cfg["substring"] = device_var.get().strip()
            cfg["baud"] = baud_val
        line_start = True

    def do_send(*_: object) -> None:
        text = send_var.get()
        eol_bytes = EOL_MAP[eol_var.get()]
        payload = text.encode("utf-8", errors="replace") + eol_bytes
        tx_q.put(payload)
        send_var.set("")

    apply_btn.config(command=apply_changes)
    send_btn.config(command=do_send)
    send_entry.bind("<Return>", do_send)
    device_combo.bind("<Return>", apply_changes)
    baud_entry.bind("<Return>", apply_changes)

    # Menu
    menubar = tk.Menu(root)
    file_menu = tk.Menu(menubar, tearoff=0)
    file_menu.add_command(label="Refresh devices", command=refresh_devices)
    file_menu.add_separator()
    file_menu.add_command(label="Quit", command=lambda: on_close())
    menubar.add_cascade(label="File", menu=file_menu)

    options_menu = tk.Menu(menubar, tearoff=0)
    options_menu.add_checkbutton(label="Show timestamps", variable=ts_var)
    options_menu.add_checkbutton(label="Wall-clock format", variable=clock_var)
    options_menu.add_separator()
    options_menu.add_command(label="Clear", command=clear_rx)
    menubar.add_cascade(label="Options", menu=options_menu)
    root.config(menu=menubar)

    refresh_devices()

    STATUS_COLORS = {
        "idle": "#666",
        "waiting": "#a60",
        "connected": "#0a0",
        "error": "#a00",
    }

    def drain() -> None:
        nonlocal line_start
        try:
            while True:
                kind, msg = status_q.get_nowait()
                status_var.set(msg)
                status_lbl.config(foreground=STATUS_COLORS.get(kind, "#666"))
        except queue.Empty:
            pass

        pending: list[bytes] = []
        try:
            while True:
                d = rx_q.get_nowait()
                if d == RESET_MARKER:
                    if pending:
                        append_rx(b"".join(pending))
                        pending = []
                    line_start = True
                else:
                    pending.append(d)
        except queue.Empty:
            pass
        if pending:
            append_rx(b"".join(pending))

        root.after(30, drain)

    def on_close() -> None:
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(30, drain)

    t = threading.Thread(target=supervisor, daemon=True)
    t.start()

    send_entry.focus_set()
    root.mainloop()
    stop_event.set()
    t.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
