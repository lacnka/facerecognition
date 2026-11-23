# Building the SENTINEL doorbell: design notes

These notes explain how the doorbell extension of SENTINEL was designed, what went wrong while building it,
and how each problem was fixed. It worked end to end on real hardware on 22 November 2026: a micro:bit button
press led to a recognised face (91% match) and a `LOGIN` line in the SENTINEL window.

## Goal

Press a doorbell button on a micro:bit. A standby "SENTINEL extension" on a LEGO EV3 with a webcam scans the
visitor's face. If the person is recognised, the micro:bit shows "Hello <name>" and beeps, and the PC running
`sentinel.py` logs that a login was detected. Everything except the micro:bit had to be wireless.

## Constraints that shaped the design

- **One micro:bit.** The first design used two (a battery-powered doorbell and a gateway into the PC). With one,
  the micro:bit stays plugged into the PC by USB and does everything: button, display, beeper and serial link.
- **No Wi-Fi dongle for the EV3.** Its only network is Bluetooth tethering to an iPhone hotspot, with the PC on
  the same hotspot. The webcam is therefore the only USB device on the EV3, so no hub is needed.
- **The EV3's speaker is broken.** The beep moved to the micro:bit (built-in speaker on v2, a buzzer on pin 0 on v1).
- **The EV3 is too slow for the recognizer.** YuNet and SFace need far more than a 300 MHz ARM9 with 64 MB of RAM
  can give, so the EV3 only captures frames and the PC does the face matching, using the faces already enrolled
  in `sentinel.py`.

## Architecture

```
micro:bit --USB serial--> sentinel.py <--TCP over the hotspot--> EV3 + webcam
```

- **micro:bit to PC:** one text line per message. The micro:bit sends `RING`. The PC replies `SCAN`, then one of
  `HELLO:<ascii name>`, `UNKNOWN` or `ERR`.
- **EV3 to PC, standby:** the EV3 opens a TCP connection and keeps it open, sending a `.` heartbeat every
  5 seconds. The PC sends `RING\n` down it. The EV3 initiates the connection, so the PC never needs the
  EV3's address.
- **EV3 to PC, scan:** on a ring the EV3 opens a second connection and sends frames as `[4-byte length][JPEG]`,
  ending with a zero length. The PC answers every frame with one JSON line (`name`, `score`, `faces`).
- **Hello message:** the first message of each connection is `<token>|standby` or `<token>|scan`, compared with a
  constant-time check.
- **Face matching** runs on the PC in its own detector/recognizer instance, so the doorbell thread never
  collides with the camera modes of the control panel.

### Failure handling

- No EV3 in standby: the micro:bit shows `!` straight away.
- The EV3 gets the ring but never starts scanning: a 12 second watchdog sends `ERR`.
- The webcam dies mid-scan: the PC sees the connection close and sends `ERR`.
- A second press during a scan is ignored. The "busy" state is always released, including after errors.
- The micro:bit itself gives up with `?` after 20 seconds.

## Problems hit and how they were fixed

| # | Problem | Cause | Fix |
|---|---|---|---|
| 1 | The design needed two micro:bits | Assumed a doorbell plus a gateway | One micro:bit on USB doing both jobs |
| 2 | The EV3 needed a hub for webcam, Wi-Fi dongle and the micro:bit | Assumed a Wi-Fi dongle and a micro:bit on the EV3 | Bluetooth tethering to the hotspot, micro:bit moved to the PC |
| 3 | The planned EV3 beep would never be heard | The EV3's speaker is broken | The micro:bit beeps; the PC beep is an optional extra |
| 4 | `ssh robot@ev3dev.local` timed out | The `.local` name resolved to something unreachable | Use the EV3's IP address |
| 5 | `apt-get update` failed with 404s on the EV3 | Debian stretch moved to `archive.debian.org` | Point `/etc/apt/sources.list` at the archive and disable the expiry check |
| 6 | `ping` from the EV3 to the PC failed | Windows blocks ping on "public" networks, and a phone hotspot counts as public | Test the real TCP port instead (it connected) and allow Python on both profiles |
| 7 | The webcam pictures were black | `fswebcam` skipped too few frames for the webcam to set its exposure | Skip 30 frames before every picture and use the MJPEG format |
| 9 | The first ring worked but the next ring saw only black pictures (until the micro:bit was replugged, which wrongly looked like a micro:bit fault) | A first fix skipped only 10 frames when the previous capture was recent, but `fswebcam` reopens the camera for every picture, so the exposure restarts every time | Always skip 30 frames; a picture under 10 KB counts as black and is retried once with 60 |
| 10 | The micro:bit link could go quiet and needed a replug | A serial link that stops answering looks the same as an idle one, because the micro:bit only talks on a button press | The micro:bit sends a heartbeat every 2 s; SENTINEL reopens the port after 8 s of silence (not while scanning, and not right after an answer, when the display is busy scrolling) and sends a serial break after 3 failures. The watchdog stays disarmed until the first heartbeat, so older programs keep working. A first version disarmed itself after the first reopen; a simulated test caught it |
| 8 | Vietnamese names break the display and the log | The micro:bit font is ASCII only, and Windows CSV defaults are not UTF-8 | The micro:bit gets an ASCII version of the name (`Nguyễn Đức` becomes `Nguyen Duc`); the log keeps the full name in UTF-8 |

## How it was tested

Real hardware was only available at the end, so most of the logic was tested in simulation first:

- A scripted EV3 client and a scripted recognizer exercised the network protocol: known face, stranger,
  empty doorway, wrong token, a webcam that dies mid-scan, an EV3 that ignores the ring, and recovery afterwards.
- A pseudo-terminal stood in for the micro:bit's serial port.
- A tiny fake `microbit` module ran the MicroPython program through the same cases.
- The real recognizer models and the real micro:bit radio and speaker paths could only be checked on the hardware.

## Known limits and ideas

- The PC's hotspot IP can change between sessions, so the EV3 needs the new address (`ext` shows it).
- The link is plain TCP protected only by a shared secret. That is fine on a private hotspot, but it is not
  encrypted and should never face the internet.
- The doorbell is wired to the PC by the micro:bit's USB cable, so the PC has to be near the door (or use a long cable).
- Ideas: start the EV3 script at boot, let the EV3 find the PC automatically, drive a motor to open a latch
  on a match, and attach a photo of each visitor to the log and the Discord alert.

## Privacy notes

Face data is biometric. The face database, thumbnails, clips and settings file (which can contain a Discord
webhook URL) are excluded from this repository by `.gitignore`. Enroll and record only people who have agreed to it.
