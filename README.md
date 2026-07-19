# Cyberdeck Terminal

A full-color terminal emulator for the **Raspberry Pi 4** with a [GoodTFT 3.5" GPIO LCD (ILI9486 / LCD35)](https://github.com/goodtft/LCD-show).

> **No LCD kernel driver needed** — drives the display directly via Python SPI (`luma.lcd`).
> HDMI output is completely unaffected. No LCD-show install required.

---

## Features

- **No kernel LCD driver** — SPI direct-drive via `luma.lcd`; HDMI stays fully operational
- Full color ANSI/256-color/true-color terminal on the GPIO LCD
- **System stats dashboard** on startup (CPU, RAM, disk, temp)
- **GPIO kill-switch** — short physical pin 40 to GND to enable keyboard input; release to return keyboard to the desktop
- USB and BLE keyboard support via evdev
- Shell restarts automatically if it exits
- Software rotation (0/90/180/270°) configurable without reinstalling drivers

---

## Pin Reference

| Function | Physical Pin | BCM |
|---|---|---|
| LCD SPI MOSI | 19 | 10 |
| LCD SPI MISO | 21 | 9 |
| LCD SPI SCLK | 23 | 11 |
| LCD Chip Select | 24 | 8 |
| LCD D/C (RS) | 18 | 24 |
| LCD Reset | 22 | 25 |
| Touch IRQ | 11 | 17 |
| Touch CS | 26 | 7 |
| **Kill-switch** | **40** | **BCM 21** |

> **Kill-switch behavior:**  
> Pin 40 → GND = keyboard **exclusively captured** by terminal (evdev grab)  
> Pin 40 floating = keyboard **returned to the desktop** (X11/system gets events)

---

## Installation

> **No LCD-show drivers needed.** The display is driven directly from Python.

### Step 1 — Install Cyberdeck Terminal
```bash
git clone <your-repo-url> rpi4-cyberdeck
cd rpi4-cyberdeck
chmod +x install.sh
sudo ./install.sh
```

The installer will:
- Enable SPI on the Pi automatically (`raspi-config nonint do_spi 0`)
- Install `luma.lcd`, `pygame`, `pyte`, `evdev`, `psutil`
- Enable the systemd service (auto-starts on boot)

> If the installer says **"Reboot required for SPI to activate"**, reboot and then start the service.

### Step 2 — Start the terminal
```bash
sudo systemctl start cyberdeck-term
# Or reboot — it starts automatically on every boot
```

---

## Usage

```bash
cyberdeck-term start     # start the terminal
cyberdeck-term stop      # stop the terminal
cyberdeck-term restart   # restart (use after config changes)
cyberdeck-term status    # show service status
cyberdeck-term log       # live log output (Ctrl+C to exit)
```

---

## Configuration

Edit `/etc/cyberdeck-term/config.ini` then run `cyberdeck-term restart`.

```ini
[display]
fb_device  = /dev/fb1      # framebuffer device
width      = 480           # set by LCD driver (don't change unless driver changes)
height     = 320
rotation   = 0             # ← CHANGE THIS TO ROTATE: 0 / 90 / 180 / 270
font_size  = 13            # larger = fewer cols/rows, easier to read
fps        = 30

[gpio]
kill_switch_pin  = 21      # BCM number (physical 40)
pull_up          = true    # internal pull-up (recommended)
poll_interval_ms = 50

[terminal]
shell       = /bin/bash
startup_cmd = python3 /opt/cyberdeck-term/sysinfo.py  # leave blank to skip

[colors]
default_fg = 204,204,204   # R,G,B foreground when no ANSI color set
default_bg = 12,12,12      # R,G,B background
```

### Changing Orientation

```bash
sudo nano /etc/cyberdeck-term/config.ini
# Set: rotation = 180   (flip upside-down)
# Set: rotation = 90    (portrait)
cyberdeck-term restart
```

---

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│                  cyberdeck-term.py                           │
│                                                              │
│  /bin/bash ◄──PTY──► pyte (VT100) ──► pygame (offscreen)    │
│                                              │               │
│                                         PIL Image            │
│                                              │               │
│                                       luma.lcd (SPI)         │
│                                              │               │
│                                       ILI9486 LCD ← no /dev/fb1 ─┐
│                                                              │   │
│  HDMI output ──────────────────────────────────── unaffected ┘   │
│                                                                   │
│  evdev (/dev/input/eventX) ──► PTY write                         │
│       ↑                                                           │
│  GPIO 21 (pin 40) LOW = grab keyboard exclusively                 │
│                   HIGH = ungrab (X11/desktop gets keys back)      │
└──────────────────────────────────────────────────────────────────┘
```

- **pyte** models the VT100/ANSI terminal state (colors, cursor, scrolling)
- **pygame (offscreen)** renders each cell to an in-memory surface — no framebuffer driver
- **luma.lcd** converts the PIL image to RGB565 and pushes it to the ILI9486 over SPI
- **evdev grab** exclusively captures keyboard at `/dev/input` level; X11 gets events back when pin 40 is released

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Black screen on LCD | Run `ls /dev/spidev0.*` — if missing, SPI not enabled; reboot after install |
| Display noise/garbage | Check wiring (MOSI/MISO/SCLK/CS/DC/RST pins); try lower `spi_speed_hz` in config |
| `luma.lcd` import error | `pip3 install luma.lcd` |
| `pygame` import error | `pip3 install pygame` |
| `pyte` import error | `pip3 install pyte` |
| `RPi.GPIO` error | `pip3 install RPi.GPIO` |
| Keyboard not detected | `ls /dev/input/event*`; ensure keyboard connected before service starts |
| Wrong orientation | Edit `rotation` in config → `cyberdeck-term restart` |
| Text too small/large | Edit `font_size` in config → restart |
| Stats not showing | `python3 /opt/cyberdeck-term/sysinfo.py` — check psutil errors |
| Slow refresh | Lower `fps` in config or reduce `font_size` (fewer pixels to transfer) |

---

## Dependencies

| Package | Purpose |
|---|---|
| `luma.lcd` | ILI9486 SPI display driver |
| `pygame` | Offscreen terminal rendering (no display mode needed) |
| `Pillow` | Surface → PIL image conversion for luma |
| `pyte` | VT100/ANSI terminal model |
| `evdev` | Raw keyboard input + exclusive grab |
| `RPi.GPIO` | GPIO pin polling (kill-switch) |
| `psutil` | System stats (CPU, RAM, disk, temp) |

sudo systemctl status cyberdeck-term && sudo journalctl -u cyberdeck-term -n 30 --no-pager && ls -l /dev/spi*
