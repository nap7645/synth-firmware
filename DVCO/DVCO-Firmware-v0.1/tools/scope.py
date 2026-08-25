#!/usr/bin/env python3
"""
scope.py — live terminal oscilloscope for the VCO firmware.

Reads waveform frames from the NUCLEO-G431KB over the ST-LINK virtual COM
port and draws them as ASCII in your terminal. Also lets you retune the
oscillator interactively, so you can watch the waveform change as you type.

    pip install pyserial
    python3 tools/scope.py                 # auto-detect the ST-LINK port
    python3 tools/scope.py -p COM7         # or name it explicitly
    python3 tools/scope.py --raw           # dump lines, no rendering
    python3 tools/scope.py --csv out.csv   # log frames to CSV as well

Keys while running:
    1 2 3 4   sine / saw / square / triangle
    up/down   frequency +/- one semitone       left/right   +/- 10 Hz
    a / z     amplitude +/- 5 %
    space     freeze the display               q            quit

Protocol (firmware -> host), one per line:
    W <n> <s0> <s1> ... <sn-1>      waveform frame, samples 0..999
    S <freq> <waveidx> <name> <amp> <samplerate> <outmax>
    # <text>                        log line
"""
import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial missing. Install it with:  pip install pyserial")

# ---- terminal helpers ------------------------------------------------------
CSI = "\x1b["
CLEAR, HOME, HIDE, SHOW = CSI + "2J", CSI + "H", CSI + "?25l", CSI + "?25h"
DIM, BOLD, RESET = CSI + "2m", CSI + "1m", CSI + "0m"
CYAN, YELLOW, GREY = CSI + "36m", CSI + "33m", CSI + "90m"

WAVES = ["sine", "saw", "square", "tri"]


def find_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    for p in ports:
        blob = " ".join(str(x) for x in (p.description, p.manufacturer, p.product)).lower()
        if "st-link" in blob or "stlink" in blob or "stm32" in blob:
            return p.device
    if not ports:
        sys.exit("No serial ports found. Is the Nucleo plugged in?")
    print("Could not identify an ST-LINK port. Available:")
    for p in ports:
        print(f"  {p.device}  {p.description}")
    sys.exit("Re-run with  -p <port>")


