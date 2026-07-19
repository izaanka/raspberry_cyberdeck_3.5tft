#!/usr/bin/env python3
"""
cyberdeck-term.py — Framebuffer-free terminal for RPi4 + ILI9486 GPIO LCD.

Drives the display directly via SPI (luma.lcd) — no LCD kernel driver needed,
HDMI output is completely unaffected.

• Renders a full-color ANSI/VT100 terminal via pygame (offscreen) → PIL → SPI.
• Physical pin 40 (BCM GPIO 21) shorted to GND → keyboard exclusively grabbed.
• When pin 40 is HIGH → releases grab, keyboard returns to the desktop/X11.
• Startup: custom sysinfo dashboard (CPU / RAM / disk / temp).

Install:  sudo ./install.sh
Config:   /etc/cyberdeck-term/config.ini
Service:  sudo systemctl {start|stop|restart|status} cyberdeck-term
"""

import os, sys, pty, fcntl, struct, termios, threading, time, signal, select
import configparser, logging

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cyberdeck")

# ── Dependency checks ─────────────────────────────────────────────────────────
def need(mod, pkg=None):
    try:
        return __import__(mod)
    except ImportError:
        log.error("Missing library '%s'. Run: pip3 install %s", mod, pkg or mod)
        sys.exit(1)

# pygame runs fully offscreen (no framebuffer driver needed)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_NOMOUSE",     "1")

pygame = need("pygame")
pyte   = need("pyte")
evdev  = need("evdev")
GPIO   = need("RPi.GPIO", "RPi.GPIO")
PIL    = need("PIL",       "Pillow")

from PIL import Image as PILImage
from evdev import InputDevice, ecodes, list_devices

# luma.lcd drives the ILI9486 over SPI without any kernel display driver
try:
    from luma.core.interface.serial import spi as luma_spi
    from luma.lcd.device import ili9486 as luma_ili9486
    LUMA_OK = True
except ImportError:
    log.error("Missing luma.lcd. Run: pip3 install luma.lcd")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────────
CONFIG_PATH = "/etc/cyberdeck-term/config.ini"

def load_cfg():
    cfg = configparser.ConfigParser()
    cfg.read_string("""
[display]
width     = 480
height    = 320
rotation  = 90
font_size = 13
fps       = 20
[spi]
port         = 0
device       = 0
gpio_dc      = 24
gpio_rst     = 25
gpio_bl      = -1
spi_speed_hz = 32000000
[gpio]
kill_switch_pin  = 21
pull_up          = true
poll_interval_ms = 50
[terminal]
shell       = /bin/bash
startup_cmd = python3 /opt/cyberdeck-term/sysinfo.py
[colors]
default_fg = 204,204,204
default_bg = 12,12,12
""")
    cfg.read(CONFIG_PATH)
    return cfg

CFG = load_cfg()

ROTATION  = CFG.getint("display", "rotation")
FONT_SIZE = CFG.getint("display", "font_size")
FPS       = CFG.getint("display", "fps")

# Render dimensions depend on rotation
if ROTATION in (0, 180):
    RENDER_W = CFG.getint("display", "width")
    RENDER_H = CFG.getint("display", "height")
else:  # 90 / 270: landscape
    RENDER_W = max(CFG.getint("display", "width"),  CFG.getint("display", "height"))
    RENDER_H = min(CFG.getint("display", "width"),  CFG.getint("display", "height"))

SPI_PORT     = CFG.getint("spi", "port")
SPI_DEVICE   = CFG.getint("spi", "device")
GPIO_DC      = CFG.getint("spi", "gpio_dc")
GPIO_RST     = CFG.getint("spi", "gpio_rst")
GPIO_BL      = CFG.getint("spi", "gpio_bl")
SPI_SPEED    = CFG.getint("spi", "spi_speed_hz")

GPIO_PIN     = CFG.getint("gpio", "kill_switch_pin")
POLL_MS      = CFG.getint("gpio", "poll_interval_ms")
PULL_UP      = CFG.getboolean("gpio", "pull_up")

SHELL        = CFG.get("terminal", "shell")
STARTUP_CMD  = CFG.get("terminal", "startup_cmd").strip()

DEFAULT_FG   = tuple(int(x) for x in CFG.get("colors", "default_fg").split(","))
DEFAULT_BG   = tuple(int(x) for x in CFG.get("colors", "default_bg").split(","))

