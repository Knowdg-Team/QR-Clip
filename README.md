# QR-Clip

**Send your phone's clipboard to your computer by scanning a QR code.**

Your computer shows a QR code. You scan it with your phone, a page opens, you
paste, you tap *Send* — and the text is in your computer's clipboard. No
account, no cloud, no app to install on the phone. Nothing leaves your Wi-Fi.

*[Version française](README.fr.md)*

<p align="center">
  <img src="docs/screenshot.png" alt="The QR-Clip window showing a QR code and the history of received texts" width="380">
</p>

## Why

Copying a long URL, a 2FA code, or a snippet of text from a phone to a laptop
usually means emailing yourself, or signing into a sync service that uploads
your clipboard to someone else's server. QR-Clip is a 30-second, local-only
alternative: run it, scan, paste, done.

## Highlights

- **Zero dependencies.** Pure Python standard library, plus GTK 4 / PyGObject
  for the clipboard — already installed on any GNOME desktop. No `pip install`.
- **The QR encoder is written from scratch** ([`qrmini.py`](qrmini.py), ~330
  lines): byte mode, versions 1–10, Reed–Solomon over GF(256), all eight mask
  patterns with penalty scoring, plus a small PNG writer and an ANSI renderer
  for the terminal. It ships with [real tests](test_qrmini.py).
- **It actually works on Wayland**, which is the hard part — see
  [below](#the-wayland-clipboard-problem).
- **LAN-only and token-protected.** The URL in the QR code carries a random
  token, regenerated on every launch.
- **Headless mode** (`--no-gui`): the QR code is rendered in the terminal.

## Requirements

- Python 3.7+ (3.11 tested)
- PyGObject / GTK 4 (`python3-gi`, `gir1.2-gtk-4.0`) to write to the clipboard
- Phone and computer on the same Wi-Fi

Tested on Debian 12 / GNOME 43 / Wayland.

## Quick start

```bash
git clone https://github.com/Knowdg-Team/QR-Clip.git
cd QR-Clip
./qrclip.py
```

A window opens with the QR code (it is also printed in the terminal). Scan it
with your phone's camera, paste into the page, tap **Envoyer**. `Ctrl+C` to stop.

### Options

| Option | Effect |
| --- | --- |
| `--port 8765` | Port to listen on |
| `--no-gui` | No window: QR code in the terminal only |
| `--discret` | Never print or display received content (passwords, tokens…) |
| `--no-token` | Drop the token, so the URL is stable and memorable (less safe) |
| `--host 192.168.1.42` | Force the address advertised in the QR code |
| `--backend x11\|wayland` | Force the GTK backend |

### Desktop launcher (optional)

```bash
sed "s|@QRCLIP_PATH@|$PWD|" qrclip.desktop.in > ~/.local/share/applications/qrclip.desktop
```

## How it works

```
  phone                          computer
  ┌───────────┐   HTTP POST   ┌──────────────────┐
  │ web page  │ ────────────► │ http.server      │  (server thread)
  │ (paste)   │   + token     │        │         │
  └───────────┘               │        ▼         │
                              │  GLib.idle_add   │  (hop to the GTK thread)
                              │        │         │
                              │        ▼         │
                              │  Gdk.Clipboard   │ ──► X11 selection (XWayland)
                              └──────────────────┘         │
                                                           ▼
                                                    mutter bridges it
                                                    to Wayland apps
```

- [`qrclip.py`](qrclip.py) — HTTP server, mobile page, GTK 4 window.
- [`qrmini.py`](qrmini.py) — QR encoder, PNG writer, ANSI renderer.

### The Wayland clipboard problem

This is the part worth reading if you are writing anything similar.

Under Wayland, `wl_data_device.set_selection` requires a *serial* from a recent
input event: **only the client holding keyboard focus may take the selection**.
A background service never has focus, so `Gdk.Clipboard.set()` returns
**without any error and does nothing**. There is no exception and no warning —
the only way to notice is to read the clipboard back from another process.

The fix is to run on XWayland, where owning a selection does not depend on
focus; mutter then bridges it to Wayland clients. This is exactly why `xclip`
still works under GNOME.

```python
import os
if os.environ.get('DISPLAY'):
    os.environ['GDK_BACKEND'] = 'x11'   # BEFORE importing gi
import gi
```

Two traps:

1. **`os.environ.setdefault` is not enough.** GNOME sessions already export
   `GDK_BACKEND=wayland`, so you must overwrite it. Check with
   `type(Gdk.Display.get_default()).__name__` — it must print `GdkX11Display`.
2. **GDK is not thread-safe.** Called from an HTTP server thread,
   `clipboard.set()` fails the same silent way. It has to be dispatched with
   `GLib.idle_add`.

Consequence: **QR-Clip must stay running** for as long as you want to paste,
since the process owning the selection is the one serving the data. GNOME
usually retains the last content after the owner quits, but the protocol does
not guarantee it.

## Security

- The server listens on your local network over plain HTTP: use it on a Wi-Fi
  you trust, not on a public network.
- The URL carries a random token, regenerated on every launch; without it both
  the page and the send endpoint return 403. It stops a curious neighbour from
  writing to your clipboard — it does not protect against network sniffing.
- Received content is previewed in the terminal and the window. Use
  `--discret` when sending secrets.
- Uploads are capped at 1 MiB.

## Limitations

- Text only, phone → computer.
- The phone cannot pre-fill from its own clipboard: `navigator.clipboard.readText()`
  requires a secure context, and this is plain HTTP over a local IP. Paste manually.
- QR versions 1–10 (plenty for a short URL).

## Tests

```bash
python3 test_qrmini.py
```

Checks the QR encoder against a reference Reed–Solomon vector, verifies the
codeword polynomial is divisible by the generator over 220 random draws, and
round-trips 20 encode → decode cases through an independently written decoder.

## License

MIT — see [LICENSE](LICENSE).
