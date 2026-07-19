#!/usr/bin/env python3
"""
sysinfo.py — Cyberdeck startup system info display.
Prints a colorful dashboard of CPU, RAM, disk and temp stats to stdout,
then exits so the user lands at a normal bash prompt.
"""

import os, time, platform, socket, subprocess
try:
    import psutil
except ImportError:
    print("\033[31mpsutil not installed. Run: pip3 install psutil\033[0m")
    raise SystemExit(0)

# ── ANSI helpers ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

def fg(r, g, b): return f"\033[38;2;{r};{g};{b}m"
def bg(r, g, b): return f"\033[48;2;{r};{g};{b}m"

C_CYAN    = fg(80,  220, 200)
C_GREEN   = fg(80,  220, 100)
C_YELLOW  = fg(255, 210, 80)
C_RED     = fg(255, 90,  90)
C_BLUE    = fg(100, 160, 255)
C_MAGENTA = fg(210, 100, 255)
C_WHITE   = fg(240, 240, 240)
C_GRAY    = fg(140, 140, 140)
C_ORANGE  = fg(255, 160, 60)

LINE_W = 58   # fits 60-col terminal with 1-char margins

def bar(pct, width=24, full_col=C_GREEN, empty_col=C_GRAY):
    filled = int(round(pct / 100.0 * width))
    filled = max(0, min(width, filled))
    if pct > 80:
        full_col = C_RED
    elif pct > 60:
        full_col = C_YELLOW
    return (full_col + "█" * filled +
            empty_col + "░" * (width - filled) + RESET)

def hline(char="─", color=C_CYAN):
    return color + char * LINE_W + RESET

def label(key, val, key_col=C_CYAN, val_col=C_WHITE):
    return f"  {key_col}{BOLD}{key:<12}{RESET}  {val_col}{val}{RESET}"

# ── Gather stats ──────────────────────────────────────────────────────────────
def cpu_temp():
    """Read RPi CPU temp in °C."""
    try:
        t = psutil.sensors_temperatures()
        for k in ('cpu_thermal', 'coretemp', 'cpu-thermal'):
            if k in t and t[k]:
                return t[k][0].current
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None

def get_cpu_info():
    """Get CPU model string."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Model name") or line.startswith("Hardware"):
                    return line.split(":")[1].strip()
    except Exception:
        pass
    return platform.processor() or "Unknown"

def get_uptime():
    boot = psutil.boot_time()
    secs = int(time.time() - boot)
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "No network"

def main():
    cpu_pct  = psutil.cpu_percent(interval=0.5)
    cpu_freq = psutil.cpu_freq()
    freq_str = f"{cpu_freq.current/1000:.2f} GHz" if cpu_freq else "?"
    cores    = psutil.cpu_count(logical=True)
    mem      = psutil.virtual_memory()
    disk     = psutil.disk_usage("/")
    temp     = cpu_temp()
    uptime   = get_uptime()
    hostname = socket.gethostname()
    ip       = get_ip()
    os_name  = f"Raspberry Pi OS  /  {platform.release()}"
    cpu_info = get_cpu_info()

    temp_str = f"{temp:.1f}°C" if temp is not None else "N/A"
    temp_col = C_RED if (temp or 0) > 75 else (C_YELLOW if (temp or 0) > 60 else C_GREEN)

    mem_used_gb  = mem.used  / (1024**3)
    mem_total_gb = mem.total / (1024**3)
    disk_used_gb = disk.used  / (1024**3)
    disk_total_gb = disk.total / (1024**3)

    lines = [
        "",
        hline("═"),
        f"  {C_MAGENTA}{BOLD}{'CYBERDECK':^54}{RESET}",
        f"  {C_GRAY}{'System Information':^54}{RESET}",
        hline("═"),
        "",
        label("Host",    hostname),
        label("OS",      os_name),
        label("Uptime",  uptime),
        label("IP",      ip),
        "",
        hline(),
        "",
        label("CPU",     f"{cpu_info}"),
        label("",        f"{cores} cores @ {freq_str}"),
        f"  {C_CYAN}{BOLD}{'Load':12}{RESET}  {bar(cpu_pct)}  {C_YELLOW}{cpu_pct:5.1f}%{RESET}",
        f"  {C_CYAN}{BOLD}{'Temp':12}{RESET}  {temp_col}{temp_str}{RESET}",
        "",
        hline(),
        "",
        f"  {C_CYAN}{BOLD}{'Memory':12}{RESET}  {bar(mem.percent)}  "
            f"{C_YELLOW}{mem_used_gb:.1f}G{C_GRAY}/{C_WHITE}{mem_total_gb:.1f}G{RESET}",
        f"  {C_CYAN}{BOLD}{'Disk /':12}{RESET}  {bar(disk.percent)}  "
            f"{C_YELLOW}{disk_used_gb:.1f}G{C_GRAY}/{C_WHITE}{disk_total_gb:.1f}G{RESET}",
        "",
        hline("═"),
        f"  {C_GRAY}GPIO kill-switch: physical pin 40 (BCM 21){RESET}",
        hline("═"),
        "",
    ]

    print("\n".join(lines))

if __name__ == "__main__":
    main()
