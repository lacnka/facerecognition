#!/usr/bin/env python3
"""
SENTINEL EXTENSION - EV3 side (ev3dev, Python 3).  Wireless: the EV3 only needs its network link + the webcam.

Stays in standby with a connection open to sentinel.py on your PC. When the doorbell is pressed the
PC sends "RING"; this script then:
  1. grabs webcam frames and streams them to sentinel.py (second connection, closes when done)
  2. gets back the recognised name and sets the LEDs (the micro:bit does the beeping)
sentinel.py does the face matching, prints the LOGIN line and tells the doorbell micro:bit
"Hello <name>" through the gateway micro:bit that is plugged into the PC.

Why the PC does the matching: the EV3 has a ~300 MHz ARM9 and 64 MB RAM, far too slow for
YuNet + SFace. The EV3 does the physical part and the PC does the maths on your enrolled faces.

Network: the EV3 reaches the PC through your iPhone hotspot over Bluetooth (no Wi-Fi dongle);
the PC must be connected to the same hotspot.

Setup on the EV3 (ssh robot@ev3dev.local, password: maker):
    sudo apt-get update
    sudo apt-get install -y fswebcam
    # python3-ev3dev2 is usually already there; if beeps/LEDs are silent: sudo apt-get install python3-ev3dev2
Run (first time, remembers the settings):
    python3 ev3_sentinel_ext.py <pc-ip> <token>
After that:
    python3 ev3_sentinel_ext.py
"""
import json
import os
import select
import socket
import struct
import subprocess
import sys
import time

# ----------------------------------------------------------------------------- settings
PC_HOST = "192.168.1.50"       # your PC's IP (sentinel.py prints it when you type: ext on)
PC_PORT = 5055                 # same as sentinel.py setting ext_port
TOKEN = "change-me"            # same as sentinel.py setting ext_token (set ext_token <secret>)
CAM_RES = "640x480"
CAM_PALETTE = "MJPEG"          # YUYV also worked; MJPEG gave the sharpest 640x480 picture
SKIP = 30                      # frames to skip before every picture (too few = black picture)
BLACK_KB = 10                  # a black 640x480 JPEG is ~5 KB, a real one 20+ KB: smaller means "retry"
CONF_PATH = os.path.expanduser("~/.sentinel_ev3.json")
SCAN_SECONDS = 12.0             # how long to keep trying after a ring
FRAME_PATH = "/tmp/sentinel_frame.jpg"
BEEP = False                   # your EV3's speaker is broken; the micro:bit does the beeping

try:
    from ev3dev2.sound import Sound
    from ev3dev2.led import Leds
    _snd, _leds = Sound(), Leds()
except Exception:
    _snd = _leds = None


# ----------------------------------------------------------------------------- settings from file / command line
def load_settings():
    """Usage:  python3 ev3_sentinel_ext.py <pc-ip> <token>   (remembered in ~/.sentinel_ev3.json)
               python3 ev3_sentinel_ext.py                   (uses what was remembered)"""
    global PC_HOST, TOKEN
    try:
        with open(CONF_PATH) as f:
            c = json.load(f)
        PC_HOST, TOKEN = c.get("host", PC_HOST), c.get("token", TOKEN)
    except (OSError, ValueError):
        pass
    if len(sys.argv) >= 3:
        PC_HOST, TOKEN = sys.argv[1], sys.argv[2]
        with open(CONF_PATH, "w") as f:
            json.dump({"host": PC_HOST, "token": TOKEN}, f)
        os.chmod(CONF_PATH, 0o600)
        print("saved settings to %s" % CONF_PATH)


# ----------------------------------------------------------------------------- helpers
def led(color):
    if _leds is None:
        return
    try:
        _leds.set_color("LEFT", color)
        _leds.set_color("RIGHT", color)
    except Exception:
        pass


def tone(freq, ms):
    try:
        if _snd is not None:
            _snd.tone(freq, ms)
        else:
            subprocess.run(["beep", "-f", str(freq), "-l", str(ms)], timeout=5)
    except Exception:
        pass


def beep(ok):
    if not BEEP:
        return
    if ok:
        tone(880, 120)
        tone(1175, 220)
    else:
        tone(220, 450)