FONT_PATH      = "/opt/cyberdeck-term/fonts/DejaVuSansMono.ttf"
FONT_BOLD_PATH = "/opt/cyberdeck-term/fonts/DejaVuSansMono-Bold.ttf"

# ── 256-Color Palette ─────────────────────────────────────────────────────────
_NAMED = {
    "black":          (0,   0,   0),
    "red":            (197, 15,  31),
    "green":          (19,  161, 14),
    "brown":          (136, 104, 21),
    "blue":           (0,   55,  218),
    "magenta":        (136, 23,  152),
    "cyan":           (58,  150, 221),
    "white":          (204, 204, 204),
    "bright_black":   (118, 118, 118),
    "bright_red":     (231, 72,  86),
    "bright_green":   (22,  198, 12),
    "bright_yellow":  (249, 241, 165),
    "bright_blue":    (59,  120, 255),
    "bright_magenta": (180, 0,   158),
    "bright_cyan":    (97,  214, 214),
    "bright_white":   (242, 242, 242),
}

def _build_palette():
    p = list(_NAMED.values())
    for r in range(6):
        for g in range(6):
            for b in range(6):
                p.append((0 if r == 0 else 55 + r*40,
                           0 if g == 0 else 55 + g*40,
                           0 if b == 0 else 55 + b*40))
    for i in range(24):
        v = 8 + i*10; p.append((v, v, v))
    return p

_PAL256 = _build_palette()

def resolve_color(c, is_fg=True):
    default = DEFAULT_FG if is_fg else DEFAULT_BG
    if c == "default": return default
    if isinstance(c, str):
        if c in _NAMED: return _NAMED[c]
        try:
            idx = int(c)
            return _PAL256[idx] if 0 <= idx < 256 else default
        except ValueError:
            pass
    if isinstance(c, int) and 0 <= c < 256:
        return _PAL256[c]
    if isinstance(c, (list, tuple)) and len(c) == 3:
        return tuple(int(x) for x in c)
    return default

# ── Key mapping ───────────────────────────────────────────────────────────────
_SPECIAL = {
    ecodes.KEY_UP:        b"\x1b[A",   ecodes.KEY_DOWN:     b"\x1b[B",
    ecodes.KEY_RIGHT:     b"\x1b[C",   ecodes.KEY_LEFT:     b"\x1b[D",
    ecodes.KEY_HOME:      b"\x1b[H",   ecodes.KEY_END:      b"\x1b[F",
    ecodes.KEY_PAGEUP:    b"\x1b[5~",  ecodes.KEY_PAGEDOWN: b"\x1b[6~",
    ecodes.KEY_DELETE:    b"\x1b[3~",  ecodes.KEY_INSERT:   b"\x1b[2~",
    ecodes.KEY_F1:        b"\x1bOP",   ecodes.KEY_F2:       b"\x1bOQ",
    ecodes.KEY_F3:        b"\x1bOR",   ecodes.KEY_F4:       b"\x1bOS",
    ecodes.KEY_F5:        b"\x1b[15~", ecodes.KEY_F6:       b"\x1b[17~",
    ecodes.KEY_F7:        b"\x1b[18~", ecodes.KEY_F8:       b"\x1b[19~",
    ecodes.KEY_F9:        b"\x1b[20~", ecodes.KEY_F10:      b"\x1b[21~",
    ecodes.KEY_F11:       b"\x1b[23~", ecodes.KEY_F12:      b"\x1b[24~",
    ecodes.KEY_BACKSPACE: b"\x7f",     ecodes.KEY_TAB:      b"\t",
    ecodes.KEY_ENTER:     b"\r",       ecodes.KEY_ESC:      b"\x1b",
    ecodes.KEY_SPACE:     b" ",
}

