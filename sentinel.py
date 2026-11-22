#!/usr/bin/env python3
"""
SENTINEL v2 - all-in-one face recognition + security camera control panel.
Pure OpenCV (YuNet detector + SFace recognizer).

    pip install opencv-python numpy
    python sentinel.py

Type  menu  to see every command,  help <command>  for details.
Models (~40 MB) download automatically on first use.
Only enroll / record people who have agreed to it.
"""
import collections
import csv
import datetime
import difflib
import hmac
import html
import json
import os
import pickle
import platform
import random
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
import urllib.request
import uuid
import webbrowser
import zipfile
from pathlib import Path

import cv2
import numpy as np

# ----------------------------------------------------------------------------- paths / constants
BASE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE, "models")
DB_PATH = os.path.join(BASE, "faces.pkl")          # same format as facerec.py -> old faces still work
CFG_PATH = os.path.join(BASE, "sentinel_config.json")
CLIPS_DIR = os.path.join(BASE, "clips")
SNAPS_DIR = os.path.join(BASE, "snaps")
THUMBS_DIR = os.path.join(BASE, "thumbs")
BACKUP_DIR = os.path.join(BASE, "backups")
SORT_DIR = os.path.join(BASE, "sorted")
LOG_PATH = os.path.join(CLIPS_DIR, "log.csv")
ATT_PATH = os.path.join(BASE, "attendance.csv")
REPORT_PATH = os.path.join(BASE, "report.html")

DET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
REC_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
DET_PATH = os.path.join(MODELS_DIR, os.path.basename(DET_URL))
REC_PATH = os.path.join(MODELS_DIR, os.path.basename(REC_URL))
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

DEFAULTS = {
    "camera": "auto",        # "auto" or an index
    "threshold": 0.363,      # cosine similarity needed to count as a match
    "trigger": "face",       # security cam trigger: face | motion | both
    "motion_sens": 1.5,      # % of the picture that must change to count as motion
    "pre": 2.0,              # seconds kept from BEFORE a trigger
    "post": 5.0,             # seconds recorded after the last trigger
    "only_unknown": False,   # face trigger only for people who aren't enrolled
    "beep": False,           # beep when a recording starts (Windows)
    "show": True,            # show the preview window in cam mode
    "send_clip": False,      # upload the finished clip to Discord (<8 MB)
    "webhook": "",           # Discord webhook URL (secret!)
    "speak": True,           # text-to-speech greetings / alerts (Windows)
    "greet_every": 600.0,    # seconds before greeting the same person again
    "lock_after": 20.0,      # guard mode: lock the PC after this many seconds without you
    "theme": "cyber",        # cyber | matrix | amber | ice | sakura | xmas (limited edition)
    "theme_prev": "cyber",   # theme restored by 'xmas off'
    "xmas_anytime": False,   # allow the limited Christmas theme outside its season
    "xmas_style": "auto",    # face accessory: auto | santa | elf | antlers
    "flappy_best": 0,        # high score
    "ext": False,            # doorbell extension: listen for the EV3 webcam scanner at startup
    "ext_port": 5055,        # TCP port the EV3 connects to
    "ext_token": "change-me",  # shared secret the EV3 must send (change it: set ext_token <secret>)
    "ext_serial": "auto",    # micro:bit port: auto or e.g. COM5
    "ext_beep": False,       # also beep on this PC at the door (off: the micro:bit does the beeping)
}
CHOICES = {"trigger": ("face", "motion", "both"), "xmas_style": ("auto", "santa", "elf", "antlers")}

FONT = cv2.FONT_HERSHEY_SIMPLEX
B_CYAN, B_GREEN, B_RED, B_AMBER, B_WHITE = (255, 229, 0), (120, 255, 0), (70, 70, 255), (0, 190, 255), (255, 255, 255)

# ----------------------------------------------------------------------------- terminal styling
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"


def fg(n):
    return f"\033[38;5;{n}m"


GREEN, RED, YEL, GREY, ORANGE, WHITE = fg(46), fg(196), fg(220), fg(244), fg(208), fg(255)
THEMES = {
    "cyber": (51, 39, 201, (51, 45, 39, 33, 99)),
    "matrix": (46, 40, 34, (46, 40, 34, 28, 22)),
    "amber": (220, 214, 208, (226, 220, 214, 208, 202)),
    "ice": (159, 117, 105, (195, 159, 123, 117, 111)),
    "sakura": (218, 212, 205, (225, 219, 213, 207, 201)),
    "xmas": (196, 34, 220, (196, 255, 34, 255, 196)),   # red / green / gold, candy-cane banner
}
CYAN = BLUE = MAG = ""
GRAD = THEMES["cyber"][3]
XMAS = False          # True while the limited Christmas edition is active
_NO_FX = False        # security recordings switch the festive effects off


def apply_theme(name):
    global CYAN, BLUE, MAG, GRAD, XMAS
    a, b, c, g = THEMES.get(name, THEMES["cyber"])
    CYAN, BLUE, MAG, GRAD = fg(a), fg(b), fg(c), g
    XMAS = name == "xmas"


apply_theme("cyber")
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def setup_terminal():
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            m = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(m)):
                k.SetConsoleMode(h, m.value | 0x0004)  # enable ANSI colors
        except Exception:
            os.system("")
        os.system("title SENTINEL // face recognition control panel")
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # hide OpenCV warning spam
    except Exception:
        pass


def vlen(s):
    return len(ANSI_RE.sub("", s))


def pad(s, n):
    return s + " " * max(0, n - vlen(s))


def now_s():
    return datetime.datetime.now().strftime("%H:%M:%S")


def say(kind, msg):
    styles = {"ok": (GREEN, " OK "), "info": (CYAN, "INFO"), "warn": (YEL, "WARN"), "err": (RED, "FAIL"),
              "rec": (RED, " REC"), "id": (MAG, " ID "), "net": (BLUE, "NET "), "sys": (ORANGE, " SYS")}
    col, label = styles[kind]
    print(f"{GREY}{now_s()}{RESET} {col}[{label}]{RESET} {msg}")


def clear():
    os.system("cls" if os.name == "nt" else "clear")


GLYPHS = {
    "S": [r" ____  ", r"/ ___| ", r"\___ \ ", r" ___) |", r"|____/ "],
    "E": [r" _____ ", r"| ____|", r"|  _|  ", r"| |___ ", r"|_____|"],
    "N": [r" _   _ ", r"| \ | |", r"|  \| |", r"| |\  |", r"|_| \_|"],
    "T": [r" _____ ", r"|_   _|", r"  | |  ", r"  | |  ", r"  |_|  "],
    "I": [r" ___ ", r"|_ _|", r" | | ", r" | | ", r"|___|"],
    "L": [r" _     ", r"| |    ", r"| |    ", r"| |___ ", r"|_____|"],
}


def tree_lines():
    y, g, r, b = fg(226), fg(34), fg(196), fg(94)
    return [f"{y}    *    {RESET}",
            f"{g}   /{y}*{g}\\   {RESET}",
            f"{g}  /{r}o{g}*{y}o{g}\\  {RESET}",
            f"{g} /{y}*{g}o{r}*{g}o{y}*{g}\\ {RESET}",
            f"{b}   |||   {RESET}"]


def snow_line(w=None):
    w = w or panel_width()
    return "  " + fg(253) + "".join(random.choice(" " * 9 + "*.'+") for _ in range(w)) + RESET


def snow_anim(frames=16, rows=5, delay=0.06):
    """Terminal snowfall: rows of flakes shift downwards a few times."""
    w = panel_width()
    pool = " " * 30 + "*.'+"
    buf = collections.deque([" " * w] * rows, maxlen=rows)
    for _ in range(rows):
        print()
    for _ in range(frames):
        buf.appendleft("".join(random.choice(pool) for _ in range(w)))
        sys.stdout.write(f"\033[{rows}A")
        for line in buf:
            sys.stdout.write("  " + fg(255) + line + RESET + "\n")
        sys.stdout.flush()
        time.sleep(delay)


def banner():
    rows = ["".join(GLYPHS[ch][i] for ch in "SENTINEL") for i in range(5)]
    tree = tree_lines() if XMAS else [""] * 5
    for row, col, t in zip(rows, GRAD, tree):
        print("  " + fg(col) + BOLD + row + RESET + (("   " + t) if t else ""))
    if XMAS:
        print(f"  {RED}*{RESET} {WHITE}{BOLD}CHRISTMAS LIMITED EDITION{RESET} {RED}*{RESET}  {GREY}face recognition // security camera // control panel{RESET}\n")
    else:
        print(f"  {GREY}face recognition  {CYAN}//{GREY}  security camera  {CYAN}//{GREY}  control panel  {CYAN}//{GREY}  v2{RESET}\n")


def panel_width():
    cols = shutil.get_terminal_size((120, 30)).columns
    return max(60, min(100, cols - 4))


def panel(title, lines, width=None, color=None):
    width = width or panel_width()
    color = color or CYAN
    t = f" {title} "
    print(f"  {color}┌─{t}{'─' * max(0, width - vlen(t) - 3)}┐{RESET}")
    for ln in lines:
        print(f"  {color}│{RESET} {pad(ln, width - 4)} {color}│{RESET}")
    print(f"  {color}└{'─' * (width - 2)}┘{RESET}")


def prompt():
    p = f"{CYAN}{BOLD}sentinel{RESET}{GREY}@{RESET}{BLUE}panel{RESET} {MAG}>{RESET} "
    if os.name != "nt":
        p = ANSI_RE.sub(lambda m: "\001" + m.group(0) + "\002", p)  # keep readline happy
    return input(p)


def ask(label, arg=""):
    return arg.strip() or input(f"  {CYAN}{label}{RESET} > ").strip()


def bar(frac, n=20, ch="█", empty="░"):
    k = int(max(0.0, min(1.0, frac)) * n)
    return ch * k + empty * (n - k)


def confirm(q):
    return input(f"  {YEL}{q} (y/N){RESET} ").strip().lower() == "y"


def safe_name(s):
    return re.sub(r"[^\w\- ]", "_", s)