def grab():
    """One JPEG from the USB webcam. fswebcam reopens the camera for every picture, and a freshly opened
    webcam gives black frames until its auto-exposure settles, so every picture skips SKIP frames first.
    If the picture still looks black, try once more with twice the wait."""
    data = b""
    for skip in (SKIP, SKIP * 2):
        subprocess.run(["fswebcam", "-q", "-r", CAM_RES, "-p", CAM_PALETTE, "--no-banner", "--skip", str(skip),
                        "--jpeg", "85", FRAME_PATH],
                       check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(FRAME_PATH, "rb") as f:
            data = f.read()
        kb = len(data) // 1024
        print("  frame: %d KB (skip %d)%s" % (kb, skip, "" if kb >= BLACK_KB else " - looks black, retrying"))
        if kb >= BLACK_KB:
            break
    return data


def _hello(sock, role, token):
    msg = ("%s|%s" % (token, role)).encode()
    sock.sendall(struct.pack(">I", len(msg)) + msg)


def scan(grab_fn=None, seconds=None, host=None, port=None, token=None):
    """Stream frames to sentinel.py until a known face comes back. Returns (name or None, score).
    Raises OSError / PermissionError if the PC can't be reached or rejects the token.

    Wire format (big-endian integers):
        EV3 -> PC   [len]["<token>|scan"]   then  [len][jpeg] ...   then  [0] to finish
        PC  -> EV3  one JSON line per frame: {"name": "Alvin" | null, "score": 0.71, "faces": 1}
    """
    grab_fn = grab_fn or grab
    seconds = SCAN_SECONDS if seconds is None else seconds
    host, port, token = host or PC_HOST, port or PC_PORT, token or TOKEN
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(60)
    rf = sock.makefile("rb")
    name, score = None, 0.0
    try:
        _hello(sock, "scan", token)
        end = time.time() + seconds
        while time.time() < end and name is None:
            jpg = grab_fn()
            sock.sendall(struct.pack(">I", len(jpg)) + jpg)
            reply = json.loads(rf.readline().decode() or "{}")
            if "error" in reply:
                raise PermissionError(reply["error"])
            name, score = reply.get("name"), reply.get("score", 0.0)
        sock.sendall(struct.pack(">I", 0))      # tells the PC to write the log entry
    finally:
        try:
            rf.close()
            sock.close()
        except OSError:
            pass
    return name, score


def handle_ring():
    print("ring! scanning ...")
    led("AMBER")
    try:
        name, score = scan()
    except Exception as e:                      # PC off, bad token, webcam missing ...
        print("scan failed: %s: %s" % (type(e).__name__, e))
        led("RED")
        beep(False)
    else:
        if name:
            print("recognised %s (%.0f%%)" % (name, score * 100))
            led("GREEN")
            beep(True)
        else:
            print("nobody recognised")
            led("RED")
            beep(False)
    time.sleep(1)
    led("GREEN")


# ----------------------------------------------------------------------------- standby link
def standby_loop(on_ring=None, host=None, port=None, token=None, stop=None):
    """Keep a connection to sentinel.py open (heartbeat every 5 s) and call on_ring() on every "RING".
    Reconnects forever if the PC or the Wi-Fi goes away. `stop` is an optional threading.Event."""
    on_ring = on_ring or handle_ring
    while not (stop and stop.is_set()):
        sock = None
        host_, port_, token_ = host or PC_HOST, port or PC_PORT, token or TOKEN
        try:
            sock = socket.create_connection((host_, port_), timeout=10)
            _hello(sock, "standby", token_)
            print("standby: connected to PC %s:%d - waiting for the doorbell" % (host_, port_))
            led("GREEN")
            buf, last_beat = b"", time.time()
            while not (stop and stop.is_set()):
                ready, _, _ = select.select([sock], [], [], 1.0)
                if ready:
                    data = sock.recv(64)
                    if not data:
                        raise ConnectionError("PC closed the connection (wrong token or sentinel stopped)")
                    buf += data
                if time.time() - last_beat >= 5:
                    sock.sendall(b".")
                    last_beat = time.time()
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip() == b"RING":
                        on_ring()
                        buf = b""                # drop rings that piled up during the scan
                        last_beat = 0            # heartbeat right away after a long scan
        except Exception as e:
            print("PC link down (%s: %s) - retrying in 3 s" % (type(e).__name__, e))
            led("RED")
            time.sleep(3)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


if __name__ == "__main__":
    load_settings()
    if TOKEN == "change-me" or PC_HOST == "192.168.1.50":
        print("First run: python3 ev3_sentinel_ext.py <pc-ip> <token>   (e.g. 172.20.10.2 mysecret)")
        sys.exit(1)
    try:
        standby_loop()
    except KeyboardInterrupt:
        led("GREEN")
        sys.exit(0)
