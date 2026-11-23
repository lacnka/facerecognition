# facerecognition: SENTINEL

SENTINEL is an all-in-one face recognition and security camera control panel for Windows, written in pure
OpenCV (YuNet detector + SFace recognizer). It also has a **doorbell extension**: press a button on a
micro:bit, a LEGO EV3 with a webcam scans the visitor, and the PC recognizes them, logs the login and
makes the micro:bit say "Hello <name>".

> Only enroll or record people who have agreed to it. Face data is biometric data, so keep `faces.pkl` private.

## Contents

| Path | What it is | Runs on |
|---|---|---|
| `sentinel.py` | The control panel: enroll, live recognition, security camera, doorbell listener | PC (Windows) |
| `ev3/ev3_sentinel_ext.py` | Standby script: wakes on a ring, streams webcam frames to the PC | LEGO EV3 (ev3dev) |
| `microbit/microbit_doorbell_usb.py` | Doorbell button, display and beeper | micro:bit, plugged into the PC |
| `WRITEUP.md` | Design notes: why it is built this way, problems hit and how they were fixed | |

## Sentinel on its own (PC only)

```
pip install opencv-python numpy pyserial
python sentinel.py
```

Type `menu` to see every command and `help <command>` for details. The two model files (about 40 MB) download
automatically on first use. Useful first commands: `enroll <name>`, `live`, `cam` (security camera with clips),
`settings`, `doctor`.

## Doorbell extension

```
 micro:bit (USB into PC)  <--serial-->  sentinel.py (PC)  <--Wi-Fi / TCP-->  EV3 + webcam
   button A = doorbell                   face matching, log                    streams JPEG frames
   shows Hello <name>, beeps
```

1. A press on button A makes the micro:bit write `RING` to the USB serial port.
2. `sentinel.py` sends `RING` to the EV3, which has kept a connection open to the PC.
3. The EV3 grabs webcam frames and streams them to the PC. The PC matches them against the enrolled faces.
4. On a match the PC prints a `LOGIN` line, writes `clips/doorbell_log.csv` (and sends a Discord message if
   a webhook is set) and tells the micro:bit `HELLO:<name>`. The micro:bit beeps and scrolls the name.
   A stranger gets a low beep and an X.

Why the PC does the matching: the EV3 (300 MHz ARM9, 64 MB RAM) is far too slow for YuNet + SFace.

### Setup

1. **micro:bit:** paste `microbit/microbit_doorbell_usb.py` into python.microbit.org, flash it, and leave the
   micro:bit plugged into the PC. Close the editor tab afterwards, because it can lock the COM port.
   A micro:bit v2 beeps with its built-in speaker, a v1 needs a buzzer on pin 0.
2. **PC:** make sure the PC and the EV3 are on the same network (for example both on a phone hotspot). Run
   `python sentinel.py`, enroll yourself (`enroll <name>`), then `set ext_token <secret>` and `ext on`.
   It prints the PC's IP. Allow Python through the Windows firewall for **both** private and public networks.
3. **EV3:** plug in a USB webcam and install `fswebcam` (on the old ev3dev stretch image the apt sources must
   first be pointed at `archive.debian.org`, see `WRITEUP.md`). Copy `ev3/ev3_sentinel_ext.py` to the EV3, then run:
   ```
   python3 ev3_sentinel_ext.py <pc-ip> <token>
   ```
   The IP and token are remembered in `~/.sentinel_ev3.json`, so next time just run `python3 ev3_sentinel_ext.py`.
4. Press button A.

Commands: `ext` (status), `ext on|off`, `ext log` (recent doorbell events).

### Settings

| Setting | Default | Meaning |
|---|---|---|
| `ext` | off | Start the doorbell listener at startup |
| `ext_port` | 5055 | TCP port the EV3 connects to |
| `ext_token` | `change-me` | Shared secret the EV3 must send. **Change it.** |
| `ext_serial` | auto | micro:bit COM port, or for example `COM5` |
| `ext_beep` | off | Extra beep on the PC (the micro:bit already beeps) |

### Troubleshooting

| You see | Meaning |
|---|---|
| `!` on the micro:bit | The EV3 could not reach the PC, or its webcam failed. Read the EV3 console. |
| `no EV3 in standby` | The EV3 script isn't running, or its PC IP is wrong. |
| `gateway micro:bit not found` | Replug the micro:bit, or `set ext_serial COM5`. |
| `rejected a connection (wrong token)` | The token differs between `ext_token` and the EV3. |
| Black webcam pictures (frames of about 5 KB) | Raise `SKIP` in the EV3 script: a freshly opened webcam needs time to set its exposure. The script already retries a black picture once with a longer wait. |
| `ssh` to `ev3dev.local` times out | Use the EV3's IP address instead of the name. |

## Privacy and safety

- `faces.pkl` (the face database), `thumbs/`, `clips/`, `snaps/` and `sentinel_config.json` (which can hold your
  Discord webhook URL) are listed in `.gitignore`. Never commit them: this repository is public.
- The EV3 link is plain TCP on a local network, protected only by the shared token. Don't expose port 5055 to
  the internet.
- A doorbell that logs and greets is not access control.