def render(samples, state, out_max, width, height, frozen, fps):
    """Draw one frame of samples as an ASCII plot."""
    if not samples:
        return ""

    # Resample the frame to the terminal width (nearest-neighbour is fine —
    # we are showing shape, not doing measurement).
    n = len(samples)
    cols = [samples[min(n - 1, i * n // width)] for i in range(width)]

    # Map sample value -> row. Row 0 is the top of the plot.
    rows = [[" "] * width for _ in range(height)]
    prev_r = None
    for x, v in enumerate(cols):
        r = int((out_max - v) * (height - 1) / max(1, out_max))
        r = max(0, min(height - 1, r))
        # Join to the previous column with verticals so steep edges (square,
        # saw reset) read as edges instead of disconnected dots.
        if prev_r is not None and abs(r - prev_r) > 1:
            step = 1 if r > prev_r else -1
            for rr in range(prev_r + step, r, step):
                rows[rr][x] = "|"
        rows[r][x] = "*"
        prev_r = r

    mid = height // 2
    out = []
    for i, row in enumerate(rows):
        line = "".join(row)
        if i == mid:
            # midline shows through wherever the trace is not drawn
            line = "".join(c if c != " " else f"{GREY}-{RESET}" for c in line)
        out.append(f"{CYAN}{line}{RESET}" if i != mid else line)

    vmin, vmax = min(samples), max(samples)
    pkpk = vmax - vmin
    duty = sum(samples) / len(samples) / out_max * 100.0

    hdr = (f"{BOLD}VCO scope{RESET}  "
           f"{YELLOW}{state.get('freq','?')} Hz{RESET}  "
           f"{YELLOW}{state.get('wave','?')}{RESET}  "
           f"amp {state.get('amp','?')}%")
    if frozen:
        hdr += f"  {BOLD}[FROZEN]{RESET}"
    sub = (f"{DIM}min {vmin}  max {vmax}  pk-pk {pkpk}  "
           f"mean-duty {duty:.1f}%  {len(samples)} samples @ "
           f"{state.get('sr','?')} Hz  {fps:.0f} fps{RESET}")
    keys = f"{DIM}1-4 wave   arrows freq   a/z amp   space freeze   q quit{RESET}"

    return "\n".join([hdr, sub, ""] + out + ["", keys])


# ---- non-blocking keyboard -------------------------------------------------
class Keys:
    """Raw-mode single-keypress reader. Falls back to no-op if stdin isn't a tty."""

    def __init__(self):
        self.enabled = sys.stdin.isatty()
        self.win = sys.platform == "win32"
        self._restore = None

    def __enter__(self):
        if self.enabled and not self.win:
            import termios, tty
            self._fd = sys.stdin.fileno()
            self._restore = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *a):
        if self._restore is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._restore)

    def get(self):
        """Return a key name or None. Arrow keys come back as 'up'/'down'/etc."""
        if not self.enabled:
            return None
        if self.win:
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch())
            return ch
        import select
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            if select.select([sys.stdin], [], [], 0.01)[0]:
                seq = sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[D": "left", "[C": "right"}.get(seq)
            return "esc"
        return ch


def main():
    ap = argparse.ArgumentParser(description="Live terminal scope for the VCO firmware.")
    ap.add_argument("-p", "--port", help="serial port (auto-detected if omitted)")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("--raw", action="store_true", help="print raw lines, no plot")
    ap.add_argument("--csv", help="also append each frame to this CSV file")
    ap.add_argument("--height", type=int, default=22, help="plot rows")
    ap.add_argument("--width", type=int, default=0, help="plot columns (0 = fit terminal)")
    args = ap.parse_args()

    port = find_port(args.port)
    try:
        ser = serial.Serial(port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        sys.exit(f"Could not open {port}: {e}\n"
                 "If a serial terminal is already attached to it, close that first.")

    print(f"Connected to {port} at {args.baud}. Ctrl-C to quit.")
    time.sleep(0.2)
    ser.reset_input_buffer()
    ser.write(b"?\n")

    state = {"freq": "?", "wave": "?", "amp": "?", "sr": "?"}
    out_max, freq, amp, wave_idx = 999, 440, 100, 0
    samples, frozen, last_draw = [], False, 0.0
    frames, fps, fps_t0 = 0, 0.0, time.time()
    csv_f = open(args.csv, "a") if args.csv else None
    buf = b""

    def send(cmd):
        ser.write((cmd + "\n").encode())

    try:
        with Keys() as keys:
            if not args.raw:
                sys.stdout.write(HIDE + CLEAR)
            while True:
                # --- keyboard ---
                k = keys.get()
                if k:
                    if k == "q":
                        break
                    elif k == " ":
                        frozen = not frozen
                    elif k in "1234":
                        wave_idx = int(k) - 1
                        send(f"w{wave_idx}")
                    elif k == "up":
                        freq = min(20000, int(round(freq * 2 ** (1 / 12))))
                        send(f"f{freq}")
                    elif k == "down":
                        freq = max(1, int(round(freq / 2 ** (1 / 12))))
                        send(f"f{freq}")
                    elif k == "right":
                        freq = min(20000, freq + 10); send(f"f{freq}")
                    elif k == "left":
                        freq = max(1, freq - 10); send(f"f{freq}")
                    elif k == "a":
                        amp = min(100, amp + 5); send(f"a{amp}")
                    elif k == "z":
                        amp = max(0, amp - 5); send(f"a{amp}")

                # --- serial ---
                chunk = ser.read(4096)
                if chunk:
                    buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("ascii", "replace").strip()
                    if not line:
                        continue
                    if args.raw:
                        print(line)
                        continue
                    if line[0] == "W":
                        parts = line.split()
                        try:
                            vals = [int(x) for x in parts[2:]]
                        except ValueError:
                            continue
                        if vals and not frozen:
                            samples = vals
                            frames += 1
                            if csv_f:
                                csv_f.write(",".join(map(str, vals)) + "\n")
                    elif line[0] == "S":
                        f = line.split()
                        if len(f) >= 7:
                            state.update(freq=f[1], wave=f[3], amp=f[4], sr=f[5])
                            try:
                                out_max = int(f[6]); freq = int(f[1])
                                amp = int(f[4]); wave_idx = int(f[2])
                            except ValueError:
                                pass
                    elif line[0] == "#" and args.raw:
                        print(GREY + line + RESET)

                # --- draw ---
                now = time.time()
                if not args.raw and samples and now - last_draw > 1 / 30:
                    last_draw = now
                    if now - fps_t0 >= 1.0:
                        fps = frames / (now - fps_t0)
                        frames, fps_t0 = 0, now
                    width = args.width or max(40, (_term_cols() or 100) - 2)
                    sys.stdout.write(HOME + CLEAR + HOME)
                    sys.stdout.write(render(samples, state, out_max, width,
                                            args.height, frozen, fps))
                    sys.stdout.flush()

                if not chunk and not k:
                    time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        if not args.raw:
            sys.stdout.write(SHOW + RESET + "\n")
        if csv_f:
            csv_f.close()
        ser.close()


def _term_cols():
    try:
        import shutil
        return shutil.get_terminal_size().columns
    except Exception:
        return None


if __name__ == "__main__":
    main()