_CHAR_MAP = {
    ecodes.KEY_GRAVE:      ("`", "~"),  ecodes.KEY_1: ("1","!"),
    ecodes.KEY_2:          ("2","@"),   ecodes.KEY_3: ("3","#"),
    ecodes.KEY_4:          ("4","$"),   ecodes.KEY_5: ("5","%"),
    ecodes.KEY_6:          ("6","^"),   ecodes.KEY_7: ("7","&"),
    ecodes.KEY_8:          ("8","*"),   ecodes.KEY_9: ("9","("),
    ecodes.KEY_0:          ("0",")"),   ecodes.KEY_MINUS: ("-","_"),
    ecodes.KEY_EQUAL:      ("=","+"),   ecodes.KEY_LEFTBRACE:  ("[","{"),
    ecodes.KEY_RIGHTBRACE: ("]","}"),   ecodes.KEY_BACKSLASH:  ("\\","|"),
    ecodes.KEY_SEMICOLON:  (";",":"),   ecodes.KEY_APOSTROPHE: ("'",'"'),
    ecodes.KEY_COMMA:      (",","<"),   ecodes.KEY_DOT:        (".",">" ),
    ecodes.KEY_SLASH:      ("/","?"),
}
for _i, _k in enumerate([
    ecodes.KEY_A, ecodes.KEY_B, ecodes.KEY_C, ecodes.KEY_D, ecodes.KEY_E,
    ecodes.KEY_F, ecodes.KEY_G, ecodes.KEY_H, ecodes.KEY_I, ecodes.KEY_J,
    ecodes.KEY_K, ecodes.KEY_L, ecodes.KEY_M, ecodes.KEY_N, ecodes.KEY_O,
    ecodes.KEY_P, ecodes.KEY_Q, ecodes.KEY_R, ecodes.KEY_S, ecodes.KEY_T,
    ecodes.KEY_U, ecodes.KEY_V, ecodes.KEY_W, ecodes.KEY_X, ecodes.KEY_Y,
    ecodes.KEY_Z,
]):
    ch = chr(ord("a") + _i)
    _CHAR_MAP[_k] = (ch, ch.upper())

def key_to_bytes(code, shift, ctrl, alt):
    if code in _SPECIAL:
        seq = _SPECIAL[code]
        return (b"\x1b" + seq) if alt else seq
    if code in _CHAR_MAP:
        normal, shifted = _CHAR_MAP[code]
        ch = shifted if shift else normal
        if ctrl and ch.lower() in "abcdefghijklmnopqrstuvwxyz":
            b = bytes([ord(ch.lower()) - ord("a") + 1])
        else:
            b = ch.encode("utf-8", errors="replace")
        return (b"\x1b" + b) if alt else b
    return b""

# ── Terminal model ─────────────────────────────────────────────────────────────
class Terminal:
    def __init__(self, cols, rows):
        self.cols   = cols
        self.rows   = rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.lock   = threading.Lock()
        self.dirty  = True

    def feed(self, data: bytes):
        with self.lock:
            self.stream.feed(data)
            self.dirty = True

    def snapshot(self):
        with self.lock:
            self.dirty = False
            return {
                "buffer": {y: dict(row) for y, row in self.screen.buffer.items()},
                "cursor": (self.screen.cursor.x, self.screen.cursor.y),
                "cursor_hidden": self.screen.cursor.hidden,
            }

# ── PTY ────────────────────────────────────────────────────────────────────────
def set_winsize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0))

def spawn_shell(shell, cols, rows, startup_cmd):
    master, slave = pty.openpty()
    set_winsize(master, cols, rows)
    env = os.environ.copy()
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor",
                "COLUMNS": str(cols), "LINES": str(rows)})
    pid = os.fork()
    if pid == 0:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        for fd in (0, 1, 2):
            os.dup2(slave, fd)
        if slave > 2:
            os.close(slave)
        os.close(master)
        os.execvpe(shell, [shell], env)
        os._exit(1)
    os.close(slave)
    if startup_cmd:
        time.sleep(0.35)
        os.write(master, (startup_cmd + "\n").encode())
    return master, pid

def pty_reader(master_fd, term, stop_evt):
    while not stop_evt.is_set():
        try:
            r, _, _ = select.select([master_fd], [], [], 0.05)
            if r:
                data = os.read(master_fd, 4096)
                if data:
                    term.feed(data)
        except OSError:
            break

# ── GPIO kill-switch ──────────────────────────────────────────────────────────
class GPIOMonitor:
    def __init__(self, pin, pull_up, poll_ms, on_grab, on_release):
        self.pin        = pin
        self.poll_s     = poll_ms / 1000.0
        self.on_grab    = on_grab
        self.on_release = on_release
        self._grabbed   = False
        self._stop      = threading.Event()
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(pin, GPIO.IN,
                   pull_up_down=GPIO.PUD_UP if pull_up else GPIO.PUD_DOWN)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            low = (GPIO.input(self.pin) == GPIO.LOW)
            if low and not self._grabbed:
                self._grabbed = True
                self.on_grab()
            elif not low and self._grabbed:
                self._grabbed = False
                self.on_release()
            time.sleep(self.poll_s)

    def stop(self):
        self._stop.set()
        GPIO.cleanup()

