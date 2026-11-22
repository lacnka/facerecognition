# ONE micro:bit  -  DOORBELL, plugged into your PC by USB (no radio, no second micro:bit)
# Paste into https://python.microbit.org and flash, then leave it plugged into the PC.
#
# Button A = doorbell. It writes "RING" to the USB serial port; sentinel.py (on the PC) answers with:
#   SCAN        -> animation while the EV3 scans your face
#   HELLO:name  -> two-tone beep, then scrolls "Hello name"
#   UNKNOWN     -> low beep and an X
#   ERR         -> low beep and "!"  (EV3 offline / webcam problem)
from microbit import *
import music

BEEP = True                 # beep on the micro:bit: built-in speaker on v2, or a buzzer on pin 0 for v1
WAIT_MS = 20000             # give up waiting for an answer after this long

uart.init(115200)           # no pins given = the USB serial connection to the PC

FRAMES = [Image("00000:00000:00900:00000:00000"),
          Image("00000:09990:09090:09990:00000"),
          Image("99999:90009:90009:90009:99999")]


def beep(ok):
    if not BEEP:
        return
    if ok:                                  # recognised: two rising tones
        music.pitch(880, 120)
        music.pitch(1175, 220)
    else:                                   # stranger / error: one low tone
        music.pitch(220, 450)


def show_answer(msg):
    if msg is None:
        beep(False)
        display.scroll("?", delay=80)
    elif msg.startswith("HELLO:"):
        beep(True)
        display.scroll("Hello " + msg[6:], delay=90)
    elif msg == "UNKNOWN":
        beep(False)
        display.show(Image.NO)
        sleep(2000)
    else:                                   # ERR or anything unexpected
        beep(False)
        display.show("!")
        sleep(2000)
    display.clear()


display.show(Image.HAPPY)
sleep(600)
display.clear()

waiting = False
started = 0
last_frame = 0
i = 0

while True:
    if button_a.was_pressed() and not waiting:
        uart.write("RING\n")
        waiting = True
        started = running_time()

    if uart.any():
        line = uart.readline()
        if line:
            try:
                msg = str(line, "utf-8").strip()
            except Exception:
                msg = ""
            if msg == "SCAN":
                waiting = True
                started = running_time()
            elif msg:
                show_answer(msg)
                waiting = False
                button_a.was_pressed()      # forget presses made while scanning

    if waiting:
        now = running_time()
        if now - started > WAIT_MS:
            show_answer(None)
            waiting = False
            button_a.was_pressed()
        elif now - last_frame > 250:
            display.show(FRAMES[i % 3])
            i += 1
            last_frame = now
    sleep(20)
