# a-term

A tiny no-hassle serial terminal for Linux that **follows the port**. CLI
or Tkinter GUI.

## Why

The Arduino IDE serial monitor (and most others) lose the connection every
time you re-upload a sketch — the kernel briefly tears down `/dev/ttyACM0`
and re-enumerates the device, sometimes under a new name. You then have to
manually reconnect, every single time. Same story when a cable gets bumped.

`a-term` watches a stable `/dev/serial/by-id/` symlink instead. When the
device disappears it says so and waits; when it comes back it reattaches
automatically — even if the underlying `/dev/tty*` node changed.

## Install

```bash
pip install -r requirements.txt
```

Only dependency is `pyserial`. For GUI mode, also install Tk:

```bash
sudo apt install python3-tk        # Debian / Ubuntu
```

Your user needs read/write on the tty device — on most distros that means
membership in the `dialout` (or `uucp`) group.

## CLI

List available USB-serial devices:

```bash
./a-term.py --list
```

Connect to the first device whose by-id name contains `Arduino`:

```bash
./a-term.py Arduino
```

Type a line, press Enter to send. Ctrl-C exits.

### Flags

| Flag | Default | Description |
|---|---|---|
| `--baud N` | `115200` | Baud rate |
| `--eol {none,lf,cr,crlf}` | `lf` | Line ending appended on send |
| `--log FILE` | _off_ | Append received bytes to FILE (raw, untimestamped) |
| `--no-timestamps` | — | Turn off the per-line timestamp prefix (on by default) |
| `--clock` | _off_ | Use wall-clock `[HH:MM:SS.mmm]` instead of `[ms-since-start]` |
| `--list` | — | List devices and exit |
| `--gui` | — | Launch the Tkinter GUI instead of the CLI |

### Timestamp formats

```
[   123456] hello          # default: ms since program start (9-digit field)
[14:15:11.532] hello       # --clock
hello                      # --no-timestamps
```

### Status messages

Printed to stderr on every state transition (CLI only — the GUI shows the
state in a colored indicator):

```
[a-term] waiting for device matching 'Arduino'...
[a-term] connected: usb-Arduino_LLC_Arduino_Uno_...-if00 -> /dev/ttyACM0 @ 115200
[a-term] released to avrdude (PID 12345), waiting...
[a-term] port free again, resuming...
[a-term] disconnected, waiting for reappearance...
```

## GUI

```bash
./a-term.py --gui              # empty window, pick device from dropdown
./a-term.py --gui Arduino      # opens connected to the first match
```

Layout:

- **Top bar** — device combobox, baud combobox (common rates pre-filled,
  editable for custom), **Apply** button, color-coded status indicator
  (gray idle / amber waiting / green connected / cyan holding for an
  uploader / red error).
- **Middle** — scrolling RX area, optionally prefixed with the same
  timestamp formats as the CLI.
- **Bottom bar** — send entry, EOL dropdown, **Send** button. Press Enter
  in the send field to transmit.
- **Options menu** — toggle timestamps, toggle wall-clock format, Clear.
- **File menu** — Refresh devices (re-scan `/dev/serial/by-id/`), Quit.

Picking a different device or baud from the dropdowns auto-applies. Typing
a custom value requires Enter or the Apply button (so you can fix typos
without disconnecting mid-edit). Closing the window stops the supervisor.

## Behaviour

- If the substring matches multiple devices at startup, the CLI lists them
  and exits. In the GUI you'll see a red "multiple devices match" status —
  refine the substring.
- Once locked onto a device (matched by full by-id path), `a-term` follows
  that exact device for the life of the session. Plugging in a *different*
  matching device won't be picked up — restart (CLI) or change the device
  selection and Apply (GUI).
- Carriage returns (`\r` in `\r\n` line endings) are stripped from the GUI
  display but kept in the `--log` file. The CLI passes them through to the
  terminal driver.
- **Yields the port to uploaders.** Every ~100 ms while connected, `a-term`
  scans `/proc/*/fd` for other processes opening the same tty. If avrdude
  (or bossac, esptool, etc.) grabs the port, `a-term` closes its handle and
  enters a *holding* state until the uploader releases, then reattaches
  automatically. The status indicator turns cyan during the upload and the
  contender's process name is shown. There's a small race window (~100 ms)
  where a few sync bytes may be split, but upload protocols retry through
  this.

## Limitations

- Linux only — depends on the `/dev/serial/by-id/` udev convention.
- Line-buffered input (no raw mode for shell-over-serial sessions).
- CLI has no hotkey menu (Ctrl-C exits, nothing else is wired).
- GUI has no Connect/Disconnect button — close the window to stop.