# ── Keyboard (evdev) ──────────────────────────────────────────────────────────
class KeyboardManager:
    def __init__(self, master_fd):
        self.master_fd = master_fd
        self.active    = False
        self.devices   = []
        self._stop     = threading.Event()
        self._lock     = threading.Lock()
        self._shift = self._ctrl = self._alt = self._caps = False

    def _find_keyboards(self):
        devs = []
        for path in list_devices():
            try:
                d = InputDevice(path)
                caps = d.capabilities()
                if ecodes.EV_KEY in caps and ecodes.KEY_A in caps[ecodes.EV_KEY]:
                    devs.append(d)
                    log.info("Keyboard: %s (%s)", path, d.name)
            except Exception:
                pass
        return devs

    def start(self):
        self.devices = self._find_keyboards()
        threading.Thread(target=self._run, daemon=True).start()

    def grab(self):
        with self._lock:
            self.active = True
            for d in self.devices:
                try: d.grab()
                except Exception as e: log.warning("grab %s: %s", d.path, e)
        log.info("Keyboard GRABBED (pin 40 grounded)")

    def ungrab(self):
        with self._lock:
            self.active = False
            for d in self.devices:
                try: d.ungrab()
                except Exception: pass
        log.info("Keyboard RELEASED (pin 40 floating)")

    def _run(self):
        import selectors
        _SHIFT = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
        _CTRL  = {ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL}
        _ALT   = {ecodes.KEY_LEFTALT,   ecodes.KEY_RIGHTALT}
        sel = selectors.DefaultSelector()
        for d in self.devices:
            sel.register(d, selectors.EVENT_READ)
        while not self._stop.is_set():
            for key, _ in sel.select(timeout=0.1):
                try:
                    for ev in key.fileobj.read():
                        if ev.type != ecodes.EV_KEY: continue
                        code, val = ev.code, ev.value
                        if code in _SHIFT:   self._shift = (val != 0)
                        elif code in _CTRL:  self._ctrl  = (val != 0)
                        elif code in _ALT:   self._alt   = (val != 0)
                        elif code == ecodes.KEY_CAPSLOCK and val == 1:
                            self._caps = not self._caps
                        if self.active and val in (1, 2):
                            seq = key_to_bytes(code,
                                               self._shift ^ self._caps,
                                               self._ctrl, self._alt)
                            if seq:
                                os.write(self.master_fd, seq)
                except Exception:
                    pass

    def stop(self):
        self._stop.set()
        try: self.ungrab()
        except Exception: pass