# ----------------------------------------------------------------------------- config / db
def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH) as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    with open(CFG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def load_db():
    try:
        with open(DB_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def save_db(db):
    with open(DB_PATH, "wb") as f:
        pickle.dump(db, f)


def models_ready():
    return os.path.exists(DET_PATH) and os.path.exists(REC_PATH)


def ensure_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    for url, path in ((DET_URL, DET_PATH), (REC_URL, REC_PATH)):
        if os.path.exists(path):
            continue
        name = os.path.basename(path)

        def hook(blocks, bs, total, name=name):
            if total > 0:
                frac = min(1.0, blocks * bs / total)
                print(f"\r{GREY}{now_s()}{RESET} {BLUE}[NET ]{RESET} {name[:34]:<34} {CYAN}{bar(frac, 30)}{RESET} {int(frac * 100):>3}%", end="", flush=True)

        urllib.request.urlretrieve(url, path, hook)
        print()


# ----------------------------------------------------------------------------- vision core
class Core:
    def __init__(self):
        self.cfg = load_cfg()
        self.db = load_db()
        self.det = self.rec = None

    def models(self):
        if self.det is None:
            ensure_models()
            self.det = cv2.FaceDetectorYN.create(DET_PATH, "", (640, 480), 0.8, 0.3, 5000)
            self.rec = cv2.FaceRecognizerSF.create(REC_PATH, "")
        return self.det, self.rec

    def detect(self, frame):
        det, _ = self.models()
        h, w = frame.shape[:2]
        det.setInputSize((w, h))
        _, faces = det.detect(frame)
        return faces if faces is not None else []

    def crop(self, frame, face):
        return self.models()[1].alignCrop(frame, face)

    def embed(self, frame, face):
        return self.models()[1].feature(self.crop(frame, face))

    def identify(self, feat):
        _, rec = self.models()
        best, score = "Unknown", -1.0
        for name, feats in self.db.items():
            for f in feats:
                s = rec.match(feat, f, cv2.FaceRecognizerSF_FR_COSINE)
                if s > score:
                    best, score = name, s
        if score < self.cfg["threshold"]:
            return "Unknown", score
        return best, score

    def recognize_all(self, frame):
        out = []
        for f in self.detect(frame):
            name, score = self.identify(self.embed(frame, f))
            out.append((f, name, score))
        return out


def largest(faces):
    return max(faces, key=lambda f: f[2] * f[3])


def label_for(name, score):
    unknown = score < 0 or name == "Unknown"
    if fx_on():
        return "NAUGHTY LIST" if unknown else f"NICE: {name.upper()} {max(0, score) * 100:.0f}%"
    return "UNKNOWN" if unknown else f"{name.upper()} {max(0, score) * 100:.0f}%"


def open_camera(index):
    backends = [("default", cv2.CAP_ANY)]
    if os.name == "nt":
        backends += [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]
    indices = [int(index)] if str(index) != "auto" else [0, 1, 2, 3]
    for i in indices:
        for name, be in backends:
            try:
                cap = cv2.VideoCapture(i, be)
            except Exception:
                continue
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    say("ok", f"camera {WHITE}{i}{RESET} online via {name}")
                    return cap
            cap.release()
    say("err", "no working camera. Plug it in, close Zoom/Teams/Camera app, check Windows camera privacy.")
    return None


# ----------------------------------------------------------------------------- HUD drawing
def hud_box(frame, face, label, color):
    x, y, w, h = face[:4].astype(int)
    n = max(10, min(w, h) // 4)
    for px, py, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        cv2.line(frame, (px, py), (px + dx * n, py), color, 2, cv2.LINE_AA)
        cv2.line(frame, (px, py), (px, py + dy * n), color, 2, cv2.LINE_AA)
    (tw, th), _ = cv2.getTextSize(label, FONT, 0.5, 1)
    if fx_on() and y + h + th + 12 <= frame.shape[0]:
        ty = y + h + th + 10  # label goes under the chin so the hat stays visible
        cv2.rectangle(frame, (x, y + h), (x + tw + 8, ty), color, -1)
        cv2.putText(frame, label, (x + 4, ty - 5), FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    else:
        ty = max(th + 10, y)
        cv2.rectangle(frame, (x, ty - th - 10), (x + tw + 8, ty), color, -1)
        cv2.putText(frame, label, (x + 4, ty - 6), FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    for i in range(5):
        cv2.circle(frame, (int(face[4 + 2 * i]), int(face[5 + 2 * i])), 2, color, -1, cv2.LINE_AA)
    if fx_on():
        draw_accessory(frame, face, acc_style_for(label))


def hud_header(frame, title, right="", color=B_CYAN, rec=False):
    h, w = frame.shape[:2]
    if fx_on() and color == B_CYAN:
        color = B_GOLD
    bar_ = frame[0:26, 0:w].copy()
    cv2.rectangle(frame, (0, 0), (w, 26), (20, 20, 20), -1)
    cv2.addWeighted(bar_, 0.25, frame[0:26, 0:w], 0.75, 0, frame[0:26, 0:w])
    cv2.putText(frame, f"SENTINEL // {title}", (8, 18), FONT, 0.5, color, 1, cv2.LINE_AA)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txt = f"{right}  {stamp}".strip()
    (tw, _), _ = cv2.getTextSize(txt, FONT, 0.45, 1)
    cv2.putText(frame, txt, (w - tw - 8, 18), FONT, 0.45, B_WHITE, 1, cv2.LINE_AA)
    if rec and int(time.time() * 2) % 2 == 0:
        cv2.circle(frame, (w // 2, 13), 6, B_RED, -1, cv2.LINE_AA)
    if fx_on():
        xmas_fx(frame)
    else:
        y = int(26 + (time.time() * 90) % max(1, h - 26))  # scan line
        cv2.line(frame, (0, y), (w, y), color, 1, cv2.LINE_AA)


# ----------------------------------------------------------------------------- Christmas edition (limited)
B_GOLD, B_XRED, B_XWHITE, B_XGREEN = (0, 215, 255), (40, 40, 215), (245, 245, 245), (60, 160, 60)


def fx_on():
    return XMAS and not _NO_FX


def in_season(d=None):
    d = d or datetime.date.today()
    return (d.month == 11 and d.day >= 15) or d.month == 12 or (d.month == 1 and d.day <= 6)


def season_end(d=None):
    d = d or datetime.date.today()
    return datetime.date(d.year + 1 if d.month >= 11 else d.year, 1, 6)


def xmas_available(cfg):
    return in_season() or bool(cfg.get("xmas_anytime"))


def days_to_christmas(d=None):
    d = d or datetime.date.today()
    x = datetime.date(d.year, 12, 25)
    if d > x:
        x = datetime.date(d.year + 1, 12, 25)
    return (x - d).days


def countdown_text(d=None):
    d = d or datetime.date.today()
    if d.month == 12 and d.day == 25:
        return "Merry Christmas! It's today!"
    if (d.month == 12 and d.day > 25) or (d.month == 1):
        return "Happy holidays! See you next year"
    n = days_to_christmas(d)
    return f"{n} day{'s' if n != 1 else ''} until Christmas"


class Snow:
    def __init__(self, w, h, n=110):
        self.rng = np.random.default_rng()
        r = self.rng
        self.w, self.h = w, h
        self.x, self.y = r.uniform(0, w, n), r.uniform(0, h, n)
        self.v, self.r, self.p = r.uniform(30, 90, n), r.integers(1, 4, n), r.uniform(0, 6.28, n)
        self.t = time.time()

    def draw(self, frame):
        now = time.time()
        dt, self.t = min(0.1, now - self.t), now
        self.y += self.v * dt
        self.x += np.sin(now * 1.3 + self.p) * 18 * dt
        gone = self.y > self.h
        self.y[gone] = 0
        self.x[gone] = self.rng.uniform(0, self.w, int(gone.sum()))
        for xi, yi, ri in zip(self.x, self.y, self.r):
            cv2.circle(frame, (int(xi) % self.w, int(yi)), int(ri), (255, 255, 255), -1, cv2.LINE_AA)


_snows = {}


def snow_on(frame):
    h, w = frame.shape[:2]
    if (w, h) not in _snows:
        _snows[(w, h)] = Snow(w, h)
    _snows[(w, h)].draw(frame)


def draw_garland(frame, y0=28):
    """String of blinking Christmas lights hanging under the header bar."""
    w = frame.shape[1]
    seg, sag = 80, 12
    blink = int(time.time() * 2)
    cols = [(40, 40, 255), (0, 215, 255), (60, 220, 60), (255, 150, 50)]
    k = 0
    for x0 in range(-10, w, seg):
        pts = [(x0 + int(seg * t / 8), y0 + int(sag * 4 * (t / 8) * (1 - t / 8))) for t in range(9)]
        cv2.polylines(frame, [np.array(pts, np.int32)], False, (20, 110, 20), 2, cv2.LINE_AA)
        for t in (1, 3, 5, 7):
            col = cols[(k + blink) % 4]
            cv2.circle(frame, (pts[t][0], pts[t][1] + 4), 4, col, -1, cv2.LINE_AA)
            k += 1


XMAS_STYLE = "auto"   # auto | santa | elf | antlers


def set_style(v):
    global XMAS_STYLE
    XMAS_STYLE = v if v in ("auto", "santa", "elf", "antlers") else "auto"


def acc_style_for(label):
    """auto: nice-list people get a Santa or elf hat (stable per name), strangers get antlers."""
    if XMAS_STYLE != "auto":
        return XMAS_STYLE
    if label.startswith("NAUGHTY"):
        return "antlers"
    name = label[6:].rsplit(" ", 1)[0] if label.startswith("NICE: ") else label
    return ("santa", "elf")[sum(map(ord, name)) % 2]


def _head_frame(face):
    """Face-relative coordinates that rotate with the eye line (YuNet landmarks 0 and 1)."""
    x, y, w, h = [float(v) for v in face[:4]]
    cx, cy = x + w / 2, y + h / 2
    th = float(np.arctan2(face[7] - face[5], face[6] - face[4]))
    c, s = np.cos(th), np.sin(th)

    def P(lx, ly):
        return int(round(cx + lx * c - ly * s)), int(round(cy + lx * s + ly * c))

    return w, h, P


def _bez3(p0, p1, p2, p3, n=14):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2, p3 = (np.array(p, float) for p in (p0, p1, p2, p3))
    return [tuple(p) for p in (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3]


def _capsule(frame, a, b, r, col, rim=None):
    if rim:
        cv2.line(frame, a, b, rim, 2 * r + 4, cv2.LINE_AA)
        cv2.circle(frame, a, r + 2, rim, -1, cv2.LINE_AA)
        cv2.circle(frame, b, r + 2, rim, -1, cv2.LINE_AA)
    cv2.line(frame, a, b, col, 2 * r, cv2.LINE_AA)
    cv2.circle(frame, a, r, col, -1, cv2.LINE_AA)
    cv2.circle(frame, b, r, col, -1, cv2.LINE_AA)


def _pompom(frame, center, r):
    cv2.circle(frame, center, r + 2, (205, 205, 220), -1, cv2.LINE_AA)
    for k in range(7):  # fluffy edge
        ang = k * 2 * np.pi / 7
        cv2.circle(frame, (int(center[0] + r * 0.75 * np.cos(ang)), int(center[1] + r * 0.75 * np.sin(ang))),
                   max(2, int(r * 0.5)), B_XWHITE, -1, cv2.LINE_AA)
    cv2.circle(frame, center, r, B_XWHITE, -1, cv2.LINE_AA)


def draw_santa_hat(frame, face):
    w, h, P = _head_frame(face)
    yb = -0.48 * h                                   # brim sits on the forehead
    r = max(4, int(0.085 * h))                       # brim half-thickness
    tip = (0.74 * w, yb - 0.42 * w)                  # the tip droops to the right
    outer = _bez3((-0.46 * w, yb), (-0.44 * w, yb - 1.00 * w), (0.28 * w, yb - 1.05 * w), tip)
    inner = _bez3(tip, (0.50 * w, yb - 0.78 * w), (0.54 * w, yb - 0.40 * w), (0.46 * w, yb))
    poly = np.array([P(*p) for p in outer + inner], np.int32)
    cv2.fillPoly(frame, [poly], (35, 35, 205), cv2.LINE_AA)
    cv2.polylines(frame, [np.array([P(*p) for p in outer[3:-3]], np.int32)], False, (80, 80, 240), max(2, int(0.035 * w)), cv2.LINE_AA)
    cv2.polylines(frame, [poly], True, (0, 0, 120), 2, cv2.LINE_AA)
    _capsule(frame, P(-0.54 * w + r, yb), P(0.54 * w - r, yb), r, B_XWHITE, rim=(205, 205, 220))
    _pompom(frame, P(*tip), max(5, int(0.095 * w)))


def draw_elf_hat(frame, face):
    w, h, P = _head_frame(face)
    yb = -0.48 * h
    r = max(4, int(0.075 * h))
    tip = (0.16 * w, yb - 1.15 * w)
    left = _bez3((-0.46 * w, yb), (-0.40 * w, yb - 0.45 * w), (-0.12 * w, yb - 0.85 * w), tip)
    right = _bez3(tip, (0.32 * w, yb - 0.80 * w), (0.40 * w, yb - 0.40 * w), (0.46 * w, yb))
    poly = np.array([P(*p) for p in left + right], np.int32)
    cv2.fillPoly(frame, [poly], (50, 160, 50), cv2.LINE_AA)
    cv2.polylines(frame, [np.array([P(*p) for p in left[2:-2]], np.int32)], False, (110, 210, 110), max(2, int(0.03 * w)), cv2.LINE_AA)
    cv2.polylines(frame, [poly], True, (20, 90, 20), 2, cv2.LINE_AA)
    a, b = P(-0.52 * w + r, yb), P(0.52 * w - r, yb)
    _capsule(frame, a, b, r, (40, 40, 205), rim=(20, 20, 130))
    for k in range(5):  # little white dots on the brim
        x0 = -0.40 * w + k * 0.2 * w
        cv2.circle(frame, P(x0, yb), max(1, int(r * 0.3)), B_XWHITE, -1, cv2.LINE_AA)
    bell = P(*tip)
    cv2.circle(frame, bell, max(4, int(0.065 * w)), B_GOLD, -1, cv2.LINE_AA)
    cv2.circle(frame, bell, max(4, int(0.065 * w)), (0, 140, 190), 1, cv2.LINE_AA)


def draw_antlers(frame, face):
    """Reindeer antlers plus a red nose on the nose landmark."""
    w, h, P = _head_frame(face)
    brown = (45, 85, 140)
    th = max(2, int(0.05 * w))
    for sg in (-1, 1):
        base, mid, top = (sg * 0.26 * w, -0.45 * h), (sg * 0.40 * w, -0.45 * h - 0.45 * w), (sg * 0.56 * w, -0.45 * h - 0.82 * w)
        cv2.polylines(frame, [np.array([P(*base), P(*mid), P(*top)], np.int32)], False, brown, th, cv2.LINE_AA)
        cv2.line(frame, P(*mid), P(sg * 0.18 * w, -0.45 * h - 0.70 * w), brown, max(2, th - 1), cv2.LINE_AA)
        cv2.line(frame, P(sg * 0.48 * w, -0.45 * h - 0.62 * w), P(sg * 0.72 * w, -0.45 * h - 0.55 * w), brown, max(2, th - 1), cv2.LINE_AA)
    nx, ny, r = int(face[8]), int(face[9]), max(4, int(0.075 * w))
    cv2.circle(frame, (nx, ny), r, (40, 40, 240), -1, cv2.LINE_AA)
    cv2.circle(frame, (nx - r // 3, ny - r // 3), max(1, r // 3), (200, 200, 255), -1, cv2.LINE_AA)
    cv2.circle(frame, (nx, ny), r, (0, 0, 130), 1, cv2.LINE_AA)


def draw_accessory(frame, face, style="santa"):
    {"santa": draw_santa_hat, "elf": draw_elf_hat, "antlers": draw_antlers}.get(style, draw_santa_hat)(frame, face)


def candy_border(img, t=20, seg=24):
    h, w = img.shape[:2]
    for i, x in enumerate(range(0, w, seg)):
        col = B_XRED if i % 2 == 0 else B_XWHITE
        cv2.rectangle(img, (x, 0), (min(x + seg, w), t), col, -1)
        cv2.rectangle(img, (x, h - t), (min(x + seg, w), h), col, -1)
    for i, y in enumerate(range(0, h, seg)):
        col = B_XRED if i % 2 == 0 else B_XWHITE
        cv2.rectangle(img, (0, y), (t, min(y + seg, h)), col, -1)
        cv2.rectangle(img, (w - t, y), (w, min(y + seg, h)), col, -1)
    return img


def xmas_fx(frame):
    snow_on(frame)
    draw_garland(frame)


# Jingle Bells (public domain) as (frequency Hz, milliseconds)
CAROL = [(659, 220), (659, 220), (659, 440), (659, 220), (659, 220), (659, 440), (659, 220), (784, 220), (523, 330), (587, 110), (659, 880),
         (698, 220), (698, 220), (698, 330), (698, 110), (698, 220), (659, 220), (659, 220), (659, 110), (659, 110),
         (659, 220), (587, 220), (587, 220), (659, 220), (587, 440), (784, 440)]


def play_carol():
    for f, d in CAROL:
        beep(f, d)
        time.sleep(0.02)


def chime():
    """Three-note sleigh-bell style chime."""
    for f, d in ((784, 110), (988, 110), (1175, 220)):
        beep(f, d)


def win_closed(name):
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1
    except Exception:
        return False


def center_text(img, text, y, scale, color, thick=2):
    (tw, _), _ = cv2.getTextSize(text, FONT, scale, thick)
    cv2.putText(img, text, ((img.shape[1] - tw) // 2, y), FONT, scale, color, thick, cv2.LINE_AA)


# ----------------------------------------------------------------------------- helpers: speech, discord, motion
def speak(core_cfg, text):
    """Text-to-speech through Windows' built-in voice (no extra packages)."""
    if os.name != "nt" or not core_cfg.get("speak", True):
        return
    safe = re.sub(r"[^\w ,.!?-]", "", text)

    def run():
        try:
            ps = f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def beep(freq=1000, ms=200):
    if os.name == "nt":
        try:
            import winsound
            winsound.Beep(freq, ms)
        except Exception:
            pass


def greeting_word():
    if XMAS:
        return "Merry Christmas"
    h = datetime.datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


_threads = []


def _discord_post(url, text, file_path=None):
    try:
        b = uuid.uuid4().hex
        body = (f'--{b}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                f'Content-Type: application/json\r\n\r\n').encode() + json.dumps({"content": text}).encode() + b"\r\n"
        if file_path and os.path.exists(file_path):
            name = os.path.basename(file_path)
            ctype = "image/jpeg" if name.endswith(".jpg") else "video/mp4"
            with open(file_path, "rb") as f:
                data = f.read()
            body += (f'--{b}\r\nContent-Disposition: form-data; name="files[0]"; filename="{name}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n').encode() + data + b"\r\n"
        body += f"--{b}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={b}", "User-Agent": "sentinel/2.0"})
        urllib.request.urlopen(req, timeout=30).read()
        say("net", "discord alert delivered")
    except Exception as e:
        say("err", f"discord send failed: {e}")


def notify(url, text, file_path=None):
    if not url:
        return
    t = threading.Thread(target=_discord_post, args=(url, text, file_path), daemon=True)
    t.start()
    _threads.append(t)


def motion_amount(prev_gray, frame):
    """Return (percent of pixels that changed, new gray frame)."""
    g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY), (9, 9), 0)
    if prev_gray is None:
        return 0.0, g
    return float((cv2.absdiff(g, prev_gray) > 25).mean() * 100), g


def read_log():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, newline="") as f:
        return list(csv.reader(f))[1:]


# ----------------------------------------------------------------------------- generic live loop
def live_loop(core, title, on_result=None, extra=None):
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    win = f"SENTINEL // {title}"
    say("info", f"{title.lower()} running - press {WHITE}Q{RESET} in the video window (or Ctrl+C here)")
    ema, tprev = 20.0, time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            results = []
            for f, name, score in core.recognize_all(frame):
                known = name != "Unknown"
                hud_box(frame, f, label_for(name, score), B_GREEN if known else B_RED)
                results.append((name, score))
            now = time.time()
            ema = 0.9 * ema + 0.1 / max(now - tprev, 1e-3)
            tprev = now
            if on_result and on_result(results, now) is True:  # a callback can end the loop by returning True
                break
            hud_header(frame, title, f"{ema:.0f} FPS" + (f" | {extra()}" if extra else ""))
            cv2.imshow(win, frame)
            if cv2.waitKey(1) & 0xFF == ord("q") or win_closed(win):
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
    say("info", f"{title.lower()} stopped")


# =============================================================================
#  VISION COMMANDS
# =============================================================================
def cmd_enroll(core, arg):
    name = ask("name to enroll", arg)
    if not name:
        return say("warn", "no name given")
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    samples = core.db.get(name, [])
    target, auto, last, new = 10, False, 0.0, 0
    win = "SENTINEL // ENROLL"
    say("info", f"enrolling {WHITE}{name}{RESET}: {WHITE}SPACE{RESET} capture, {WHITE}A{RESET} auto-capture {target}, {WHITE}Q{RESET} finish")
    os.makedirs(THUMBS_DIR, exist_ok=True)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        faces = core.detect(frame)
        view = frame.copy()
        one = len(faces) == 1
        for f in faces:
            hud_box(view, f, name.upper(), B_GREEN if one else B_AMBER)
        hud_header(view, "ENROLL", f"{name} | samples {len(samples)}")
        msg = "AUTO CAPTURE..." if auto else "SPACE=capture  A=auto  Q=done"
        if not one:
            msg = "need exactly ONE face in view"
        elif faces[0][2] < 80:
            msg = "move closer"
        cv2.putText(view, msg, (8, view.shape[0] - 10), FONT, 0.55, B_AMBER if not one else B_CYAN, 1, cv2.LINE_AA)
        cv2.imshow(win, view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q") or win_closed(win):
            break
        if key == ord("a"):
            auto = not auto
        if (key == ord(" ") or (auto and time.time() - last > 0.6)) and one and faces[0][2] >= 80:
            samples.append(core.embed(frame, faces[0]))
            cv2.imwrite(os.path.join(THUMBS_DIR, safe_name(name) + ".jpg"), core.crop(frame, faces[0]))
            new += 1
            last = time.time()
            say("ok", f"sample {len(samples)} captured")
            if auto and new >= target:
                break
    cap.release()
    cv2.destroyAllWindows()
    if new:
        core.db[name] = samples
        save_db(core.db)
        say("ok", f"saved {WHITE}{len(samples)}{RESET} samples for {WHITE}{name}{RESET}")
    else:
        say("warn", "nothing captured")


def cmd_live(core, arg):
    if not core.db:
        say("warn", "nobody enrolled yet - everything will show as UNKNOWN (use: enroll <name>)")
    live_loop(core, "LIVE")


def cmd_image(core, arg):
    path = ask("image path", arg).strip('"')
    img = cv2.imread(path)
    if img is None:
        return say("err", f"can't read {path}")
    res = core.recognize_all(img)
    say("info", f"{len(res)} face(s) found")
    for i, (f, name, score) in enumerate(res, 1):
        known = name != "Unknown"
        say("id", f"face {i}: {(GREEN if known else RED)}{name}{RESET}  match {max(0, score) * 100:.0f}%")
        hud_box(img, f, label_for(name, score), B_GREEN if known else B_RED)
    win = "SENTINEL // IMAGE"
    cv2.imshow(win, img)
    say("info", "press any key in the image window to close")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def cmd_compare(core, arg):
    try:
        parts = [p.strip('"') for p in shlex.split(arg, posix=False)] if arg.strip() else []
    except ValueError:
        parts = []
    while len(parts) < 2:
        parts.append(input(f"  {CYAN}image {len(parts) + 1} path{RESET} > ").strip().strip('"'))
    feats = []
    for p in parts[:2]:
        img = cv2.imread(p)
        if img is None:
            return say("err", f"can't read {p}")
        faces = core.detect(img)
        if len(faces) == 0:
            return say("err", f"no face found in {os.path.basename(p)}")
        if len(faces) > 1:
            say("warn", f"{len(faces)} faces in {os.path.basename(p)} - using the largest")
        feats.append(core.embed(img, largest(faces)))
    score = core.models()[1].match(feats[0], feats[1], cv2.FaceRecognizerSF_FR_COSINE)
    same = score >= core.cfg["threshold"]
    say("id", f"similarity {WHITE}{score * 100:.0f}%{RESET}  (threshold {core.cfg['threshold'] * 100:.0f}%)")
    say("ok" if same else "warn", f"verdict: {GREEN + 'SAME PERSON' if same else RED + 'DIFFERENT PEOPLE'}{RESET}")


def cmd_sort(core, arg):
    folder = ask("folder of photos", arg).strip('"')
    if not os.path.isdir(folder):
        return say("err", f"not a folder: {folder}")
    files = [f for f in sorted(os.listdir(folder)) if os.path.splitext(f)[1].lower() in IMG_EXT]
    if not files:
        return say("warn", "no images in that folder")
    if not core.db:
        say("warn", "nobody enrolled - everything goes to _unknown")
    counts = collections.Counter()
    say("info", f"sorting {len(files)} photos (originals are COPIED, not moved) into {SORT_DIR}")
    for i, fn in enumerate(files, 1):
        img = cv2.imread(os.path.join(folder, fn))
        if img is None:
            continue
        s = 1280 / max(img.shape[:2])
        if s < 1:
            img = cv2.resize(img, None, fx=s, fy=s)
        names = {n for _, n, _ in core.recognize_all(img)} if core.db else ({"Unknown"} if len(core.detect(img)) else set())
        dests = {n for n in names if n != "Unknown"} or ({"_unknown"} if names else {"_nofaces"})
        for d in dests:
            os.makedirs(os.path.join(SORT_DIR, safe_name(d)), exist_ok=True)
            shutil.copy2(os.path.join(folder, fn), os.path.join(SORT_DIR, safe_name(d), fn))
            counts[d] += 1
        print(f"\r{GREY}{now_s()}{RESET} {CYAN}{bar(i / len(files), 30)}{RESET} {i}/{len(files)}", end="", flush=True)
    print()
    panel("SORT RESULT", [f"{WHITE}{k:<20}{RESET}{v} photo(s)" for k, v in counts.most_common()], color=GREEN)
    if os.name == "nt":
        os.startfile(SORT_DIR)


def cmd_snap(core, arg):
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    win = "SENTINEL // SNAP"
    t0 = time.time()
    frame = None
    while time.time() - t0 < 3.2:
        ok, frame = cap.read()
        if not ok:
            break
        view = frame.copy()
        left = 3 - int(time.time() - t0)
        center_text(view, str(max(left, 1)), view.shape[0] // 2 + 40, 4, B_CYAN, 4)
        hud_header(view, "SNAP", "get ready")
        cv2.imshow(win, view)
        cv2.waitKey(1)
    if frame is not None:
        res = core.recognize_all(frame)
        for f, name, score in res:
            hud_box(frame, f, label_for(name, score), B_GREEN if name != "Unknown" else B_RED)
        os.makedirs(SNAPS_DIR, exist_ok=True)
        path = os.path.join(SNAPS_DIR, f"snap_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg")
        cv2.imwrite(path, frame)
        say("ok", f"saved {path}")
        for _, name, score in res:
            say("id", f"{name} {max(0, score) * 100:.0f}%")
        cv2.imshow(win, frame)
        cv2.waitKey(1500)
    cap.release()
    cv2.destroyAllWindows()


def build_gallery(db):
    tiles = []
    for name in sorted(db):
        p = os.path.join(THUMBS_DIR, safe_name(name) + ".jpg")
        img = cv2.imread(p) if os.path.exists(p) else None
        if img is None:
            img = np.full((112, 112, 3), 40, np.uint8)
            cv2.putText(img, "?", (42, 70), FONT, 1.6, (120, 120, 120), 2)
        img = cv2.resize(img, (140, 140))
        tile = np.full((180, 160, 3), 25, np.uint8)
        tile[10:150, 10:150] = img
        cv2.rectangle(tile, (10, 10), (149, 149), B_CYAN, 1)
        cv2.putText(tile, name[:16], (10, 170), FONT, 0.5, B_WHITE, 1, cv2.LINE_AA)
        tiles.append(tile)
    cols = min(5, max(1, len(tiles)))
    rows = [np.hstack(tiles[i:i + cols] + [np.full((180, 160, 3), 25, np.uint8)] * (cols - len(tiles[i:i + cols])))
            for i in range(0, len(tiles), cols)]
    sheet = np.vstack(rows)
    head = np.full((34, sheet.shape[1], 3), 20, np.uint8)
    cv2.putText(head, f"SENTINEL // GALLERY  ({len(db)} identities)", (10, 23), FONT, 0.55, B_CYAN, 1, cv2.LINE_AA)
    return np.vstack([head, sheet])


def cmd_gallery(core, arg):
    if not core.db:
        return say("warn", "nobody enrolled yet")
    win = "SENTINEL // GALLERY"
    cv2.imshow(win, build_gallery(core.db))
    say("info", "press any key in the window to close")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def cmd_people(core, arg):
    if not core.db:
        return say("warn", "nobody enrolled yet")
    lines = [f"{WHITE}{n:<18}{RESET} {CYAN}{bar(len(fs) / 20)}{RESET} {GREY}{len(fs)} samples{RESET}" for n, fs in sorted(core.db.items())]
    panel(f"ENROLLED ({len(core.db)})", lines)


def cmd_remove(core, arg):
    name = ask("remove who", arg)
    if name not in core.db:
        return say("err", f"'{name}' not found")
    if confirm(f"delete {name}?"):
        del core.db[name]
        save_db(core.db)
        t = os.path.join(THUMBS_DIR, safe_name(name) + ".jpg")
        if os.path.exists(t):
            os.remove(t)
        say("ok", f"{name} removed")


def cmd_rename(core, arg):
    parts = arg.split(maxsplit=1)
    old = parts[0] if parts else input(f"  {CYAN}current name{RESET} > ").strip()
    new = parts[1].strip() if len(parts) > 1 else input(f"  {CYAN}new name{RESET} > ").strip()
    if old not in core.db:
        return say("err", f"'{old}' not found")
    if not new or new in core.db:
        return say("err", "new name is empty or already exists")
    core.db[new] = core.db.pop(old)
    save_db(core.db)
    t_old = os.path.join(THUMBS_DIR, safe_name(old) + ".jpg")
    if os.path.exists(t_old):
        os.replace(t_old, os.path.join(THUMBS_DIR, safe_name(new) + ".jpg"))
    say("ok", f"{old} -> {new}")


# =============================================================================
#  SECURITY COMMANDS
# =============================================================================
def cmd_cam(core, arg):
    global _NO_FX
    cfg = core.cfg
    _NO_FX = True  # evidence footage stays clean: no snow, garland or hats baked into recordings
    if not core.db:
        say("warn", "nobody enrolled - every face counts as UNKNOWN")
    cap = open_camera(cfg["camera"])
    if cap is None:
        return
    os.makedirs(CLIPS_DIR, exist_ok=True)
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", newline="") as f:
            csv.writer(f).writerow(["start", "end", "people", "clip", "snapshot"])
    win = "SENTINEL // WATCH"
    show, mode = cfg["show"], cfg["trigger"]
    say("ok", f"security cam {GREEN}ARMED{RESET}  (trigger: {mode}{' / strangers only' if cfg['only_unknown'] and mode != 'motion' else ''}, "
              f"pre {cfg['pre']}s / post {cfg['post']}s, discord {'ON' if cfg['webhook'] else 'off'})")
    say("info", f"stop with {WHITE}Q{RESET} in the window" if show else f"stop with {WHITE}Ctrl+C{RESET}")

    prebuf = collections.deque()
    writer, last_seen, started, people = None, 0.0, None, set()
    clip = snap = ""
    prev_g = None
    ema, tprev = 20.0, time.time()

    def finish():
        nonlocal writer, people
        if writer is None:
            return
        writer.release()
        writer = None
        end = datetime.datetime.now()
        with open(LOG_PATH, "a", newline="") as f:
            csv.writer(f).writerow([started.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"),
                                    ";".join(sorted(people)), os.path.basename(clip), os.path.basename(snap)])
        say("rec", f"clip saved {WHITE}{os.path.basename(clip)}{RESET}  ({', '.join(sorted(people))})")
        if cfg["send_clip"] and os.path.getsize(clip) < 8 * 1024 * 1024:
            notify(cfg["webhook"], f"Clip {started:%H:%M:%S}-{end:%H:%M:%S}", clip)
        people = set()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                say("err", "camera stopped delivering frames")
                break
            now = time.time()
            ema = 0.9 * ema + 0.1 / max(now - tprev, 1e-3)
            tprev = now

            face_trigger, names = False, set()
            for f, name, score in (core.recognize_all(frame) if core.db else [(f, "Unknown", -1.0) for f in core.detect(frame)]):
                known = name != "Unknown"
                hud_box(frame, f, label_for(name, score), B_GREEN if known else B_RED)
                names.add(name)
                if not cfg["only_unknown"] or not known:
                    face_trigger = True
            motion_pct = 0.0
            if mode in ("motion", "both"):
                motion_pct, prev_g = motion_amount(prev_g, frame)
            motion_trigger = motion_pct > cfg["motion_sens"]
            trigger = (face_trigger and mode in ("face", "both")) or (motion_trigger and mode in ("motion", "both"))
            if trigger:
                people |= (names if face_trigger and mode in ("face", "both") else set()) or {"motion"}

            hud_header(frame, "WATCH", f"{ema:.0f} FPS" + (f" | MOTION {motion_pct:.1f}%" if mode != "face" else ""), rec=writer is not None)
            stamp = datetime.datetime.now()

            if trigger:
                last_seen = now
                if writer is None:
                    started = stamp
                    tag = stamp.strftime("%Y%m%d_%H%M%S")
                    clip = os.path.join(CLIPS_DIR, f"clip_{tag}.mp4")
                    snap = os.path.join(CLIPS_DIR, f"snap_{tag}.jpg")
                    h, w = frame.shape[:2]
                    writer = cv2.VideoWriter(clip, cv2.VideoWriter_fourcc(*"mp4v"), int(max(5, min(30, ema))), (w, h))
                    for _, old in prebuf:
                        writer.write(old)
                    prebuf.clear()
                    cv2.imwrite(snap, frame)
                    say("rec", f"{RED}trigger{RESET} - recording ({', '.join(sorted(people))})")
                    msg = f"Alert {stamp:%Y-%m-%d %H:%M:%S}: {', '.join(sorted(people))}"
                    if XMAS:
                        msg = ("🎅 Naughty list alert " if people & {"Unknown", "motion"} else "🎄 Nice list sighting ") + msg.split(" ", 1)[1]
                    notify(cfg["webhook"], msg, snap)
                    if cfg["beep"]:
                        beep()

            if writer is not None:
                writer.write(frame)
                if now - last_seen > cfg["post"]:
                    finish()
            else:
                prebuf.append((now, frame.copy()))
                while prebuf and prebuf[0][0] < now - cfg["pre"]:
                    prebuf.popleft()

            if show:
                cv2.imshow(win, frame)
                if cv2.waitKey(1) & 0xFF == ord("q") or win_closed(win):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        _NO_FX = False
        finish()
        cap.release()
        cv2.destroyAllWindows()
        for t in _threads:
            t.join(timeout=30)
    say("info", "security cam disarmed")


def cmd_guard(core, arg):
    if not core.db:
        return say("err", "guard needs at least one enrolled face (use: enroll <name>)")
    if os.name != "nt":
        return say("err", "guard locks the screen with a Windows call - only works on Windows")
    secs = core.cfg["lock_after"]
    say("sys", f"GUARD armed: the PC locks after {WHITE}{secs:.0f}s{RESET} without an enrolled face (change: set lock_after <sec>)")
    state = {"last_known": time.time(), "locked": False}

    def on_result(results, now):
        if any(n != "Unknown" for n, _ in results):
            if state["locked"]:
                say("ok", "welcome back")
            state["last_known"], state["locked"] = now, False
        elif not state["locked"] and now - state["last_known"] > secs:
            state["locked"] = True
            say("sys", f"{RED}no known face for {secs:.0f}s - locking workstation{RESET}")
            import ctypes
            ctypes.windll.user32.LockWorkStation()

    def extra():
        if state["locked"]:
            return "LOCKED"
        return f"lock in {max(0, secs - (time.time() - state['last_known'])):.0f}s"

    live_loop(core, "GUARD", on_result, extra)


def cmd_logs(core, arg):
    rows = read_log()[-10:]
    if not rows:
        return say("warn", "no events logged yet")
    lines = []
    for r in rows:
        col = RED if "Unknown" in r[2] else GREEN
        lines.append(f"{GREY}{r[0]}{RESET} {col}{r[2][:24]:<24}{RESET} {GREY}{r[3]}{RESET}")
    panel("LAST EVENTS", lines, color=ORANGE)


def compute_stats(rows):
    people, hours, total_s = collections.Counter(), collections.Counter(), 0.0
    for r in rows:
        try:
            s = datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
            e = datetime.datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
            hours[s.hour] += 1
            total_s += (e - s).total_seconds()
        except Exception:
            continue
        for p in r[2].split(";"):
            if p:
                people[p] += 1
    return people, hours, total_s


def cmd_stats(core, arg):
    rows = read_log()
    if not rows:
        return say("warn", "no events logged yet - run 'cam' first")
    people, hours, total_s = compute_stats(rows)
    unknown = people.get("Unknown", 0)
    lines = [f"{WHITE}events{RESET} {len(rows)}    {WHITE}recorded{RESET} {total_s / 60:.1f} min    {WHITE}stranger events{RESET} {RED if unknown else GREEN}{unknown}{RESET}", ""]
    lines.append(f"{CYAN}who was seen{RESET}")
    mx = max(people.values()) if people else 1
    for p, n in people.most_common(8):
        lines.append(f"  {(RED if p in ('Unknown', 'motion') else GREEN)}{p[:16]:<16}{RESET} {bar(n / mx, 24)} {n}")
    lines += ["", f"{CYAN}activity by hour{RESET}"]
    hm = max(hours.values()) if hours else 1
    for h in sorted(hours):
        lines.append(f"  {GREY}{h:02d}:00{RESET} {ORANGE}{bar(hours[h] / hm, 24)}{RESET} {hours[h]}")
    if hours:
        lines += ["", f"busiest hour: {WHITE}{max(hours, key=hours.get):02d}:00{RESET}"]
    panel("SECURITY STATS", lines, color=ORANGE)


def build_report(rows):
    trs = []
    for r in reversed(rows):
        who = html.escape(r[2])
        cls = "bad" if ("Unknown" in r[2] or "motion" in r[2]) else "ok"
        snap = f'<a href="clips/{html.escape(r[4])}"><img src="clips/{html.escape(r[4])}"></a>' if len(r) > 4 and r[4] else ""
        clip = f'<a href="clips/{html.escape(r[3])}">play</a>' if len(r) > 3 and r[3] else ""
        trs.append(f"<tr><td>{html.escape(r[0])}</td><td>{html.escape(r[1])}</td><td class={cls}>{who}</td><td>{snap}</td><td>{clip}</td></tr>")
    return f"""<!doctype html><meta charset=utf-8><title>SENTINEL report</title>
<style>body{{background:#0b0f14;color:#cfe8ff;font-family:Consolas,monospace;margin:30px}}
h1{{color:#00e5ff;letter-spacing:4px}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #1d2a36;padding:8px;text-align:left}}th{{color:#00e5ff}}
img{{height:70px;border:1px solid #00e5ff;border-radius:4px}}.ok{{color:#39ff88}}.bad{{color:#ff5566}}a{{color:#ffb300}}</style>
<h1>SENTINEL // EVENT REPORT</h1><p>{len(rows)} events - generated {datetime.datetime.now():%Y-%m-%d %H:%M}</p>
<table><tr><th>start</th><th>end</th><th>who</th><th>snapshot</th><th>clip</th></tr>{''.join(trs)}</table>"""


def cmd_report(core, arg):
    rows = read_log()
    if not rows:
        return say("warn", "no events logged yet - run 'cam' first")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(build_report(rows))
    # report lives in BASE, clips in BASE/clips -> relative links work
    say("ok", f"report written: {REPORT_PATH}")
    webbrowser.open(Path(REPORT_PATH).as_uri())


def cmd_clips(core, arg):
    os.makedirs(CLIPS_DIR, exist_ok=True)
    if os.name == "nt":
        os.startfile(CLIPS_DIR)
    else:
        say("info", CLIPS_DIR)


def cmd_purge(core, arg):
    try:
        days = float(ask("delete clips older than how many days", arg))
    except ValueError:
        return say("err", "give a number of days")
    cutoff = time.time() - days * 86400
    old = [os.path.join(CLIPS_DIR, f) for f in os.listdir(CLIPS_DIR)
           if re.match(r"(clip|snap)_.*\.(mp4|jpg)$", f) and os.path.getmtime(os.path.join(CLIPS_DIR, f)) < cutoff] if os.path.isdir(CLIPS_DIR) else []
    if not old:
        return say("info", "nothing that old")
    size = sum(os.path.getsize(p) for p in old) / 1e6
    if confirm(f"delete {len(old)} files ({size:.1f} MB)?"):
        for p in old:
            os.remove(p)
        say("ok", f"deleted {len(old)} files")


# =============================================================================
#  PRODUCTIVITY COMMANDS
# =============================================================================
def today_attendance():
    t = datetime.date.today().isoformat()
    if not os.path.exists(ATT_PATH):
        return []
    with open(ATT_PATH, newline="") as f:
        return [r for r in list(csv.reader(f))[1:] if r and r[0] == t]


def cmd_attendance(core, arg):
    if arg.strip().lower() in ("show", "list", "today"):
        rows = today_attendance()
        if not rows:
            return say("warn", "nobody checked in today")
        return panel(f"ATTENDANCE {datetime.date.today()}", [f"{GREY}{r[1]}{RESET}  {GREEN}{r[2]}{RESET}" for r in rows], color=GREEN)
    if not core.db:
        return say("err", "enroll people first")
    seen = {r[2] for r in today_attendance()}
    if not os.path.exists(ATT_PATH):
        with open(ATT_PATH, "w", newline="") as f:
            csv.writer(f).writerow(["date", "time", "name"])

    def on_result(results, now):
        for name, _ in results:
            if name != "Unknown" and name not in seen:
                seen.add(name)
                with open(ATT_PATH, "a", newline="") as f:
                    csv.writer(f).writerow([datetime.date.today().isoformat(), now_s(), name])
                say("id", f"{GREEN}{name}{RESET} checked in")
                speak(core.cfg, f"{name} checked in")

    say("sys", f"attendance running - first sighting each day is logged to {ATT_PATH}  (view: attendance show)")
    live_loop(core, "ATTENDANCE", on_result, lambda: f"{len(seen)} present")


def cmd_greet(core, arg):
    if not core.db:
        return say("err", "enroll people first")
    last = {}

    def on_result(results, now):
        for name, _ in results:
            if now - last.get(name, 0) > core.cfg["greet_every"]:
                last[name] = now
                if name == "Unknown":
                    say("id", f"{YEL}unknown visitor{RESET}")
                else:
                    say("id", f"{greeting_word()}, {GREEN}{name}{RESET}!")
                    speak(core.cfg, f"{greeting_word()} {name}")
                    if XMAS:
                        threading.Thread(target=chime, daemon=True).start()
                        wl = load_wishes().get(name)
                        if wl:
                            say("id", f"Santa knows you want: {WHITE}{wl[0]}{RESET}" + (f" (+{len(wl) - 1} more)" if len(wl) > 1 else ""))

    live_loop(core, "GREETER", on_result)


def cmd_focus(core, arg):
    try:
        minutes = float(arg) if arg.strip() else 25.0
    except ValueError:
        return say("err", "usage: focus <minutes>")
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    win = "SENTINEL // FOCUS"
    total = minutes * 60
    present = away = 0.0
    away_alerted, close_last, close_n, away_n, stretch = False, 0.0, 0, 0, 0.0
    start = last_t = time.time()
    say("sys", f"focus session: {minutes:g} min - I track whether you're at your desk and not too close to the screen. Q to end early.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.time()
            dt, last_t = now - last_t, now
            faces = core.detect(frame)
            status, col = "AWAY", B_RED
            if len(faces):
                present += dt
                away_alerted = False
                f = largest(faces)
                hud_box(frame, f, "FOCUSED", B_GREEN)
                status, col = "AT DESK", B_GREEN
                if f[2] / frame.shape[1] > 0.42 and now - close_last > 60:
                    close_last, close_n = now, close_n + 1
                    say("warn", "you're very close to the screen - lean back a bit")
                    speak(core.cfg, "You are too close to the screen")
                stretch = 0.0
            else:
                away += dt
                stretch += dt  # length of the current away streak
                if stretch > 30 and not away_alerted:
                    away_alerted, away_n = True, away_n + 1
                    say("warn", "away from your desk for 30s+")
                    beep(700, 300)
                    speak(core.cfg, "Time to get back to work")
            remain = max(0, total - (now - start))
            pct = present / max(1e-6, present + away) * 100
            hud_header(frame, "FOCUS", f"{status} | focus {pct:.0f}%", col)
            center_text(frame, f"{int(remain // 60):02d}:{int(remain % 60):02d}", frame.shape[0] - 24, 1.2, B_CYAN, 2)
            cv2.imshow(win, frame)
            if cv2.waitKey(1) & 0xFF == ord("q") or win_closed(win) or remain <= 0:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
    elapsed = max(1e-6, present + away)
    beep(1200, 400)
    panel("FOCUS SESSION SUMMARY", [
        f"{WHITE}at desk     {RESET}{present / 60:.1f} min   {CYAN}{bar(present / elapsed, 24)}{RESET} {present / elapsed * 100:.0f}%",
        f"{WHITE}away        {RESET}{away / 60:.1f} min   (long breaks: {away_n})",
        f"{WHITE}too close   {RESET}{close_n} warning(s)",
    ], color=GREEN)


def greeting_overlay(frame, name):
    """Magic-mirror styling: dim, mirror, clock and a greeting."""
    out = cv2.flip(frame, 1)
    out = (out * 0.45).astype(np.uint8)
    h, w = out.shape[:2]
    now = datetime.datetime.now()
    center_text(out, now.strftime("%H:%M"), int(h * 0.30), 3.0, B_WHITE, 5)
    center_text(out, now.strftime("%A, %d %B %Y"), int(h * 0.30) + 40, 0.9, B_CYAN, 2)
    if name:
        text = f"{greeting_word()}, {name}" if name != "Unknown" else "Welcome, visitor"
        center_text(out, text, int(h * 0.85), 1.3, B_GREEN if name != "Unknown" else B_AMBER, 3)
    if XMAS:
        center_text(out, countdown_text(), int(h * 0.30) + 80, 0.8, B_GOLD, 2)
        snow_on(out)
    return out


def cmd_mirror(core, arg):
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    win = "SENTINEL // MIRROR"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    say("info", f"magic mirror - press {WHITE}Q{RESET} or {WHITE}Esc{RESET} to exit")
    name, seen_at = None, 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            res = core.recognize_all(frame) if core.db else [(f, "Unknown", -1) for f in core.detect(frame)]
            if res:
                f, n, _ = max(res, key=lambda r: r[0][2] * r[0][3])
                name, seen_at = n, time.time()
            elif time.time() - seen_at > 4:
                name = None
            cv2.imshow(win, greeting_overlay(frame, name))
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()


# =============================================================================
#  FUN COMMANDS
# =============================================================================
class Flappy:
    W, H, GAP, PW, R = 480, 640, 190, 70, 14

    def __init__(self, best=0):
        self.best = best
        self.reset()

    def reset(self):
        self.y, self.vel, self.pipes, self.score, self.alive, self.bx = self.H / 2, 0.0, [], 0, True, 110

    def step(self, dt, target=None):
        """target = desired bird y (from face height) or None (no face -> bird falls)."""
        if not self.alive:
            return
        if target is not None:
            self.y += (target - self.y) * min(1.0, 12 * dt)
            self.vel = 0.0
        else:
            self.vel += 900 * dt
            self.y += self.vel * dt
        speed = 170 + self.score * 6
        if not self.pipes or self.pipes[-1][0] < self.W - 260:
            self.pipes.append([self.W + self.PW, random.randint(90, self.H - 90 - self.GAP), False])
        for p in self.pipes:
            p[0] -= speed * dt
            if not p[2] and p[0] + self.PW < self.bx:
                p[2] = True
                self.score += 1
        self.pipes = [p for p in self.pipes if p[0] > -self.PW]
        if self.y < self.R or self.y > self.H - self.R:
            self.alive = False
        for x, gap_top, _ in self.pipes:
            if self.bx + self.R > x and self.bx - self.R < x + self.PW and (self.y - self.R < gap_top or self.y + self.R > gap_top + self.GAP):
                self.alive = False
        if not self.alive:
            self.best = max(self.best, self.score)

    def render(self):
        img = np.zeros((self.H, self.W, 3), np.uint8)
        for y in range(0, self.H, 4):
            img[y:y + 4] = (max(20, 75 - y // 9), 32, 12) if XMAS else (40 + y // 12, 20 + y // 20, 10)
        for x, gap_top, _ in self.pipes:
            x = int(x)
            for y0, y1 in ((0, gap_top), (gap_top + self.GAP, self.H)):
                cv2.rectangle(img, (x, y0), (x + self.PW, y1), (60, 200, 80), -1)
                if XMAS:  # candy-cane pipes
                    for k, ys in enumerate(range(y0, y1, 18)):
                        cv2.rectangle(img, (x, ys), (x + self.PW, min(ys + 18, y1)), B_XRED if k % 2 == 0 else B_XWHITE, -1)
                cv2.rectangle(img, (x, y0), (x + self.PW, y1), (30, 120, 40), 2)
        c = (int(self.bx), int(self.y))
        cv2.circle(img, c, self.R, (30, 90, 160) if XMAS else (0, 220, 255), -1, cv2.LINE_AA)
        cv2.circle(img, (c[0] + 5, c[1] - 4), 3, (0, 0, 0), -1, cv2.LINE_AA)
        if XMAS:  # Rudolph: red nose + antlers
            cv2.circle(img, (c[0] + self.R, c[1] + 2), 5, (40, 40, 255), -1, cv2.LINE_AA)
            cv2.line(img, (c[0] - 6, c[1] - 12), (c[0] - 11, c[1] - 25), (40, 80, 140), 2, cv2.LINE_AA)
            cv2.line(img, (c[0] + 3, c[1] - 13), (c[0] + 8, c[1] - 26), (40, 80, 140), 2, cv2.LINE_AA)
        if XMAS:
            snow_on(img)
        cv2.putText(img, str(self.score), (self.W // 2 - 15, 70), FONT, 2, B_WHITE, 3, cv2.LINE_AA)
        cv2.putText(img, f"best {self.best}", (10, 25), FONT, 0.6, B_CYAN, 1, cv2.LINE_AA)
        if not self.alive:
            center_text(img, "GAME OVER", self.H // 2, 1.6, B_RED, 3)
            center_text(img, "SPACE restart   Q quit", self.H // 2 + 40, 0.7, B_WHITE, 1)
        return img


def cmd_flappy(core, arg):
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    game = Flappy(core.cfg.get("flappy_best", 0))
    win = "SENTINEL // FLAPPY FACE"
    say("info", f"move your head {WHITE}up/down{RESET} to steer the bird. Hide your face and it falls. SPACE=restart, Q=quit")
    last = time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            faces = core.detect(frame)
            target = None
            if len(faces):
                f = largest(faces)
                target = 40 + (f[1] + f[3] / 2) / frame.shape[0] * (game.H - 80)
                hud_box(frame, f, "PILOT", B_GREEN)
            now = time.time()
            game.step(min(0.05, now - last), target)
            last = now
            img = game.render()
            small = cv2.resize(frame, (160, 120))
            img[game.H - 130:game.H - 10, game.W - 170:game.W - 10] = small
            cv2.imshow(win, img)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q") or win_closed(win):
                break
            if k == ord(" ") and not game.alive:
                game.reset()
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
    if game.best > core.cfg.get("flappy_best", 0):
        core.cfg["flappy_best"] = game.best
        save_cfg(core.cfg)
        say("ok", f"new high score: {WHITE}{game.best}{RESET}")
    else:
        say("info", f"final best this session: {game.best}")


def make_strip(shots, footer=None):
    footer = footer or (f"MERRY CHRISTMAS {datetime.date.today().year}" if XMAS else "SENTINEL BOOTH")
    tiles = []
    for i, s in enumerate(shots):
        t = cv2.resize(s, (320, 240))
        border = (B_XRED if i % 2 == 0 else B_XGREEN) if XMAS else (255, 255, 255)
        t = cv2.copyMakeBorder(t, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=border)
        tiles.append(t)
    strip = np.vstack(tiles)
    foot = np.full((60, strip.shape[1], 3), B_XRED if XMAS else 255, np.uint8)
    ink, ink2 = ((255, 255, 255), (220, 220, 255)) if XMAS else ((40, 40, 40), (90, 90, 90))
    cv2.putText(foot, footer, (14, 26), FONT, 0.6, ink, 2, cv2.LINE_AA)
    cv2.putText(foot, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), (14, 48), FONT, 0.45, ink2, 1, cv2.LINE_AA)
    out = np.vstack([strip, foot])
    if XMAS:
        for f in range(0, out.shape[0], 90):  # a few fixed snowflakes on the strip
            cv2.circle(out, (20 + (f * 37) % (out.shape[1] - 40), f), 3, (255, 255, 255), -1, cv2.LINE_AA)
    return out


def cmd_booth(core, arg):
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    win = "SENTINEL // BOOTH"
    shots = []
    say("info", "photo booth: 4 shots, strike a pose!")
    try:
        for n in range(4):
            t0 = time.time()
            while time.time() - t0 < 3:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("camera lost")
                view = cv2.flip(frame, 1)
                center_text(view, str(3 - int(time.time() - t0)), view.shape[0] // 2 + 40, 4, B_CYAN, 4)
                hud_header(view, "BOOTH", f"shot {n + 1}/4")
                cv2.imshow(win, view)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    raise KeyboardInterrupt
            ok, frame = cap.read()
            shots.append(cv2.flip(frame, 1))
            flash = np.full_like(frame, 255)
            cv2.imshow(win, flash)
            cv2.waitKey(120)
        strip = make_strip(shots)
        os.makedirs(SNAPS_DIR, exist_ok=True)
        path = os.path.join(SNAPS_DIR, f"booth_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg")
        cv2.imwrite(path, strip)
        say("ok", f"saved {path}")
        cv2.imshow(win, strip)
        cv2.waitKey(4000)
    except (KeyboardInterrupt, RuntimeError):
        say("warn", "booth cancelled")
    finally:
        cap.release()
        cv2.destroyAllWindows()


# =============================================================================
#  SYSTEM COMMANDS
# =============================================================================
def cmd_scan(core, arg):
    say("info", "probing cameras 0-4 ...")
    found = []
    for i in range(5):
        for name, be in ([("default", cv2.CAP_ANY)] + ([("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)] if os.name == "nt" else [])):
            try:
                cap = cv2.VideoCapture(i, be)
                if cap.isOpened() and cap.read()[0]:
                    say("ok", f"camera {WHITE}{i}{RESET} works via {name}")
                    found.append(i)
                    cap.release()
                    break
                cap.release()
            except Exception:
                pass
    if not found:
        say("err", "no cameras found")
    else:
        say("info", f"use {WHITE}set camera <n>{RESET} to pick one (or 'auto')")


def cmd_bench(core, arg):
    say("sys", "benchmark: detection + recognition speed")
    cap = open_camera(core.cfg["camera"])
    frame = None
    if cap is not None:
        for _ in range(5):
            ok, frame = cap.read()
        cap.release()
    if frame is None:
        say("warn", "no camera - using a blank test frame (no faces, so recognition isn't timed)")
        frame = np.full((480, 640, 3), 127, np.uint8)
    core.detect(frame)  # warm-up / model load
    n = 30
    t = time.perf_counter()
    for _ in range(n):
        faces = core.detect(frame)
    det_ms = (time.perf_counter() - t) / n * 1000
    lines = [f"{WHITE}detector   {RESET}{det_ms:6.1f} ms/frame  {CYAN}{bar(min(1, 33 / max(det_ms, 1)))}{RESET} ~{1000 / det_ms:.0f} FPS"]
    if len(faces):
        t = time.perf_counter()
        for _ in range(n):
            feat = core.embed(frame, faces[0])
        emb_ms = (time.perf_counter() - t) / n * 1000
        lines.append(f"{WHITE}recognizer  {RESET}{emb_ms:6.1f} ms/face   {CYAN}{bar(min(1, 33 / max(emb_ms, 1)))}{RESET}")
        lines.append(f"{WHITE}full pipeline{RESET} ~{1000 / (det_ms + emb_ms):.0f} FPS per face")
    lines.append(f"{GREY}frame {frame.shape[1]}x{frame.shape[0]} | opencv {cv2.__version__}{RESET}")
    panel("BENCHMARK", lines, color=ORANGE)


def cmd_doctor(core, arg):
    lines = []

    def row(ok, label, detail=""):
        lines.append(f"{(GREEN + '✔') if ok else (RED + '✘')}{RESET} {label:<22}{GREY}{detail}{RESET}")

    row(sys.version_info >= (3, 8), "python", platform.python_version())
    row(True, "opencv", cv2.__version__)
    row(hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF"), "face modules")
    row(os.path.exists(DET_PATH), "detector model", "found" if os.path.exists(DET_PATH) else "missing (downloads on first use)")
    row(os.path.exists(REC_PATH), "recognizer model", "found" if os.path.exists(REC_PATH) else "missing (downloads on first use)")
    row(bool(core.db), "enrolled faces", str(len(core.db)))
    try:
        os.makedirs(CLIPS_DIR, exist_ok=True)
        p = os.path.join(CLIPS_DIR, ".w")
        open(p, "w").close()
        os.remove(p)
        row(True, "write access", CLIPS_DIR)
    except Exception as e:
        row(False, "write access", str(e))
    row(shutil.disk_usage(BASE).free > 1e9, "disk space", f"{shutil.disk_usage(BASE).free / 1e9:.1f} GB free")
    cap = None
    for i in range(3):
        c = cv2.VideoCapture(i)
        if c.isOpened() and c.read()[0]:
            cap = i
            c.release()
            break
        c.release()
    row(cap is not None, "camera", f"index {cap}" if cap is not None else "none found (plugged in?)")
    if core.cfg["webhook"]:
        try:
            urllib.request.urlopen(urllib.request.Request(core.cfg["webhook"], headers={"User-Agent": "sentinel/2.0"}), timeout=8).read()
            row(True, "discord webhook", "reachable")
        except Exception as e:
            row(False, "discord webhook", str(e)[:40])
    else:
        lines.append(f"{GREY}○ discord webhook      not configured{RESET}")
    row(os.name == "nt", "windows features", "guard / tts / beep available" if os.name == "nt" else "guard + tts need Windows")
    panel("SYSTEM DOCTOR", lines, color=ORANGE)


def cmd_backup(core, arg):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"sentinel_backup_{datetime.datetime.now():%Y%m%d_%H%M%S}.zip")
    cfg = dict(core.cfg)
    cfg["webhook"] = ""  # never put the secret URL in a backup
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(DB_PATH):
            z.write(DB_PATH, "faces.pkl")
        z.writestr("sentinel_config.json", json.dumps(cfg, indent=2))
        if os.path.isdir(THUMBS_DIR):
            for f in os.listdir(THUMBS_DIR):
                z.write(os.path.join(THUMBS_DIR, f), "thumbs/" + f)
    say("ok", f"backup saved: {path}  {GREY}(webhook URL excluded){RESET}")


def cmd_restore(core, arg):
    path = ask("backup .zip path", arg).strip('"')
    if not os.path.exists(path):
        return say("err", "file not found")
    if not confirm("overwrite your current faces with this backup?"):
        return
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "faces.pkl" in names:
            core.db = pickle.loads(z.read("faces.pkl"))
            save_db(core.db)
        if "sentinel_config.json" in names:
            hook = core.cfg["webhook"]
            new = json.loads(z.read("sentinel_config.json"))
            core.cfg.update({k: v for k, v in new.items() if k in DEFAULTS and k != "webhook"})
            core.cfg["webhook"] = hook
            save_cfg(core.cfg)
        os.makedirs(THUMBS_DIR, exist_ok=True)
        for n in names:
            if n.startswith("thumbs/") and os.path.basename(n):
                with open(os.path.join(THUMBS_DIR, os.path.basename(n)), "wb") as f:
                    f.write(z.read(n))
    apply_theme(core.cfg["theme"])
    set_style(core.cfg.get("xmas_style", "auto"))
    say("ok", f"restored {len(core.db)} identities")


def mask(v):
    return "(not set)" if not v else v[:30] + "…" + f" ({len(v)} chars)"


def cmd_settings(core, arg):
    c = core.cfg
    on = lambda b: f"{GREEN}ON{RESET}" if b else f"{GREY}off{RESET}"
    rows = [
        ("camera", c["camera"], "auto or index"), ("threshold", c["threshold"], "higher = stricter matching"),
        ("trigger", c["trigger"], "cam: face | motion | both"), ("motion_sens", c["motion_sens"], "% of picture that must change"),
        ("pre", f"{c['pre']}s", "clip seconds before trigger"), ("post", f"{c['post']}s", "clip seconds after trigger"),
        ("only_unknown", on(c["only_unknown"]), "cam: strangers only"), ("beep", on(c["beep"]), "beep on recording"),
        ("show", on(c["show"]), "cam preview window"), ("send_clip", on(c["send_clip"]), "upload clips to discord"),
        ("speak", on(c["speak"]), "spoken greetings / alerts"), ("greet_every", f"{c['greet_every']:.0f}s", "greeter cooldown"),
        ("lock_after", f"{c['lock_after']:.0f}s", "guard: lock delay"), ("theme", c["theme"], " | ".join(THEMES)),
        ("xmas_anytime", on(c["xmas_anytime"]), "Christmas theme outside its season"),
        ("xmas_style", c["xmas_style"], "auto | santa | elf | antlers"),
        ("ext", on(c["ext"]), "EV3 doorbell listener at startup"), ("ext_port", c["ext_port"], "doorbell listener TCP port"),
        ("ext_serial", c["ext_serial"], "doorbell micro:bit: auto or COM port"),
        ("ext_beep", on(c["ext_beep"]), "extra beep on the PC (micro:bit already beeps)"),
        ("webhook", mask(c["webhook"]), ""),
    ]
    panel("SETTINGS  (change with: set <key> <value>)",
          [f"{WHITE}{k:<13}{RESET}{pad(str(v), 22)}{GREY}{d}{RESET}" for k, v, d in rows], color=MAG)


def cmd_set(core, arg):
    parts = arg.split(maxsplit=1)
    if len(parts) < 2:
        return say("warn", "usage: set <key> <value>   (type 'settings' to see keys)")
    key, val = parts[0].lower(), parts[1].strip()
    if key not in DEFAULTS:
        return say("err", f"unknown setting '{key}' - did you mean {', '.join(difflib.get_close_matches(key, DEFAULTS, 2)) or '...'}?")
    default = DEFAULTS[key]
    try:
        if key == "camera":
            new = "auto" if val.lower() == "auto" else int(val)
        elif key == "theme":
            if val.lower() not in THEMES:
                raise ValueError("choose: " + ", ".join(THEMES))
            new = val.lower()
            if new == "xmas" and not xmas_available(core.cfg):
                raise ValueError("the Christmas edition is a limited-time theme (Nov 15 - Jan 6). To preview it now: set xmas_anytime on")
            if new == "xmas" and core.cfg["theme"] != "xmas":
                core.cfg["theme_prev"] = core.cfg["theme"]
        elif key in CHOICES:
            if val.lower() not in CHOICES[key]:
                raise ValueError("choose: " + ", ".join(CHOICES[key]))
            new = val.lower()
        elif isinstance(default, bool):
            if val.lower() not in ("on", "off", "true", "false", "1", "0", "yes", "no"):
                raise ValueError("use on/off")
            new = val.lower() in ("on", "true", "1", "yes")
        elif isinstance(default, float):
            new = float(val)
        elif isinstance(default, int):
            new = int(val)
        else:
            new = "" if val.lower() in ("off", "none", "clear") else val.strip('"')
    except ValueError as e:
        return say("err", f"bad value: {e}")
    core.cfg[key] = new
    save_cfg(core.cfg)
    say("ok", f"{key} = {mask(new) if key == 'webhook' else new}")
    if key == "xmas_style":
        set_style(new)
    if key == "theme":
        apply_theme(new)
        home(core)


def cmd_test(core, arg):
    if not core.cfg["webhook"]:
        return say("warn", "no webhook set. use: set webhook <url>")
    say("net", "sending test message ...")
    _discord_post(core.cfg["webhook"], "SENTINEL test message - alerts are working.")


HISTORY = []


def cmd_history(core, arg):
    if not HISTORY:
        return say("info", "no commands yet")
    for i, h in enumerate(HISTORY[-15:], max(1, len(HISTORY) - 14)):
        print(f"  {MAG}{i:>3}{RESET} {h}")
    say("info", f"repeat with {WHITE}!!{RESET} or {WHITE}!<number>{RESET}")


# =============================================================================
#  CHRISTMAS EDITION COMMANDS (limited)
# =============================================================================
ADVENT_TIPS = [
    "Turn on two-factor authentication for your email first; it protects every other account.",
    "Use a password manager and give every site its own password.",
    "Install pending updates on your phone and PC today. Patches close real holes.",
    "Cover or unplug webcams you are not using.",
    "Keep important files in two places, and one of them off your computer.",
    "Change your router's default admin password.",
    "Do not tap links in unexpected delivery or prize messages. Open the shop's own app instead.",
    "Check which apps have camera and microphone access, and remove the ones that do not need it.",
    "Lock your screen every time you step away (Windows key + L).",
    "Put guests and smart devices on a separate guest Wi-Fi network.",
    "Turn on automatic updates for your router and smart-home gadgets if they offer it.",
    "Set a PIN on your SIM card so a stolen phone cannot be used for calls and codes.",
    "Review the devices that are logged in to your main accounts and sign out the ones you do not know.",
    "Never read out one-time codes to anyone, even someone claiming to be support.",
    "Shred papers that have your name, address or account details.",
    "Use a separate email address for your most important accounts.",
    "Avoid banking or shopping on free public Wi-Fi.",
    "Turn on Find My Device for your phone and laptop before you lose one.",
    "Search your email on haveibeenpwned.com to see whether it appeared in a data breach.",
    "Switch off remote-access features you do not use.",
    "Leave a spare key with someone you trust, never under the doormat.",
    "Test your backup by restoring one file from it.",
    "Before you travel, check door locks, windows and smoke alarms.",
    "Set up new gifts properly: install updates and set a passcode before wrapping them.",
]


def advent_open_days(d=None):
    d = d or datetime.date.today()
    if d.month == 12:
        return min(d.day, 24)
    return 24 if (d.month == 1 and d.day <= 6) else 0


def need_xmas(core):
    if XMAS:
        return True
    if xmas_available(core.cfg):
        say("warn", f"turn the Christmas edition on first: {WHITE}xmas on{RESET}")
    else:
        say("warn", "the Christmas edition is a limited-time theme (Nov 15 - Jan 6)")
    return False


def make_card(frame, faces, sender=""):
    """Christmas card: Santa hats on every face, snow, candy-cane border and a greeting."""
    img = frame.copy()
    for f in faces:
        draw_accessory(img, f, "santa" if XMAS_STYLE == "auto" else XMAS_STYLE)
    snow_on(img)
    h, w = img.shape[:2]
    band = img[h - 104:h - 20, 20:w - 20]
    img[h - 104:h - 20, 20:w - 20] = (band * 0.3).astype(np.uint8)
    for dx, dy, col in ((2, 2, (0, 0, 0)), (0, 0, B_WHITE)):
        (tw, _), _ = cv2.getTextSize("MERRY CHRISTMAS", FONT, 1.5, 3)
        cv2.putText(img, "MERRY CHRISTMAS", ((w - tw) // 2 + dx, h - 62 + dy), FONT, 1.5, col, 3, cv2.LINE_AA)
    sub = f"from {sender}" if sender else str(datetime.date.today().year)
    center_text(img, sub, h - 34, 0.75, B_GOLD, 2)
    return candy_border(img)


def cmd_card(core, arg):
    if not need_xmas(core):
        return
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    win = "SENTINEL // CHRISTMAS CARD"
    t0, frame = time.time(), None
    say("info", "get ready for your Christmas card!")
    while time.time() - t0 < 3.2:
        ok, frame = cap.read()
        if not ok:
            break
        view = frame.copy()
        for f in core.detect(view):
            draw_accessory(view, f, "santa" if XMAS_STYLE == "auto" else XMAS_STYLE)
        center_text(view, str(max(1, 3 - int(time.time() - t0))), view.shape[0] // 2 + 40, 4, B_GOLD, 4)
        hud_header(view, "CARD", "smile!")
        cv2.imshow(win, view)
        cv2.waitKey(1)
    ok, frame = cap.read()
    cap.release()
    if ok:
        card = make_card(frame, core.detect(frame), arg.strip())
        os.makedirs(SNAPS_DIR, exist_ok=True)
        path = os.path.join(SNAPS_DIR, f"card_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg")
        cv2.imwrite(path, card)
        say("ok", f"saved {path}")
        cv2.imshow(win, card)
        cv2.waitKey(4000)
    cv2.destroyAllWindows()


def cmd_advent(core, arg):
    if not need_xmas(core):
        return
    open_n, today = advent_open_days(), datetime.date.today()
    a = arg.strip()
    if a.isdigit():
        n = int(a)
        if not 1 <= n <= 24:
            return say("err", "doors are numbered 1-24")
        if n > open_n:
            return say("warn", f"door {n} is still locked - it opens on Dec {n}")
        return panel(f"DOOR {n}", textwrap.wrap(ADVENT_TIPS[n - 1], panel_width() - 6), color=GREEN)
    lines = []
    for r in range(4):
        row = ""
        for c in range(6):
            n = r * 6 + c + 1
            if today.month == 12 and n == open_n:
                row += f"{YEL}[{n:>2}]{RESET}  "
            elif n <= open_n:
                row += f"{GREEN}[{n:>2}]{RESET}  "
            else:
                row += f"{RED}[##]{RESET}  "
        lines.append(row)
    lines.append("")
    if open_n:
        for i, ln in enumerate(textwrap.wrap(ADVENT_TIPS[open_n - 1], panel_width() - 20)):
            lines.append((f"{WHITE}latest tip:{RESET} " if i == 0 else " " * 12) + ln)
    else:
        lines.append(f"{GREY}Doors open one per day from Dec 1.{RESET}")
    lines.append(f"{GREY}open any unlocked door with: advent <number>{RESET}")
    panel("ADVENT CALENDAR - 24 security tips", lines, color=RED)


def cmd_nicelist(core, arg):
    if not need_xmas(core):
        return
    lines = [f"{GREEN}{BOLD}NICE LIST{RESET}"]
    if core.db:
        for n in sorted(core.db):
            pct = 70 + sum(map(ord, n)) % 30
            lines.append(f"  {WHITE}{n:<16}{RESET} {GREEN}{bar(pct / 100, 20)}{RESET} {pct}% nice {GREY}(scientifically calculated){RESET}")
    else:
        lines.append(f"  {GREY}nobody enrolled yet{RESET}")
    bad = [r for r in read_log() if "Unknown" in r[2] or "motion" in r[2]]
    lines += ["", f"{RED}{BOLD}NAUGHTY LIST{RESET}"]
    if bad:
        lines.append(f"  {RED}{len(bad)} suspicious visit(s) recorded{RESET}, latest at {WHITE}{bad[-1][0]}{RESET}")
        lines.append(f"  {GREY}Santa is watching...{RESET}")
    else:
        lines.append(f"  {GREEN}empty. Nobody suspicious has been recorded.{RESET}")
    panel("SANTA'S LIST", lines, color=RED)


def cmd_xmas(core, arg):
    sub, cfg = arg.strip().lower(), core.cfg
    if sub == "on":
        return cmd_set(core, "theme xmas")
    if sub == "off":
        prev = cfg.get("theme_prev", "cyber")
        return cmd_set(core, f"theme {prev if prev in THEMES and prev != 'xmas' else 'cyber'}")
    if sub == "carol":
        if not need_xmas(core):
            return
        if os.name != "nt":
            return say("warn", "the carol uses the Windows beeper, so it only plays on Windows")
        say("sys", "playing Jingle Bells (turn your speakers up)")
        threading.Thread(target=play_carol, daemon=True).start()
        return
    if sub == "countdown":
        if not need_xmas(core):
            return
        now = datetime.datetime.now()
        year = now.year if now <= datetime.datetime(now.year, 12, 25) else now.year + 1
        if now.month == 12 and now.day == 25:
            return panel("COUNTDOWN", [f"{GREEN}{BOLD}Merry Christmas! It's today!{RESET}"], color=RED)
        delta = datetime.datetime(year, 12, 25) - now
        h, rem = divmod(delta.seconds, 3600)
        m, s = divmod(rem, 60)
        return panel("COUNTDOWN TO CHRISTMAS", [
            f"{WHITE}{BOLD}{delta.days}{RESET} days  {WHITE}{BOLD}{h}{RESET} hours  {WHITE}{BOLD}{m}{RESET} minutes  {WHITE}{BOLD}{s}{RESET} seconds",
            f"{RED}{bar(1 - delta.days / 365, 40)}{RESET}"], color=RED)
    avail = xmas_available(cfg)
    panel("CHRISTMAS EDITION  (limited)", [
        f"{WHITE}status   {RESET}{GREEN + 'ON' if XMAS else (YEL + 'available - type: xmas on' if avail else GREY + 'out of season')}{RESET}",
        f"{WHITE}season   {RESET}Nov 15 - Jan 6   {GREY}({(season_end() - datetime.date.today()).days} days left){RESET}" if in_season() else
        f"{WHITE}season   {RESET}Nov 15 - Jan 6   {GREY}returns next November{RESET}",
        f"{WHITE}now      {RESET}{countdown_text()}",
        "",
        f"{CYAN}what it changes{RESET}",
        "  Santa hats, elf hats and reindeer antlers on every face (they tilt with your head)",
        "  snowfall and a blinking light garland in every camera window",
        "  NICE LIST / NAUGHTY LIST recognition labels, placed under the chin",
        "  festive Flappy (Rudolph + candy-cane pipes), photo booth strip and mirror",
        "  security recordings stay clean: no effects are baked into your clips",
        "",
        f"{CYAN}commands{RESET}  xmas on|off|carol|countdown   advent [n]   nicelist   card [sender]",
        f"{CYAN}more    {RESET}  wish   secretsanta   tree   globe        {GREY}hat style: set xmas_style auto|santa|elf|antlers{RESET}",
    ], color=RED)


WISH_PATH = os.path.join(BASE, "wishlist.json")
SANTA_PATH = os.path.join(BASE, "secretsanta.json")


def load_wishes():
    try:
        with open(WISH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_wishes(w):
    with open(WISH_PATH, "w", encoding="utf-8") as f:
        json.dump(w, f, indent=2, ensure_ascii=False)


def canon_name(core, name):
    for n in core.db:
        if n.lower() == name.lower():
            return n
    return name


def cmd_wish(core, arg):
    if not need_xmas(core):
        return
    parts = arg.split(maxsplit=2)
    sub = parts[0].lower() if parts else ""
    wishes = load_wishes()
    if sub == "add":
        if len(parts) < 3:
            return say("warn", "usage: wish add <name> <gift>   e.g. wish add Alvin a new microphone")
        name = canon_name(core, parts[1])
        wishes.setdefault(name, []).append(parts[2].strip())
        save_wishes(wishes)
        return say("ok", f"added to {WHITE}{name}{RESET}'s wish list ({len(wishes[name])} item(s))")
    if sub in ("remove", "del"):
        try:
            name, idx = canon_name(core, parts[1]), int(parts[2])
            gone = wishes[name].pop(idx - 1)
        except (IndexError, ValueError, KeyError):
            return say("warn", "usage: wish remove <name> <number>")
        if not wishes[name]:
            del wishes[name]
        save_wishes(wishes)
        return say("ok", f"removed '{gone}'")
    who = canon_name(core, arg.strip()) if arg.strip() else None
    shown = {who: wishes.get(who, [])} if who else wishes
    if not any(shown.values()):
        return say("info", "no wishes yet. Add one: wish add <name> <gift>")
    lines = []
    for n in sorted(shown):
        lines.append(f"{GREEN}{BOLD}{n}{RESET}")
        lines += [f"  {RED}{i}.{RESET} {g}" for i, g in enumerate(shown[n], 1)] or [f"  {GREY}(empty){RESET}"]
    panel("SANTA'S WISH LIST", lines, color=RED)


def draw_secret_santa(names, rng=None):
    """Random giftee for everyone; nobody draws themselves."""
    rng = rng or random.SystemRandom()
    names = list(names)
    if len(names) < 3:
        raise ValueError("need at least 3 people")
    while True:
        targets = names[:]
        rng.shuffle(targets)
        if all(a != b for a, b in zip(names, targets)):
            return dict(zip(names, targets))


def cmd_secretsanta(core, arg):
    if not need_xmas(core):
        return
    parts = arg.replace(",", " ").split()
    sub = parts[0].lower() if parts else "status"
    try:
        with open(SANTA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    if sub == "draw":
        names = list(dict.fromkeys(canon_name(core, n) for n in parts[1:])) or sorted(core.db)
        if len(names) < 3:
            return say("err", "need at least 3 people. Enroll more, or list names: secretsanta draw Alvin Bao Linh")
        if data and not confirm("a draw already exists. Redo it?"):
            return
        with open(SANTA_PATH, "w", encoding="utf-8") as f:
            json.dump({"year": datetime.date.today().year, "pairs": draw_secret_santa(names)}, f, indent=2)
        return say("ok", f"drawn for {len(names)} people. Nobody has seen the result - each person runs: {WHITE}secretsanta reveal{RESET}")
    if sub == "reset":
        if data and confirm("delete the current draw?"):
            os.remove(SANTA_PATH)
            say("ok", "draw deleted")
        return
    if not data:
        return say("info", "no draw yet. Start one: secretsanta draw   (uses everyone enrolled)")
    pairs = data["pairs"]
    if sub != "reveal":
        return panel(f"SECRET SANTA {data['year']}", [f"{GREEN}{n}{RESET}" for n in pairs] +
                     ["", f"{GREY}{len(pairs)} people drawn. Targets stay hidden until each person looks at the camera: secretsanta reveal{RESET}"], color=RED)
    say("info", "look at the camera alone. I will show only YOUR giftee once I recognise you")
    state = {"who": None, "since": {}}

    def on_result(results, now):
        mine = {n for n, _ in results if n in pairs}
        if len(results) != 1 or not mine:  # exactly one recognised face, so nobody peeks
            state["since"].clear()
            return None
        n = next(iter(mine))
        state["since"].setdefault(n, now)
        if now - state["since"][n] > 1.5:
            state["who"] = n
            return True
        return None

    live_loop(core, "SECRET SANTA", on_result)
    if state["who"]:
        clear()
        panel("SECRET SANTA", [f"{GREEN}{BOLD}{state['who']}{RESET}, you are Secret Santa for:", "",
                               f"      {RED}{BOLD}{pairs[state['who']]}{RESET}", "", f"{GREY}Shh! Press Enter to hide this screen.{RESET}"], color=RED)
        input()
        home(core)
    else:
        say("warn", "nobody was recognised (is your face enrolled and in the draw?)")


def tree_frame(tick, height=11):
    """One frame of the blinking terminal Christmas tree, as a list of strings."""
    pal = [196, 226, 33, 201, 255, 214]
    out = [" " * (height + 1) + (fg(226) if tick % 2 else fg(231)) + BOLD + "*" + RESET]
    for r in range(1, height):
        w = 2 * r + 1
        row = ""
        for c in range(w):
            if c == 0:
                row += f"{fg(34)}/{RESET}"
            elif c == w - 1:
                row += f"{fg(34)}\\{RESET}"
            elif (r * 7 + c * 13 + tick) % 6 == 0:
                row += f"{fg(pal[(r + c + tick) % len(pal)])}{BOLD}o{RESET}"
            else:
                row += f"{fg(28)}^{RESET}"
        out.append(" " * (height - r) + " " + row)
    out += [" " * (height - 1) + f"{fg(94)}[___]{RESET}", " " * (height - 4) + f"{fg(253)}*.*.*.*.*.*.*.*{RESET}"]
    return out


def cmd_tree(core, arg):
    if not need_xmas(core):
        return
    names = ", ".join(sorted(core.db)) or "everyone"
    n = 14
    for _ in range(n):
        print()
    t0, tick = time.time(), 0
    try:
        while time.time() - t0 < 8:
            lines = tree_frame(tick) + ["", f"   {RED}Merry Christmas{RESET} {WHITE}{names}{RESET}!"]
            sys.stdout.write(f"\033[{n}A")
            for ln in lines:
                sys.stdout.write("  \033[K" + ln + "\n")
            sys.stdout.flush()
            tick += 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass


class Globe:
    """Snow-globe particles: flakes drift, SPACE shakes them, they settle into a snow pile."""

    def __init__(self, n=140, r=170):
        self.rng = np.random.default_rng()
        self.R = r
        ang, rad = self.rng.uniform(0, 2 * np.pi, n), r * 0.9 * np.sqrt(self.rng.uniform(0, 1, n))
        self.p = np.stack([rad * np.cos(ang), rad * np.sin(ang)], 1)
        self.v = np.zeros((n, 2))
        self.size = self.rng.integers(1, 4, n)
        self.pile = self.rng.uniform(0, 16, n)

    def shake(self):
        self.v += self.rng.normal(0, 150, self.v.shape) + np.array([0, -140])

    def step(self, dt):
        self.v[:, 1] += 170 * dt
        self.v *= 1 - min(1.0, 1.6 * dt)
        self.p += self.v * dt
        lim = self.R * 0.95
        d = np.linalg.norm(self.p, axis=1)
        out = d > lim
        self.p[out] *= (lim / d[out])[:, None]
        floor = np.minimum(self.R * 0.78, np.sqrt(np.maximum(0.0, lim ** 2 - self.p[:, 0] ** 2))) - self.pile
        down = self.p[:, 1] > floor
        self.p[down, 1] = floor[down]
        self.v[down] = 0


def render_globe(frame, g):
    R = g.R
    W, H = 2 * R + 80, 2 * R + 150
    img = np.full((H, W, 3), (30, 24, 18), np.uint8)
    cx, cy = W // 2, R + 36
    h0, w0 = frame.shape[:2]
    s = min(h0, w0)
    crop = cv2.resize(cv2.flip(frame[(h0 - s) // 2:(h0 - s) // 2 + s, (w0 - s) // 2:(w0 - s) // 2 + s], 1), (2 * R, 2 * R))
    yy, xx = np.mgrid[0:2 * R, 0:2 * R]
    dist = np.hypot(xx - R, yy - R) / R
    glass = (crop * np.clip(1.15 - 0.55 * dist ** 2, 0.35, 1)[..., None] * np.array([1.12, 1.0, 0.86])).clip(0, 255).astype(np.uint8)
    roi = img[cy - R:cy + R, cx - R:cx + R]
    roi[dist <= 1] = glass[dist <= 1]
    for (x, y), sz in zip(g.p, g.size):
        cv2.circle(img, (int(cx + x), int(cy + y)), int(sz), (255, 255, 255), -1, cv2.LINE_AA)
    ov = img.copy()
    cv2.ellipse(ov, (cx - int(R * 0.45), cy - int(R * 0.52)), (int(R * 0.22), int(R * 0.09)), -42, 0, 360, (255, 255, 255), -1, cv2.LINE_AA)
    img = cv2.addWeighted(ov, 0.3, img, 0.7, 0)
    cv2.circle(img, (cx, cy), R, (235, 235, 255), 2, cv2.LINE_AA)
    by = cy + R - 10
    cv2.rectangle(img, (cx - int(R * 0.85), by), (cx + int(R * 0.85), by + 74), (30, 60, 120), -1)
    cv2.rectangle(img, (cx - int(R * 0.85), by), (cx + int(R * 0.85), by + 8), B_GOLD, -1)
    center_text(img, "MERRY CHRISTMAS", by + 50, 0.8, B_WHITE, 2)
    cv2.putText(img, "SPACE shake   S save   Q quit", (10, H - 10), FONT, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
    return img


def cmd_globe(core, arg):
    if not need_xmas(core):
        return
    cap = open_camera(core.cfg["camera"])
    if cap is None:
        return
    g, win, last = Globe(), "SENTINEL // SNOW GLOBE", time.time()
    say("info", f"snow globe: {WHITE}SPACE{RESET} shakes it, {WHITE}S{RESET} saves a picture, {WHITE}Q{RESET} quits")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.time()
            g.step(min(0.05, now - last))
            last = now
            img = render_globe(frame, g)
            cv2.imshow(win, img)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q") or win_closed(win):
                break
            if k == ord(" "):
                g.shake()
            if k == ord("s"):
                os.makedirs(SNAPS_DIR, exist_ok=True)
                path = os.path.join(SNAPS_DIR, f"globe_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg")
                cv2.imwrite(path, img)
                say("ok", f"saved {path}")
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()


# =============================================================================
#  DOORBELL EXTENSION  (micro:bit doorbell -> this PC -> EV3 webcam -> this PC)
#
#   doorbell micro:bit --radio--> gateway micro:bit (USB into THIS PC) --> sentinel.py
#   sentinel.py --Wi-Fi--> EV3 "RING"      EV3 --Wi-Fi--> webcam frames --> sentinel.py (face match)
#   sentinel.py --> gateway --radio--> doorbell:  SCAN, then HELLO:<name> / UNKNOWN / ERR
#   Every login lands in this window, in clips/doorbell_log.csv and (optionally) on Discord.
#   Only the gateway micro:bit needs a cable; the EV3 just needs Wi-Fi.
# =============================================================================
EXT_LOG = os.path.join(CLIPS_DIR, "doorbell_log.csv")
EXT_START_TIMEOUT = 12.0     # seconds the EV3 gets to start scanning after a ring
_ext = {"srv": None, "thread": None, "ser_thread": None, "stop": None, "engine": None,
        "ser": None, "port": None, "standby": [], "busy": False, "answered": False, "ring_id": 0}
_ext_lock = threading.Lock()       # guards the fields above
_ser_lock = threading.Lock()       # guards writes to the gateway


def ascii_name(name):
    """The micro:bit font is ASCII only: Nguyễn Đức -> Nguyen Duc."""
    s = unicodedata.normalize("NFKD", name).replace("đ", "d").replace("Đ", "D")
    s = s.encode("ascii", "ignore").decode().strip()
    return (s or "visitor")[:18]


class ExtEngine:
    """Own detector/recognizer instances, so the doorbell thread never clashes with the camera modes."""

    def __init__(self):
        self.core = Core()
        self.lock = threading.Lock()

    def preload(self):
        self.core.models()

    def refresh(self):
        """Pick up people enrolled / settings changed since the last ring."""
        with self.lock:
            self.core.db = load_db()
            self.core.cfg = load_cfg()

    def recognize(self, jpg):
        """-> (best known name or None, similarity, number of faces seen)"""
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None, -1.0, 0
        with self.lock:
            res = self.core.recognize_all(img)
        known = [(n, s) for _, n, s in res if n != "Unknown"]
        if known:
            name, score = max(known, key=lambda t: t[1])
            return name, score, len(res)
        return None, max([s for _, _, s in res], default=-1.0), len(res)


def _recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def _recv_len(conn):
    return struct.unpack(">I", _recv_exact(conn, 4))[0]


def _ext_log(event, who, detail=""):
    os.makedirs(CLIPS_DIR, exist_ok=True)
    new = not os.path.exists(EXT_LOG)
    with open(EXT_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "event", "who", "detail"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event, who, detail])


# ---- gateway micro:bit (USB serial) ------------------------------------------------------
def ext_tell_microbit(text):
    """Send one line to the gateway micro:bit, which radios it to the doorbell."""
    ser = _ext["ser"]
    if ser is None:
        return False
    try:
        with _ser_lock:
            ser.write((text + "\n").encode())
        return True
    except Exception as e:
        say("err", f"gateway micro:bit write failed: {e}")
        return False


def _ext_release():
    with _ext_lock:
        _ext["busy"] = False


def _ext_ring_watchdog(rid):
    with _ext_lock:
        stuck = _ext["busy"] and not _ext["answered"] and _ext["ring_id"] == rid
    if stuck:
        say("err", f"the EV3 did not start scanning within {EXT_START_TIMEOUT:.0f}s - check ev3_sentinel_ext.py and its Wi-Fi")
        ext_tell_microbit("ERR")
        _ext_release()


def ext_ring():
    """Doorbell pressed: wake every EV3 that is in standby."""
    with _ext_lock:
        if _ext["busy"]:
            return say("info", "doorbell pressed again while scanning - ignored")
        targets = list(_ext["standby"])
        _ext["busy"], _ext["answered"] = True, False
        _ext["ring_id"] += 1
        rid = _ext["ring_id"]
    say("net", f"{BLUE}doorbell pressed{RESET} - waking the EV3")
    sent = 0
    for conn in targets:
        try:
            conn.sendall(b"RING\n")
            sent += 1
        except OSError:
            pass
    if not sent:
        say("err", "no EV3 in standby - is ev3_sentinel_ext.py running and connected to this PC?")
        ext_tell_microbit("ERR")
        return _ext_release()
    ext_tell_microbit("SCAN")
    t = threading.Timer(EXT_START_TIMEOUT, _ext_ring_watchdog, args=(rid,))
    t.daemon = True
    t.start()


def _ext_serial_loop(stop):
    try:
        import serial
        import serial.tools.list_ports as lp
    except ImportError:
        return say("err", f"the doorbell needs pyserial:  {WHITE}pip install pyserial{RESET}   then:  ext on")
    warned = False
    while not stop.is_set():
        pref = str(load_cfg().get("ext_serial", "auto"))
        port = pref if pref != "auto" else next((p.device for p in lp.comports() if p.vid == 0x0D28), None)  # 0x0D28 = micro:bit
        if not port:
            if not warned:
                say("warn", f"gateway micro:bit not found - plug it into this PC (or: {WHITE}set ext_serial COM5{RESET})")
                warned = True
            stop.wait(3)
            continue
        try:
            ser = serial.Serial(port, 115200, timeout=0.5)
        except (OSError, serial.SerialException) as e:
            if not warned:
                say("warn", f"can't open {port}: {e}  (close the micro:bit editor / serial monitor tab?)")
                warned = True
            stop.wait(3)
            continue
        warned = False
        _ext["ser"], _ext["port"] = ser, port
        say("ok", f"gateway micro:bit connected on {WHITE}{port}{RESET}")
        try:
            while not stop.is_set():
                if ser.readline().decode("utf-8", "ignore").strip() == "RING":
                    ext_ring()
        except (OSError, serial.SerialException):
            say("warn", "gateway micro:bit disconnected")
        finally:
            _ext["ser"], _ext["port"] = None, None
            try:
                ser.close()
            except Exception:
                pass


# ---- EV3 over Wi-Fi ----------------------------------------------------------------------
# Wire format (big-endian ints). EV3 -> PC first message: [len]["<token>|standby"] or [len]["<token>|scan"]
#   standby: connection stays open; the EV3 sends "." as heartbeat, the PC sends "RING\n".
#   scan:    EV3 sends [len][jpeg]...  then [0];  PC answers each frame with {"name":..,"score":..,"faces":..}\n
def _ext_standby(conn, ip):
    conn.settimeout(45)                         # the EV3 sends a heartbeat every 5 s (silent while scanning)
    with _ext_lock:
        _ext["standby"].append(conn)
    say("ok", f"EV3 {WHITE}{ip}{RESET} is in standby")
    try:
        while conn.recv(64):
            pass
    except OSError:
        pass
    finally:
        with _ext_lock:
            if conn in _ext["standby"]:
                _ext["standby"].remove(conn)
        say("warn", f"EV3 {ip} left standby")


def _ext_chime(cfg, ok):
    """Optional extra beep on this PC (Windows). The micro:bit does the normal beeping."""
    if not cfg.get("ext_beep", False):
        return

    def run():
        if ok:
            beep(880, 120)
            beep(1175, 220)
        else:
            beep(220, 450)

    threading.Thread(target=run, daemon=True).start()


def _ext_scan(conn, ip, engine, cfg):
    with _ext_lock:
        _ext["answered"] = True
    say("net", f"EV3 {WHITE}{ip}{RESET} is scanning")
    engine.refresh()
    who, best, frames, faces_seen, told = None, -1.0, 0, 0, False
    while True:
        n = _recv_len(conn)
        if n == 0:
            break                               # EV3 is done
        if n > 5_000_000:
            raise ValueError("frame too large")
        name, score, nfaces = engine.recognize(_recv_exact(conn, n))
        frames += 1
        faces_seen = max(faces_seen, nfaces)
        if name and who is None:
            who, best = name, score
            ext_tell_microbit("HELLO:" + ascii_name(who))     # answer the doorbell right away
            _ext_chime(cfg, True)
            told = True
        conn.sendall((json.dumps({"name": name, "score": round(float(score), 3), "faces": nfaces}) + "\n").encode())
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    if who:
        pct = max(0.0, best) * 100
        say("id", f"{GREEN}{BOLD}LOGIN{RESET} {WHITE}{who}{RESET} recognised at the door ({pct:.0f}%) "
                  f"{GREY}via EV3 doorbell, {frames} frame(s){RESET}")
        _ext_log("login", who, f"{pct:.0f}% from {ip}")
        notify(cfg["webhook"], f"Login at the door: {who} ({pct:.0f}%) at {stamp}")
    else:
        what = "stranger at the door" if faces_seen else "doorbell rang, no face in view"
        say("warn", f"{what} {GREY}({frames} frame(s), {faces_seen} face(s) seen){RESET}")
        _ext_log("unknown" if faces_seen else "noface", "-", f"{frames} frames from {ip}")
        notify(cfg["webhook"], f"Doorbell at {stamp}: {what}")
        if not told:
            ext_tell_microbit("UNKNOWN")
            _ext_chime(cfg, False)


def _ext_session(conn, addr, engine):
    ip, scanning = addr[0], False
    try:
        conn.settimeout(30)
        cfg = load_cfg()
        n = _recv_len(conn)
        hello = _recv_exact(conn, n) if 0 < n <= 160 else b""
        tok, _, role = hello.decode("utf-8", "replace").rpartition("|")
        if not hmac.compare_digest(tok.encode(), str(cfg["ext_token"]).encode()) or role not in ("standby", "scan"):
            conn.sendall(b'{"error": "bad token"}\n')
            return say("warn", f"doorbell: rejected a connection from {ip} (wrong token)")
        if role == "standby":
            return _ext_standby(conn, ip)
        scanning = True
        _ext_scan(conn, ip, engine, cfg)
    except (ConnectionError, socket.timeout, ValueError, OSError) as e:
        say("err", f"doorbell session with {ip} failed: {type(e).__name__}: {e}")
        if scanning:
            ext_tell_microbit("ERR")
    except Exception as e:
        say("err", f"doorbell session crashed: {type(e).__name__}: {e}")
        if scanning:
            ext_tell_microbit("ERR")
    finally:
        if scanning:
            _ext_release()
        try:
            conn.close()
        except OSError:
            pass


def _ext_serve(srv, engine, stop):
    while not stop.is_set():
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break                               # socket closed by ext_stop
        threading.Thread(target=_ext_session, args=(conn, addr, engine), daemon=True).start()


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # no packet is sent; just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ext_running():
    return bool(_ext["thread"] and _ext["thread"].is_alive())


def ext_start(core):
    if ext_running():
        return say("info", "doorbell extension is already listening")
    port = int(core.cfg["ext_port"])
    if _ext["engine"] is None:
        _ext["engine"] = ExtEngine()
    say("info", "loading face models for the doorbell ...")
    _ext["engine"].preload()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", port))
    except OSError as e:
        srv.close()
        return say("err", f"can't listen on port {port}: {e}")
    srv.listen(4)
    srv.settimeout(1.0)
    stop = threading.Event()
    t = threading.Thread(target=_ext_serve, args=(srv, _ext["engine"], stop), daemon=True)
    st = threading.Thread(target=_ext_serial_loop, args=(stop,), daemon=True)
    _ext.update(srv=srv, thread=t, ser_thread=st, stop=stop, busy=False)
    t.start()
    st.start()
    say("ok", f"doorbell extension {GREEN}STANDBY{RESET}: set PC_HOST = {WHITE}{local_ip()}{RESET}, PC_PORT = {WHITE}{port}{RESET} in the EV3 script")
    if core.cfg["ext_token"] == "change-me":
        say("warn", f"ext_token is still the default - choose your own: {WHITE}set ext_token <secret>{RESET} (same value in the EV3 script)")
    if not core.db:
        say("warn", "nobody enrolled - every visitor will count as a stranger (use: enroll <name>)")


def ext_stop():
    if not ext_running():
        return say("info", "doorbell extension is not running")
    _ext["stop"].set()
    try:
        _ext["srv"].close()
    except OSError:
        pass
    with _ext_lock:
        conns, _ext["standby"] = list(_ext["standby"]), []
    for c in conns:
        try:
            c.close()
        except OSError:
            pass
    _ext["thread"].join(timeout=3)
    _ext["ser_thread"].join(timeout=3)
    say("ok", "doorbell extension stopped")


def cmd_ext(core, arg):
    sub = arg.strip().lower() or "status"
    if sub == "on":
        core.cfg["ext"] = True
        save_cfg(core.cfg)
        return ext_start(core)
    if sub == "off":
        core.cfg["ext"] = False
        save_cfg(core.cfg)
        return ext_stop()
    if sub == "log":
        if not os.path.exists(EXT_LOG):
            return say("warn", "no doorbell events yet")
        with open(EXT_LOG, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))[1:][-10:]
        lines = [f"{GREY}{r[0]}{RESET} {(GREEN if r[1] == 'login' else RED)}{r[1]:<8}{RESET} {WHITE}{r[2]:<14}{RESET}{GREY}{r[3]}{RESET}"
                 for r in rows]
        return panel("DOORBELL LOG", lines, color=ORANGE)
    c = core.cfg
    n_ev3 = len(_ext["standby"])
    panel("DOORBELL EXTENSION", [
        f"{WHITE}listener {RESET}{GREEN + 'STANDBY' if ext_running() else GREY + 'off'}{RESET}   {GREY}(ext on | ext off | ext log){RESET}",
        f"{WHITE}EV3 link {RESET}{local_ip()}:{c['ext_port']}   {(GREEN + str(n_ev3) + ' EV3 in standby') if n_ev3 else (YEL + 'no EV3 connected')}{RESET}",
        f"{WHITE}gateway  {RESET}{(GREEN + 'micro:bit on ' + str(_ext['port'])) if _ext['ser'] else (YEL + 'not connected (plug it into this PC)')}{RESET}",
        f"{WHITE}token    {RESET}{(YEL + 'default - change it!') if c['ext_token'] == 'change-me' else GREEN + 'custom'}{RESET}",
        f"{WHITE}discord  {RESET}{'linked' if c['webhook'] else 'off'}      {WHITE}people{RESET} {len(core.db)} enrolled",
    ], color=ORANGE)


# ----------------------------------------------------------------------------- command table / menu
# (category, name, args, short description, function, longer help)
COMMANDS = [
    ("VISION", "enroll", "<name>", "add a face (webcam)", cmd_enroll, "Opens the camera. SPACE captures a sample, A auto-captures 10, Q finishes. Run again to add more samples."),
    ("VISION", "live", "", "live recognition", cmd_live, "Live recognition HUD. Q quits."),
    ("VISION", "image", "<path>", "recognize a photo", cmd_image, "Finds and names every face in an image file."),
    ("VISION", "compare", "<img1> <img2>", "same person? (2 photos)", cmd_compare, "Compares the largest face of two photos and prints a similarity score."),
    ("VISION", "sort", "<folder>", "sort photos by person", cmd_sort, "Copies each photo into sorted/<name>/ (or _unknown / _nofaces). Originals are never moved."),
    ("VISION", "snap", "", "countdown selfie", cmd_snap, "3-second countdown, saves an annotated photo to snaps/."),
    ("VISION", "gallery", "", "show enrolled faces", cmd_gallery, "Contact sheet of everyone enrolled (thumbnails come from enroll)."),
    ("VISION", "people", "", "list enrolled people", cmd_people, "Names and sample counts."),
    ("VISION", "remove", "<name>", "delete a person", cmd_remove, "Asks for confirmation first."),
    ("VISION", "rename", "<old> <new>", "rename a person", cmd_rename, "Renames an identity (single-word names; use prompts for spaces)."),
    ("SECURITY", "cam", "", "ARM security camera", cmd_cam, "Records clips + snapshots to clips/. Trigger on faces, motion or both (set trigger). Discord alerts if a webhook is set."),
    ("SECURITY", "guard", "", "lock PC when you leave", cmd_guard, "Windows only. Locks the screen after lock_after seconds with no enrolled face in view."),
    ("SECURITY", "logs", "", "last 10 events", cmd_logs, "Most recent security events."),
    ("SECURITY", "stats", "", "charts: who / when", cmd_stats, "Event counts per person and per hour of day."),
    ("SECURITY", "report", "", "HTML event report", cmd_report, "Generates report.html with snapshots and clip links, and opens it."),
    ("SECURITY", "clips", "", "open clips folder", cmd_clips, "Opens clips/ in Explorer."),
    ("SECURITY", "purge", "<days>", "delete old clips", cmd_purge, "Deletes clips/snapshots older than N days (asks first). The log is kept."),
    ("PRODUCTIVITY", "attendance", "[show]", "daily check-in log", cmd_attendance, "Logs each enrolled person's first sighting of the day to attendance.csv. 'attendance show' lists today."),
    ("PRODUCTIVITY", "focus", "[minutes]", "study / focus timer", cmd_focus, "Pomodoro timer that tracks desk presence, away breaks and screen distance. Summary at the end."),
    ("PRODUCTIVITY", "greet", "", "greets you by name", cmd_greet, "Says hello (spoken on Windows) when it recognizes someone, once per greet_every seconds."),
    ("PRODUCTIVITY", "mirror", "", "magic mirror display", cmd_mirror, "Fullscreen mirror with clock, date and a personal greeting."),
    ("FUN", "flappy", "", "head-controlled game", cmd_flappy, "Move your head up/down to steer the bird. Hide your face and it falls."),
    ("FUN", "booth", "", "4-shot photo strip", cmd_booth, "Countdown photo booth that saves a photo strip to snaps/."),
    ("SYSTEM", "scan", "", "find cameras", cmd_scan, "Tests camera indices 0-4."),
    ("SYSTEM", "bench", "", "speed benchmark", cmd_bench, "Times detection and recognition on your machine."),
    ("SYSTEM", "doctor", "", "health check", cmd_doctor, "Checks Python, OpenCV, models, camera, folders and Discord."),
    ("SYSTEM", "settings", "", "show settings", cmd_settings, "All options and their current values."),
    ("SYSTEM", "set", "<key> <val>", "change a setting", cmd_set, "Example: set theme matrix | set trigger both | set post 8 | set webhook <url> | set webhook off"),
    ("SYSTEM", "test", "", "test Discord alert", cmd_test, "Sends a test message to your webhook."),
    ("SYSTEM", "backup", "", "zip faces + settings", cmd_backup, "Saves backups/sentinel_backup_*.zip (the webhook URL is NOT included)."),
    ("SYSTEM", "restore", "<zip>", "load a backup", cmd_restore, "Restores faces, thumbnails and settings from a backup zip."),
    ("SYSTEM", "history", "", "command history", cmd_history, "Shows recent commands. Use !! or !<n> to repeat."),
    ("XMAS", "xmas", "[on|off|carol]", "Christmas edition", cmd_xmas, "Limited Christmas theme (Nov 15 - Jan 6): xmas on / xmas off, xmas carol plays Jingle Bells (Windows), xmas countdown."),
    ("XMAS", "advent", "[n]", "advent calendar + tips", cmd_advent, "24 doors, one security tip each. A door opens on its date in December. advent 5 shows door 5."),
    ("XMAS", "nicelist", "", "Santa's nice/naughty list", cmd_nicelist, "Enrolled people go on the nice list; unknown visitors and motion events from the security log go on the naughty list."),
    ("XMAS", "card", "[sender]", "Christmas photo card", cmd_card, "Countdown photo with Santa hats, snow and a candy-cane border, saved to snaps/. Example: card the Nguyen family"),
    ("XMAS", "wish", "[add|remove|name]", "wish lists (greeter reads them)", cmd_wish, "wish add Alvin a new microphone | wish remove Alvin 1 | wish Alvin | wish. The greeter tells you what Santa knows you want."),
    ("XMAS", "secretsanta", "[draw|reveal|reset]", "Secret Santa, face-locked", cmd_secretsanta, "draw = random giftees for everyone enrolled (or: draw Alvin Bao Linh). reveal = your own face unlocks ONLY your giftee. Pairings are stored in plain text in secretsanta.json, so do not peek at it."),
    ("XMAS", "tree", "", "blinking terminal tree", cmd_tree, "Animated Christmas tree in this window for 8 seconds."),
    ("XMAS", "globe", "", "snow globe (webcam)", cmd_globe, "Your webcam inside a snow globe. SPACE shakes it, S saves a picture."),
    ("SECURITY", "ext", "[on|off|log]", "EV3 doorbell extension", cmd_ext, "Doorbell chain: micro:bit doorbell -> gateway micro:bit (USB into this PC, needs: pip install pyserial) -> this PC wakes the EV3 over Wi-Fi -> EV3 streams webcam frames here -> known faces are logged as a LOGIN (window, clips/doorbell_log.csv, Discord) and the doorbell shows Hello <name>. 'ext on' starts it (and at every startup), 'ext off' stops it, 'ext log' shows recent events. Set a secret with: set ext_token <secret>."),
]
TABLE = {c[1]: c for c in COMMANDS}


def draw_status(core):
    c = core.cfg
    ok = lambda b, t, f: f"{GREEN}● {t}{RESET}" if b else f"{YEL}○ {f}{RESET}"
    people = ", ".join(sorted(core.db)) if core.db else "nobody yet"
    lines = [
        f"{ok(models_ready(), 'models ready', 'models download on first use')}    {ok(bool(core.db), f'{len(core.db)} enrolled', 'no faces enrolled')}",
        f"{GREY}people:{RESET} {people[:70]}",
        f"{GREY}camera:{RESET} {c['camera']}    {GREY}cam trigger:{RESET} {c['trigger']}"
        f"{' (strangers only)' if c['only_unknown'] and c['trigger'] != 'motion' else ''}    {GREY}discord:{RESET} "
        f"{GREEN + 'linked' + RESET if c['webhook'] else GREY + 'off' + RESET}    {GREY}theme:{RESET} {c['theme']}",
    ]
    if XMAS:
        lines.append(f"{RED}*{RESET} {WHITE}CHRISTMAS EDITION{RESET} {GREY}(limited, ends {season_end():%b %d}){RESET}   {YEL}{countdown_text()}{RESET}")
    elif xmas_available(c):
        lines.append(f"{YEL}* LIMITED EDITION:{RESET} the Christmas theme is live until {season_end():%b %d}. Type {WHITE}xmas on{RESET}")
    panel("SYSTEM STATUS", lines)


def draw_menu(core=None):
    show_xmas = core is not None and xmas_available(core.cfg)

    def block(cat):
        title = f"{RED}{BOLD}XMAS{RESET} {GREY}limited{RESET}" if cat == "XMAS" else f"{ORANGE}{BOLD}{cat}{RESET}"
        out = [title]
        for i, c in enumerate(COMMANDS, 1):
            if c[0] == cat:
                out.append(f"{MAG}{i:>2}{RESET} {CYAN}{c[1]:<11}{RESET}{GREY}{c[3]}{RESET}")
        return out

    w = panel_width()
    if w >= 98:
        left = block("VISION") + [""] + block("SECURITY")
        right = block("PRODUCTIVITY") + [""] + block("FUN") + [""] + block("SYSTEM")
        half = (w - 6) // 2
        lines = [pad(left[i] if i < len(left) else "", half) + (right[i] if i < len(right) else "") for i in range(max(len(left), len(right)))]
    else:
        lines = []
        for cat in ("VISION", "SECURITY", "PRODUCTIVITY", "FUN", "SYSTEM"):
            lines += block(cat)
    lines += ["", f"{GREY}type a name or number  |  help <cmd>  menu  status  cls  exit  |  !! repeats the last command{RESET}"]
    panel("COMMANDS", lines, color=BLUE)
    if show_xmas:  # limited-edition commands get their own red panel
        cells = [f"{RED}{i:>2}{RESET} {CYAN}{c[1]:<12}{RESET}{GREY}{c[3]}{RESET}" for i, c in enumerate(COMMANDS, 1) if c[0] == "XMAS"]
        if w >= 98:
            half, colw = (len(cells) + 1) // 2, (w - 6) // 2
            cells = [pad(cells[k], colw) + (cells[k + half] if k + half < len(cells) else "") for k in range(half)]
        panel("XMAS  limited edition", cells, color=RED)


def home(core):
    clear()
    if XMAS:
        print(snow_line())
    banner()
    draw_status(core)
    draw_menu(core)
    if XMAS:
        print(snow_line())


def show_help(arg):
    c = TABLE.get(arg.strip().lower())
    if not c:
        return say("err", f"no such command: {arg}")
    import textwrap
    body = textwrap.wrap(c[5], panel_width() - 14) or [""]
    lines = [f"{WHITE}usage{RESET}  {c[1]} {c[2]}", f"{WHITE}what {RESET}  {body[0]}"] + [f"        {b}" for b in body[1:]]
    panel(f"HELP: {c[1]}", lines)


def boot(core):
    clear()
    if XMAS:
        snow_anim()
    banner()
    steps = [("vision core", f"opencv {cv2.__version__}"),
             ("detector model", "ready" if os.path.exists(DET_PATH) else "downloads on first use"),
             ("recognizer model", "ready" if os.path.exists(REC_PATH) else "downloads on first use"),
             ("face database", f"{len(core.db)} identities"),
             ("configuration", "loaded" if os.path.exists(CFG_PATH) else "defaults"),
             ("alert channel", "discord linked" if core.cfg["webhook"] else "offline")]
    if XMAS:
        steps = [("sleigh uplink", "online"), ("reindeer vision core", f"opencv {cv2.__version__}"),
                 ("detector elves", "ready" if os.path.exists(DET_PATH) else "downloads on first use"),
                 ("recognizer elves", "ready" if os.path.exists(REC_PATH) else "downloads on first use"),
                 ("nice list", f"{len(core.db)} identities"), ("naughty list", "armed"), ("countdown", countdown_text())]
    for label, val in steps:
        print(f"  {CYAN}▸{RESET} {label:<20}", end="", flush=True)
        time.sleep(0.07)
        print(f"{GREEN}{val}{RESET}")
    time.sleep(0.35)


def main():
    setup_terminal()
    core = Core()
    note = None
    if core.cfg["theme"] == "xmas" and not xmas_available(core.cfg):  # limited edition has ended
        prev = core.cfg.get("theme_prev", "cyber")
        core.cfg["theme"] = prev if prev in THEMES and prev != "xmas" else "cyber"
        save_cfg(core.cfg)
        note = "The Christmas edition has ended for this year. Your previous theme is back. See you next November!"
    apply_theme(core.cfg["theme"])
    set_style(core.cfg.get("xmas_style", "auto"))
    boot(core)
    home(core)
    if note:
        say("sys", note)
    if core.cfg.get("ext"):
        ext_start(core)
    while True:
        try:
            line = prompt().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("!"):
            try:
                line = HISTORY[-1] if line == "!!" else HISTORY[int(line[1:]) - 1]
                print(f"  {GREY}> {line}{RESET}")
            except (IndexError, ValueError):
                say("err", "nothing to repeat")
                continue
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if cmd in ("exit", "quit", "q"):
            break
        # never keep the secret webhook URL in the on-screen history
        HISTORY.append("set webhook ********" if cmd == "set" and arg.lower().startswith("webhook") else line)
        if cmd == "help" and arg:
            show_help(arg)
            continue
        if cmd in ("menu", "help", "?", "cls", "clear"):
            home(core)
            continue
        if cmd == "status":
            draw_status(core)
            continue
        if cmd.isdigit() and 1 <= int(cmd) <= len(COMMANDS):
            cmd = COMMANDS[int(cmd) - 1][1]
        if cmd not in TABLE:
            guess = difflib.get_close_matches(cmd, TABLE, 1)
            say("err", f"unknown command '{cmd}'" + (f" - did you mean {WHITE}{guess[0]}{RESET}?" if guess else f" - type {WHITE}menu{RESET}"))
            continue
        try:
            TABLE[cmd][4](core, arg)
        except KeyboardInterrupt:
            cv2.destroyAllWindows()
            say("warn", "interrupted")
        except Exception as e:
            cv2.destroyAllWindows()
            say("err", f"{type(e).__name__}: {e}")
    print(f"{GREY}SENTINEL offline.{RESET}")


if __name__ == "__main__":
    main()
