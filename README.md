# a-term

A tiny no-hassle serial terminal for Linux that **follows the port**.

## Why

The Arduino IDE serial monitor (and most others) lose the connection every time
you re-upload a sketch — the kernel briefly tears down `/dev/ttyACM0` and
re-enumerates the device, sometimes under a new name. You then have to
manually reconnect, every single time.

`a-term` watches a stable `/dev/serial/by-id/` symlink instead. When the
device disappears, it prints a notice and waits. When it comes back, it
reattaches automatically — even if the underlying `/dev/tty*` node changed.

## Install

```bash
pip install -r requirements.txt
```

(Only dependency is `pyserial`.)

## Usage

List available USB-serial devices:

```bash
./a-term.py --list
```

Connect to the first device whose by-id name contains `Arduino`:

```bash
./a-term.py Arduino
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--baud N` | `115200` | Baud rate |
| `--eol {none,lf,cr,crlf}` | `lf` | Line ending appended on send |
| `--log FILE` | _off_ | Append received bytes to FILE (binary, untimestamped) |
| `--no-timestamps` | _on_ | Disable the `[   ms] ` prefix on each received line |
| `--list` | — | List devices and exit |

Type a line, press Enter to send. Ctrl-C exits.

## Status messages

Printed to stderr on every state transition:

```
[a-term] waiting for device matching 'Arduino'...
[a-term] connected: usb-Arduino_LLC_Arduino_Uno_...-if00 -> /dev/ttyACM0 @ 115200
[a-term] disconnected, waiting for reappearance...
```

## Behaviour notes

- If the substring matches multiple devices at startup, the program lists
  them and exits — refine the substring to pick exactly one.
- Once locked onto a device (matched by full by-id path), `a-term` follows
  that exact device for the life of the session. Plugging in a *different*
  matching device will not be picked up — restart to re-lock.
- Input is line-buffered: the terminal handles editing; nothing is sent until
  you press Enter.

## Limitations (v1)

- Linux only — depends on the `/dev/serial/by-id/` udev convention.
- Line-buffered input only (no raw mode for shell-over-serial sessions).
- No pinned status bar or hotkey menu.
- Permissions: your user needs read/write on the tty device (typically by
  joining the `dialout` or `uucp` group).