# ── Display: luma.lcd → ILI9486 via SPI ───────────────────────────────────────
def init_luma_display():
    """Initialize luma.lcd ILI9486 over SPI (no kernel framebuffer driver)."""
    # luma rotate: 0=0°, 1=90°, 2=180°, 3=270°
    luma_rotate = (ROTATION // 90) % 4

    bl_pin = GPIO_BL if GPIO_BL >= 0 else None
    serial = luma_spi(
        port=SPI_PORT,
        device=SPI_DEVICE,
        gpio_DC=GPIO_DC,
        gpio_RST=GPIO_RST,
        bus_speed_hz=SPI_SPEED,
        gpio_LIGHT=bl_pin,
    )
    # width/height here are the PHYSICAL panel dimensions (portrait native)
    dev = luma_ili9486(serial, width=320, height=480,
                       rotate=luma_rotate, mode="RGB")
    log.info("ILI9486 display initialized via SPI @ %dHz  rotate=%d°",
             SPI_SPEED, ROTATION)
    return dev

# ── pygame offscreen font init ────────────────────────────────────────────────
def init_pygame_fonts():
    pygame.display.init()
    pygame.font.init()

    from pathlib import Path
    for path in [FONT_PATH,
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]:
        if Path(path).exists():
            bold_path = path.replace("DejaVuSansMono.ttf",
                                     "DejaVuSansMono-Bold.ttf")
            fn = pygame.font.Font(path, FONT_SIZE)
            fb = pygame.font.Font(
                bold_path if Path(bold_path).exists() else path, FONT_SIZE)
            log.info("Font: %s @ %dpx", path, FONT_SIZE)
            return fn, fb

    log.warning("DejaVu font not found; falling back to pygame mono")
    f = pygame.font.SysFont("monospace", FONT_SIZE)
    return f, f

def compute_grid(font):
    cw, ch = font.size("M")
    cols = RENDER_W // cw
    rows = RENDER_H // ch
    return cols, rows, cw, ch

# ── Render ─────────────────────────────────────────────────────────────────────
def render_to_surface(surf, snap, fn, fb, cols, rows, cw, ch):
    buf    = snap["buffer"]
    cx, cy = snap["cursor"]
    c_vis  = not snap["cursor_hidden"]

    surf.fill(DEFAULT_BG)

    for y in range(rows):
        row = buf.get(y, {})
        for x in range(cols):
            obj = row.get(x)
            if obj is None: continue

            ch_str  = obj.data or " "
            bold    = getattr(obj, "bold",    False)
            reverse = getattr(obj, "reverse", False)
            fg_col  = resolve_color(getattr(obj, "fg", "default"), True)
            bg_col  = resolve_color(getattr(obj, "bg", "default"), False)

            if reverse:
                fg_col, bg_col = bg_col, fg_col

            cell_rect = pygame.Rect(x * cw, y * ch, cw, ch)
            surf.fill(bg_col, cell_rect)

            font = fb if bold else fn
            try:
                glyph = font.render(ch_str, True, fg_col)
            except Exception:
                glyph = font.render("?", True, fg_col)
            surf.blit(glyph, (x * cw, y * ch))

    # Block cursor
    if c_vis and 0 <= cx < cols and 0 <= cy < rows:
        pygame.draw.rect(surf, (220, 220, 220),
                         pygame.Rect(cx * cw, cy * ch, cw, ch), 2)

def surface_to_pil(surf):
    """Convert pygame Surface → PIL Image (RGB)."""
    raw = pygame.image.tostring(surf, "RGB")
    return PILImage.frombytes("RGB", (RENDER_W, RENDER_H), raw)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    stop_evt = threading.Event()

    # Display
    device = init_luma_display()

    # Fonts (offscreen pygame)
    fn, fb = init_pygame_fonts()
    cols, rows, cw, ch = compute_grid(fn)
    log.info("Grid: %d cols × %d rows  (cell %d×%d px)", cols, rows, cw, ch)

    # Render surface (offscreen)
    render_surf = pygame.Surface((RENDER_W, RENDER_H))

    # Terminal model
    term = Terminal(cols, rows)

    # PTY / shell
    master_fd, child_pid = spawn_shell(SHELL, cols, rows, STARTUP_CMD)

    # PTY reader thread
    reader_t = threading.Thread(
        target=pty_reader, args=(master_fd, term, stop_evt), daemon=True)
    reader_t.start()

    # Keyboard
    kb = KeyboardManager(master_fd)
    kb.start()

    # GPIO monitor
    gpio = GPIOMonitor(
        pin=GPIO_PIN, pull_up=PULL_UP, poll_ms=POLL_MS,
        on_grab=kb.grab, on_release=kb.ungrab,
    )
    gpio.start()

    # Signals
    signal.signal(signal.SIGTERM, lambda *_: stop_evt.set())
    signal.signal(signal.SIGINT,  lambda *_: stop_evt.set())

    # Render loop
    import time as _time
    frame_time = 1.0 / FPS
    snap = term.snapshot()

    try:
        while not stop_evt.is_set():
            t0 = _time.monotonic()

            # Watchdog: respawn shell if it exits
            try:
                wpid, _ = os.waitpid(child_pid, os.WNOHANG)
                if wpid == child_pid:
                    log.info("Shell exited — restarting in 2s")
                    _time.sleep(2)
                    master_fd, child_pid = spawn_shell(
                        SHELL, cols, rows, STARTUP_CMD)
            except ChildProcessError:
                pass

            # Only re-render + send SPI when screen has changed
            if term.dirty:
                snap = term.snapshot()
                render_to_surface(render_surf, snap, fn, fb, cols, rows, cw, ch)
                pil_img = surface_to_pil(render_surf)
                try:
                    device.display(pil_img)
                except Exception as e:
                    log.error("Display error: %s", e)

            # Sleep for remainder of frame budget
            elapsed = _time.monotonic() - t0
            sleep_t  = frame_time - elapsed
            if sleep_t > 0:
                _time.sleep(sleep_t)

    finally:
        stop_evt.set()
        kb.stop()
        gpio.stop()
        pygame.quit()
        try:
            os.kill(child_pid, signal.SIGTERM)
            os.waitpid(child_pid, 0)
        except Exception:
            pass
        try: os.close(master_fd)
        except Exception: pass
        log.info("Goodbye.")

if __name__ == "__main__":
    main()
