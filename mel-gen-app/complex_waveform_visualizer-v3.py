"""
Complex Waveform Visualizer
============================
Four waveform generators → sum → optional quantizer → voltage output.
Melody sequencer (clock mode) or complex oscillator (audio mode).

V/oct standard: 0V = C4 (261.6 Hz), ±1V = ±1 octave, 1/12V = 1 semitone.
Double-click any slider to reset it to its default value.

MIDI Clock sync: enable Sync out on an IAC Bus port in Ableton
  Preferences → Link, Tempo & MIDI → Output Ports → IAC Driver → Sync ON
  The script auto-detects clock and locks BPM to Ableton.
  When no clock is received for >2s it falls back to the internal BPM slider.

S&H playhead model
  - A continuous phase (0.0-1.0) advances through the waveform window in
    real time. At each S&H division boundary the waveform is sampled at the
    current phase position and that value is sent as a MIDI note.
  - Knob changes set a dirty flag; the waveform is recomputed at the very
    next division boundary with no loop-length delay.
  - Play/Pause freezes the phase. Reset snaps phase to 0.

Usage:   python3 complex_waveform_visualizer.py
Requires: numpy, matplotlib, python-rtmidi
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, RadioButtons, CheckButtons, Button
import threading
import time
import collections

try:
    import rtmidi
    RTMIDI_AVAILABLE = True
except ImportError:
    RTMIDI_AVAILABLE = False


# ===============================================================================
#  Constants
# ===============================================================================

RATIO_DIVISIONS = {
    '1/8x': 1/8, '1/6x': 1/6, '1/4x': 1/4, '1/3x': 1/3, '1/2x': 1/2,
    '1x': 1,
    '2x': 2, '3x': 3, '4x': 4, '6x': 6, '8x': 8, '12x': 12, '16x': 16,
}
RATIO_LABELS = list(RATIO_DIVISIONS.keys())
RATIO_VALUES = list(RATIO_DIVISIONS.values())

SHAPES = ['sine', 'triangle', 'square', 'ramp up', 'ramp dn']

SCALES = {
    'chromatic':   [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    'major':       [0, 2, 4, 5, 7, 9, 11],
    'minor':       [0, 2, 3, 5, 7, 8, 10],
    'pent maj':    [0, 2, 4, 7, 9],
    'pent min':    [0, 3, 5, 7, 10],
    'dorian':      [0, 2, 3, 5, 7, 9, 10],
    'mixolydian':  [0, 2, 4, 5, 7, 9, 10],
    'whole tone':  [0, 2, 4, 6, 8, 10],
    # Harmonics 1-16 nearest 12-TET semitone, reduced mod octave, deduplicated
    'harmonic':    [0, 2, 4, 7, 10, 14, 16, 19],
}
SCALE_NAMES  = list(SCALES.keys())
SCALE_VALUES = list(SCALES.values())
NOTE_NAMES   = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# S&H divisions: (label, quarter-note beats per step)
SH_DIVISIONS = [
    ('1/4',   1.0),
    ('1/8',   0.5),
    ('1/16',  0.25),
    ('1/32',  0.125),
    ('1/8T',  1.0 / 3.0),
    ('1/16T', 1.0 / 6.0),
]
SH_DIV_LABELS = [d[0] for d in SH_DIVISIONS]
SH_DIV_BEATS  = [d[1] for d in SH_DIVISIONS]

VOICE_COLORS = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12']
BG_DARK    = '#1a1a2e'
BG_PANEL   = '#16213e'
BG_CTRL    = '#0f1626'
TEXT_CLR   = '#cccccc'
MIDI_COLOR = '#00d4aa'

SAMPLES = 4096

VOICE_DEFAULTS = [
    {'shape': 0, 'ratio': 5, 'amp': 1.0, 'phase': 0},
    {'shape': 2, 'ratio': 5, 'amp': 1.0, 'phase': 0},
    {'shape': 3, 'ratio': 5, 'amp': 1.0, 'phase': 0},
    {'shape': 4, 'ratio': 5, 'amp': 1.0, 'phase': 0},
]

GLOBAL_DEFAULTS = {
    'bpm': 130, 'bars': 4, 'audio_freq': 110,
    'gamp': 1.0, 'gdc': 0.0,
    'qroot': 0, 'qscale': 1, 'qrange': 2,
    'midi_channel': 1, 'midi_gate': 0.5, 'midi_velocity': 100,
    'sh_div': 2,   # index → 1/16 default
}

MIDI_OUT_PORT_NAME = 'Complex Waveform Out'
CLOCK_TIMEOUT_S    = 2.0
PPQN               = 24


# ===============================================================================
#  Waveform Engine
# ===============================================================================

def generate_waveform(shape_name, phase_deg, t_cycle):
    t = (t_cycle + phase_deg / 360.0) % 1.0
    if shape_name == 'sine':
        return np.sin(2 * np.pi * t)
    elif shape_name == 'triangle':
        return 2.0 * np.abs(2.0 * (t - np.floor(t + 0.5))) - 1.0
    elif shape_name == 'square':
        return np.where(t < 0.5, 1.0, -1.0)
    elif shape_name == 'ramp up':
        return 2.0 * t - 1.0
    elif shape_name == 'ramp dn':
        return 1.0 - 2.0 * t
    return np.zeros_like(t)


def generate_voice(shape_name, ratio_value, amplitude_v, phase_deg,
                   complex_period_s, t_seconds):
    freq_hz = ratio_value / complex_period_s
    if freq_hz <= 0:
        return np.zeros_like(t_seconds)
    t_cycle = (t_seconds * freq_hz) % 1.0
    return amplitude_v * generate_waveform(shape_name, phase_deg, t_cycle)


def quantize_to_scale(voltage, root_note, scale_degrees, octave_range):
    v_min, v_max = np.min(voltage), np.max(voltage)
    if v_max - v_min < 1e-9:
        return np.zeros_like(voltage)
    half       = octave_range / 2.0
    normalized = (voltage - v_min) / (v_max - v_min) * octave_range - half
    valid_voct = []
    for octave in range(-octave_range, octave_range + 1):
        for d in scale_degrees:
            valid_voct.append(octave + d / 12.0)
    valid_voct = np.array(sorted(set(valid_voct)))
    valid_voct = valid_voct[(valid_voct >= -half - 0.1) & (valid_voct <= half + 0.1)]
    if len(valid_voct) == 0:
        return np.zeros_like(voltage)
    idx   = np.searchsorted(valid_voct, normalized, side='left')
    idx   = np.clip(idx, 0, len(valid_voct) - 1)
    idx_l = np.clip(idx - 1, 0, len(valid_voct) - 1)
    best  = np.where(
        np.abs(normalized - valid_voct[idx_l]) < np.abs(normalized - valid_voct[idx]),
        idx_l, idx
    )
    return valid_voct[best]


def build_voct_array(complex_s):
    """Compute and return the full voct_seq array (length SAMPLES).
    Called inside the engine thread whenever the dirty flag is set.
    """
    t = np.linspace(0, complex_s, SAMPLES, endpoint=False)
    active_wf = []
    for i in range(4):
        vs = voice_sliders[i]
        st = voice_states[i]
        if st.on:
            wf = generate_voice(
                shape_name=SHAPES[int(vs['shape'].val)],
                ratio_value=RATIO_VALUES[int(vs['ratio'].val)],
                amplitude_v=vs['amp'].val,
                phase_deg=vs['phase'].val,
                complex_period_s=complex_s,
                t_seconds=t,
            )
            active_wf.append(wf)
    if not active_wf:
        return np.zeros(SAMPLES)
    combined = sl_gamp.val * np.sum(active_wf, axis=0) + sl_gdc.val
    if quant_enabled:
        return quantize_to_scale(
            combined,
            root_note=int(sl_qroot.val),
            scale_degrees=SCALE_VALUES[int(sl_qscale.val)],
            octave_range=int(sl_qrange.val),
        )
    else:
        return quantize_to_scale(
            combined, root_note=0,
            scale_degrees=SCALES['chromatic'],
            octave_range=int(sl_qrange.val),
        )


# ===============================================================================
#  State
# ===============================================================================

class VoiceState:
    def __init__(self, on=True, show=True):
        self.on   = on
        self.show = show

voice_states    = [VoiceState() for _ in range(4)]
quant_enabled   = False
slider_defaults = {}


# ===============================================================================
#  MIDI Clock Receiver
# ===============================================================================

class ClockReceiver:
    """Listens on a virtual MIDI input port for MIDI clock ticks (0xF8).

    Maintains a rolling BPM estimate from the last PPQN tick intervals
    (one quarter note of averaging). Returns None if clock has timed out.
    """

    def __init__(self):
        self._midiin     = None
        self._lock       = threading.Lock()
        self._tick_times = collections.deque(maxlen=PPQN)
        self._last_tick  = None
        self._bpm_est    = None
        self.synced      = False

    def start(self):
        if not RTMIDI_AVAILABLE:
            return
        try:
            self._midiin = rtmidi.MidiIn()
            self._midiin.ignore_types(sysex=True, timing=False, active_sense=True)
            self._midiin.open_virtual_port('CWV Clock In')
            self._midiin.set_callback(self._on_message)
        except Exception:
            self._midiin = None

    def stop(self):
        if self._midiin is not None:
            try:
                self._midiin.cancel_callback()
                self._midiin.close_port()
            except Exception:
                pass
            del self._midiin
            self._midiin = None
        self.synced   = False
        self._bpm_est = None

    def _on_message(self, message, data=None):
        msg, _ = message
        status = msg[0]
        now    = time.perf_counter()
        if status == 0xF8:  # clock tick
            with self._lock:
                if self._last_tick is not None:
                    interval = now - self._last_tick
                    if 0.001 < interval < 0.5:
                        self._tick_times.append(interval)
                self._last_tick = now
                if len(self._tick_times) >= 4:
                    avg = sum(self._tick_times) / len(self._tick_times)
                    self._bpm_est = 60.0 / (avg * PPQN)
                    self.synced   = True

    @property
    def bpm(self):
        """Current synced BPM, or None if no clock / timed out."""
        with self._lock:
            if self._last_tick is None:
                return None
            if time.perf_counter() - self._last_tick > CLOCK_TIMEOUT_S:
                self.synced = False
                return None
            return self._bpm_est


clock_rx = ClockReceiver()


# ===============================================================================
#  MIDI Engine  -  S&H playhead
# ===============================================================================

class MidiEngine:
    """Continuous S&H sequencer.

    Architecture
    ------------
    A background thread maintains a phase float [0, 1) that advances through
    the waveform window in real time. At each S&H step boundary it samples
    the cached voct array at the current phase index and sends a MIDI note.

    Low-latency updates
    -------------------
    Every waveform slider callback calls mark_dirty(). The engine thread checks
    the dirty flag at the TOP of every step — before sleeping — and rebuilds
    the voct cache immediately if set. This means parameter changes take effect
    within one S&H step duration (e.g. 115ms at 130 BPM, 1/16 division),
    never a full loop length.

    Sleep is broken into 5ms slices so stop/pause signals are responsive.
    """

    def __init__(self):
        self._thread     = None
        self._stop_evt   = threading.Event()
        self._midiout    = None
        self._last_note  = None
        self._last_ch    = 0
        self.active      = False
        self.playing     = False
        self.status      = 'MIDI off'

        self._phase      = 0.0
        self._phase_lock = threading.Lock()

        # dirty flag: engine rebuilds voct cache at next step boundary
        self._dirty      = threading.Event()
        self._dirty.set()

        self._voct_cache  = None
        self._cache_lock  = threading.Lock()
        self._complex_s   = 1.0

    # -- Port ------------------------------------------------------------------

    def _open_port(self):
        if not RTMIDI_AVAILABLE:
            self.status = 'rtmidi not installed'
            return False
        try:
            self._midiout = rtmidi.MidiOut()
            self._midiout.open_virtual_port(MIDI_OUT_PORT_NAME)
            self.status = f'Port: {MIDI_OUT_PORT_NAME}'
            return True
        except Exception as e:
            self.status = f'Error: {e}'
            self._midiout = None
            return False

    def _close_port(self):
        if self._midiout is not None:
            try:
                self._midiout.close_port()
            except Exception:
                pass
            del self._midiout
            self._midiout = None

    # -- MIDI note helpers -----------------------------------------------------

    def _note_on(self, ch, note, vel):
        if self._midiout:
            self._midiout.send_message([0x90 | ch, note, vel])
            self._last_note = note
            self._last_ch   = ch

    def _note_off(self, ch, note):
        if self._midiout:
            self._midiout.send_message([0x80 | ch, note, 0])

    def _kill_hanging(self):
        if self._last_note is not None:
            self._note_off(self._last_ch, self._last_note)
            self._last_note = None

    @staticmethod
    def voct_to_midi(voct):
        return int(np.clip(round(60.0 + voct * 12.0), 0, 127))

    # -- Cache rebuild (called inside engine thread) ---------------------------

    def _rebuild_cache(self, bpm, bars):
        complex_s = (60.0 / bpm) * bars * 4.0
        try:
            voct_seq = build_voct_array(complex_s)
        except Exception:
            voct_seq  = np.zeros(SAMPLES)
            complex_s = 1.0
        with self._cache_lock:
            self._voct_cache = voct_seq
            self._complex_s  = complex_s
        self._dirty.clear()

    def _sample_at_phase(self, phase):
        with self._cache_lock:
            if self._voct_cache is None:
                return 60
            idx = int(phase * SAMPLES) % SAMPLES
            return self.voct_to_midi(float(self._voct_cache[idx]))

    # -- Engine thread ---------------------------------------------------------

    def _run(self):
        while not self._stop_evt.is_set():

            # Read transport parameters fresh every step
            synced_bpm = clock_rx.bpm
            if synced_bpm is not None:
                bpm = synced_bpm
                try:
                    if abs(sl_bpm.val - bpm) > 0.5:
                        sl_bpm.set_val(round(bpm))
                except Exception:
                    pass
            else:
                bpm = sl_bpm.val

            bars           = int(sl_bars.val)
            div_idx        = int(sl_sh_div.val)
            beats_per_step = SH_DIV_BEATS[div_idx]
            total_beats    = bars * 4.0
            step_dur_s     = (60.0 / bpm) * beats_per_step
            phase_step     = beats_per_step / total_beats

            channel   = int(sl_midi_ch.val) - 1
            velocity  = int(sl_midi_vel.val)
            gate_frac = sl_midi_gate.val

            # Rebuild cache immediately if any knob changed
            if self._dirty.is_set():
                self._rebuild_cache(bpm, bars)

            # Paused: idle cheaply
            if not self.playing:
                time.sleep(0.02)
                continue

            # Sample waveform at current phase
            with self._phase_lock:
                phase = self._phase

            note = self._sample_at_phase(phase)

            # Legato: note-on new pitch, then note-off old only if pitch changed
            if note != self._last_note:
                self._note_on(channel, note, velocity)
                if self._last_note is not None:
                    self._note_off(self._last_ch, self._last_note)
            # Same pitch: leave ringing, no retrigger

            # Gate + rest timing in interruptible slices
            gate_s = step_dur_s * gate_frac
            rest_s = step_dur_s - gate_s

            self._interruptible_sleep(gate_s)
            if self._stop_evt.is_set():
                break

            if rest_s > 0.002:
                self._note_off(channel, note)
                self._last_note = None
                self._interruptible_sleep(rest_s)
                if self._stop_evt.is_set():
                    break

            # Advance phase
            with self._phase_lock:
                self._phase = (self._phase + phase_step) % 1.0

        self._kill_hanging()

    def _interruptible_sleep(self, duration):
        """Sleep in 5ms slices so stop/dirty are noticed quickly."""
        end = time.perf_counter() + duration
        while not self._stop_evt.is_set():
            remaining = end - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(0.005, remaining))

    # -- Public API ------------------------------------------------------------

    def mark_dirty(self):
        """Signal that waveform parameters changed; cache rebuilds next step."""
        self._dirty.set()

    def play_pause(self):
        self.playing = not self.playing

    def reset(self):
        with self._phase_lock:
            self._phase = 0.0
        self._dirty.set()

    @property
    def phase(self):
        with self._phase_lock:
            return self._phase

    def start(self):
        if self.active:
            return
        if not self._open_port():
            _refresh_midi_status()
            return
        self._stop_evt.clear()
        self._dirty.set()
        self.playing = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.active = True
        _refresh_midi_status()

    def stop(self):
        if not self.active:
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._close_port()
        self.active  = False
        self.playing = False
        self.status  = 'MIDI off'
        _refresh_midi_status()


midi_engine = MidiEngine()


# ===============================================================================
#  Layout constants
# ===============================================================================

L_MARGIN = 0.08
L_WIDTH  = 0.24
SL_H     = 0.018
GAP      = 0.003
CHK_H    = 0.024
SECT_GAP = 0.008

P_LEFT  = 0.42
P_RIGHT = 0.97
P_WIDTH = P_RIGHT - P_LEFT


# ===============================================================================
#  Build figure
# ===============================================================================

fig = plt.figure(figsize=(17, 10), facecolor=BG_DARK)
fig.canvas.manager.set_window_title('Complex Waveform Visualizer')

ax_comp = fig.add_axes([P_LEFT, 0.55, P_WIDTH, 0.40], facecolor=BG_PANEL)
ax_comb = fig.add_axes([P_LEFT, 0.07, P_WIDTH, 0.43], facecolor=BG_PANEL)

for ax in [ax_comp, ax_comb]:
    ax.tick_params(colors='#999', labelsize=9)
    ax.spines['bottom'].set_color('#444')
    ax.spines['left'].set_color('#444')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.yaxis.label.set_color(TEXT_CLR)
    ax.xaxis.label.set_color(TEXT_CLR)
    ax.grid(True, alpha=0.12, color='#888')
    ax.axhline(0, color='#555', linewidth=0.5)

ax_comp.set_ylabel('Volts', fontsize=10)
ax_comb.set_ylabel('Volts', fontsize=10)
ax_comb.set_xlabel('Beats', fontsize=10)
plt.setp(ax_comp.get_xticklabels(), visible=False)

voice_lines = []
for i in range(4):
    ln, = ax_comp.plot([], [], color=VOICE_COLORS[i], linewidth=1.2,
                       alpha=0.85, label=f'V{i+1}')
    voice_lines.append(ln)
ax_comp.legend(loc='upper right', fontsize=8, facecolor=BG_PANEL,
               edgecolor='#444', labelcolor=TEXT_CLR, ncol=4)

combined_line,  = ax_comb.plot([], [], color='#e0e0e0', linewidth=1.4,
                               label='Combined')
quantized_line, = ax_comb.plot([], [], color='#ff6b9d', linewidth=1.4,
                               alpha=0.9, drawstyle='steps-post', label='Quantized')
ax_comb.legend(loc='upper right', fontsize=8, facecolor=BG_PANEL,
               edgecolor='#444', labelcolor=TEXT_CLR)

# S&H tick lines (note onset markers) and playhead
sh_tick_lines  = []
playhead_line  = ax_comb.axvline(0, color=MIDI_COLOR, linewidth=1.2,
                                  alpha=0.8, linestyle='--', zorder=5,
                                  visible=False)


# -- Widget helpers ------------------------------------------------------------

def style_slider(sl):
    sl.label.set_color(TEXT_CLR)
    sl.label.set_fontsize(9)
    sl.valtext.set_color(TEXT_CLR)
    sl.valtext.set_fontsize(9)

def style_check(chk, color=TEXT_CLR):
    for lbl in chk.labels:
        lbl.set_color(color)
        lbl.set_fontsize(9)

def add_section_label(y, text, color=TEXT_CLR):
    fig.text(L_MARGIN + L_WIDTH / 2, y, text, ha='center', fontsize=10,
             fontweight='bold', color=color)

def make_slider(y_pos, label, vmin, vmax, vinit, vstep, color='#3498db'):
    ax = fig.add_axes([L_MARGIN, y_pos, L_WIDTH, SL_H], facecolor=BG_CTRL)
    sl = Slider(ax, label, vmin, vmax, valinit=vinit, valstep=vstep,
                color=color, initcolor='none')
    style_slider(sl)
    slider_defaults[sl] = vinit
    return ax, sl


# ===============================================================================
#  Controls
# ===============================================================================

y = 0.96
add_section_label(y, '-- GLOBAL --')

y -= 0.045
ax_mode = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.5, 0.035], facecolor=BG_DARK)
radio_mode = RadioButtons(ax_mode, ['clock', 'audio'], active=0,
                          activecolor='#3498db')
style_check(radio_mode)

y -= SL_H + GAP * 2
ax_bpm, sl_bpm = make_slider(y, 'BPM', 20, 300, GLOBAL_DEFAULTS['bpm'], 1)

y -= SL_H + GAP
ax_bars, sl_bars = make_slider(y, 'Bars', 1, 16, GLOBAL_DEFAULTS['bars'], 1)

ax_audiofreq = fig.add_axes([L_MARGIN, y, L_WIDTH, SL_H], facecolor=BG_CTRL)
sl_audiofreq = Slider(ax_audiofreq, 'Freq (Hz)', 20, 10000, valinit=110,
                      valstep=1, color='#3498db', initcolor='none')
style_slider(sl_audiofreq)
slider_defaults[sl_audiofreq] = 110
ax_audiofreq.set_visible(False)

y -= SL_H + GAP
ax_gamp, sl_gamp = make_slider(y, 'G.Amp', 0, 2.0, 1.0, 0.01)

y -= SL_H + GAP
ax_gdc, sl_gdc = make_slider(y, 'G.DC (V)', -5.0, 5.0, 0.0, 0.01)


# -- Quantizer -----------------------------------------------------------------

y -= SECT_GAP + 0.02
add_section_label(y, '-- QUANTIZER --', '#ff6b9d')

y -= CHK_H + GAP
ax_qon = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.45, CHK_H], facecolor=BG_DARK)
chk_quant = CheckButtons(ax_qon, ['Enable'], [False])
style_check(chk_quant, '#ff6b9d')

y -= SL_H + GAP
ax_qroot, sl_qroot = make_slider(y, 'Root', 0, 11, 0, 1, '#ff6b9d')
sl_qroot.valtext.set_text('C')

y -= SL_H + GAP
ax_qscale, sl_qscale = make_slider(y, 'Scale', 0, len(SCALE_NAMES) - 1, 1, 1, '#ff6b9d')
sl_qscale.valtext.set_text('major')

y -= SL_H + GAP
ax_qrange, sl_qrange = make_slider(y, 'Oct Rng', 1, 8, 2, 1, '#ff6b9d')

y -= SECT_GAP + 0.01
ax_rescale = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.5, 0.025], facecolor='#2a2a4a')
btn_rescale = Button(ax_rescale, 'Rescale Y', color='#2a2a4a', hovercolor='#3a3a5a')
btn_rescale.label.set_color(TEXT_CLR)
btn_rescale.label.set_fontsize(9)


# -- MIDI Out ------------------------------------------------------------------

y -= SECT_GAP + 0.025
add_section_label(y, '-- MIDI OUT --', MIDI_COLOR)

y -= CHK_H + GAP
ax_midi_on = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.5, CHK_H], facecolor=BG_DARK)
chk_midi = CheckButtons(ax_midi_on, ['Enable'], [False])
style_check(chk_midi, MIDI_COLOR)

y -= SL_H + GAP
ax_sh_div, sl_sh_div = make_slider(y, 'S&H Div', 0, len(SH_DIVISIONS) - 1,
                                   GLOBAL_DEFAULTS['sh_div'], 1, MIDI_COLOR)
sl_sh_div.valtext.set_text(SH_DIV_LABELS[GLOBAL_DEFAULTS['sh_div']])

y -= SL_H + GAP
_, sl_midi_ch = make_slider(y, 'Channel', 1, 16,
                            GLOBAL_DEFAULTS['midi_channel'], 1, MIDI_COLOR)

y -= SL_H + GAP
_, sl_midi_gate = make_slider(y, 'Gate', 0.05, 1.0,
                              GLOBAL_DEFAULTS['midi_gate'], 0.01, MIDI_COLOR)

y -= SL_H + GAP
_, sl_midi_vel = make_slider(y, 'Velocity', 1, 127,
                             GLOBAL_DEFAULTS['midi_velocity'], 1, MIDI_COLOR)

# Transport: Play/Pause  Reset
y -= SECT_GAP + 0.005
btn_w = (L_WIDTH - GAP) / 2

ax_playpause = fig.add_axes([L_MARGIN, y, btn_w, 0.026], facecolor='#1a3a2a')
btn_playpause = Button(ax_playpause, '|| Pause', color='#1a3a2a', hovercolor='#2a5a3a')
btn_playpause.label.set_color(MIDI_COLOR)
btn_playpause.label.set_fontsize(9)

ax_reset_btn = fig.add_axes([L_MARGIN + btn_w + GAP, y, btn_w, 0.026],
                             facecolor='#2a2a1a')
btn_reset = Button(ax_reset_btn, '[ ] Reset', color='#2a2a1a', hovercolor='#4a4a2a')
btn_reset.label.set_color('#f39c12')
btn_reset.label.set_fontsize(9)

y -= 0.024
midi_status_text = fig.text(
    L_MARGIN + L_WIDTH / 2, y, 'MIDI off',
    ha='center', fontsize=8, color='#888888', style='italic',
)
y -= 0.018
clock_status_text = fig.text(
    L_MARGIN + L_WIDTH / 2, y, 'Clock: Internal',
    ha='center', fontsize=8, color='#888888', style='italic',
)


# -- Voices --------------------------------------------------------------------

voice_sliders = []

for v in range(4):
    d     = VOICE_DEFAULTS[v]
    vs    = {}
    color = VOICE_COLORS[v]

    y -= SECT_GAP + 0.015
    add_section_label(y, f'-- VOICE {v+1} --', color)

    y -= CHK_H + GAP
    ax_chk = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.45, CHK_H], facecolor=BG_DARK)
    vs['chk'] = CheckButtons(ax_chk, ['On', 'Show'], [True, True])
    style_check(vs['chk'], color)

    y -= SL_H + GAP
    _, vs['shape'] = make_slider(y, 'Shape', 0, len(SHAPES) - 1, d['shape'], 1, color)
    vs['shape'].valtext.set_text(SHAPES[d['shape']])

    y -= SL_H + GAP
    _, vs['ratio'] = make_slider(y, 'Ratio', 0, len(RATIO_LABELS) - 1, d['ratio'], 1, color)
    vs['ratio'].valtext.set_text(RATIO_LABELS[d['ratio']])

    y -= SL_H + GAP
    _, vs['amp'] = make_slider(y, 'Amp (V)', 0, 5.0, d['amp'], 0.01, color)

    y -= SL_H + GAP
    _, vs['phase'] = make_slider(y, 'Phase (deg)', 0, 360, d['phase'], 1, color)

    voice_sliders.append(vs)


# ===============================================================================
#  Double-click to reset slider
# ===============================================================================

def on_figure_click(event):
    if event.dblclick:
        for sl, default_val in slider_defaults.items():
            if sl.ax == event.inaxes:
                sl.set_val(default_val)
                break

fig.canvas.mpl_connect('button_press_event', on_figure_click)


# ===============================================================================
#  Update (visualizer redraw)
# ===============================================================================

def update(val=None):
    """Redraw waveforms and S&H tick marks on both plot axes."""
    global sh_tick_lines

    is_clock = radio_mode.value_selected == 'clock'
    bpm      = sl_bpm.val
    bars     = int(sl_bars.val)

    if is_clock:
        complex_s   = (60.0 / bpm) * bars * 4.0
        total_beats = float(bars * 4)
        x_label     = 'Beats'
    else:
        complex_s   = 1.0 / max(sl_audiofreq.val, 0.001)
        total_beats = complex_s * 1000.0
        x_label     = 'Time (ms)'

    t = np.linspace(0, complex_s, SAMPLES, endpoint=False)
    x = np.linspace(0, total_beats, SAMPLES, endpoint=False)

    ax_comb.set_xlabel(x_label, fontsize=10, color=TEXT_CLR)

    active_wf = []
    for i in range(4):
        vs = voice_sliders[i]
        st = voice_states[i]
        if st.on:
            wf = generate_voice(
                shape_name=SHAPES[int(vs['shape'].val)],
                ratio_value=RATIO_VALUES[int(vs['ratio'].val)],
                amplitude_v=vs['amp'].val,
                phase_deg=vs['phase'].val,
                complex_period_s=complex_s,
                t_seconds=t,
            )
            active_wf.append(wf)
            if st.show:
                voice_lines[i].set_data(x, wf)
                voice_lines[i].set_visible(True)
            else:
                voice_lines[i].set_data([], [])
                voice_lines[i].set_visible(False)
        else:
            voice_lines[i].set_data([], [])
            voice_lines[i].set_visible(False)

    if active_wf:
        combined = sl_gamp.val * np.sum(active_wf, axis=0) + sl_gdc.val
        if quant_enabled:
            root_name  = NOTE_NAMES[int(sl_qroot.val)]
            scale_name = SCALE_NAMES[int(sl_qscale.val)]
            q = quantize_to_scale(
                combined,
                root_note=int(sl_qroot.val),
                scale_degrees=SCALE_VALUES[int(sl_qscale.val)],
                octave_range=int(sl_qrange.val),
            )
            quantized_line.set_data(x, q)
            quantized_line.set_visible(True)
            combined_line.set_data([], [])
            combined_line.set_visible(False)
            ax_comb.set_ylabel(f'V/oct  (0V={root_name}4, {scale_name})',
                               fontsize=10, color='#ff6b9d')
        else:
            combined_line.set_data(x, combined)
            combined_line.set_visible(True)
            quantized_line.set_data([], [])
            quantized_line.set_visible(False)
            ax_comb.set_ylabel('Volts', fontsize=10, color=TEXT_CLR)
    else:
        for ln in [combined_line, quantized_line]:
            ln.set_data([], [])
            ln.set_visible(False)

    # S&H tick marks (note onset positions)
    for ln in sh_tick_lines:
        try:
            ln.remove()
        except Exception:
            pass
    sh_tick_lines = []

    if is_clock:
        div_idx        = int(sl_sh_div.val)
        beats_per_step = SH_DIV_BEATS[div_idx]
        step_x         = 0.0
        while step_x <= total_beats + 1e-6:
            ln = ax_comb.axvline(step_x, color=MIDI_COLOR, linewidth=0.7,
                                 alpha=0.45, linestyle=':', zorder=4)
            sh_tick_lines.append(ln)
            step_x += beats_per_step

    ax_comp.set_xlim(x[0], x[-1])
    ax_comb.set_xlim(x[0], x[-1])

    # Y limits (expand only, never contract)
    comp_min = comp_max = 0.0
    for i in range(4):
        _, yd = voice_lines[i].get_data()
        if len(yd):
            comp_min = min(comp_min, float(np.min(yd)))
            comp_max = max(comp_max, float(np.max(yd)))
    if comp_min == comp_max:
        comp_min, comp_max = -1.0, 1.0
    m = (comp_max - comp_min) * 0.1
    lo, hi = ax_comp.get_ylim()
    ax_comp.set_ylim(min(lo, comp_min - m), max(hi, comp_max + m))

    comb_min = comb_max = 0.0
    for ln in [combined_line, quantized_line]:
        _, yd = ln.get_data()
        if len(yd):
            comb_min = min(comb_min, float(np.min(yd)))
            comb_max = max(comb_max, float(np.max(yd)))
    if comb_min == comb_max:
        comb_min, comb_max = -1.0, 1.0
    m = (comb_max - comb_min) * 0.1
    lo, hi = ax_comb.get_ylim()
    ax_comb.set_ylim(min(lo, comb_min - m), max(hi, comb_max + m))

    fig.canvas.draw_idle()


# ===============================================================================
#  Playhead animation timer (60ms interval)
# ===============================================================================

def _tick_playhead(frame=None):
    """Update the playhead line position and sync status labels."""
    if not midi_engine.active:
        return

    is_clock    = radio_mode.value_selected == 'clock'
    bars        = int(sl_bars.val)
    total_beats = bars * 4.0

    phase    = midi_engine.phase
    beat_pos = phase * total_beats if is_clock else phase * 1000.0

    playhead_line.set_xdata([beat_pos, beat_pos])
    playhead_line.set_visible(True)

    # Clock sync label
    synced_bpm = clock_rx.bpm
    if synced_bpm is not None:
        clock_status_text.set_text(f'Clock: Synced {synced_bpm:.1f} BPM')
        clock_status_text.set_color(MIDI_COLOR)
    else:
        clock_status_text.set_text('Clock: Internal')
        clock_status_text.set_color('#888888')

    # Play/pause button label
    btn_playpause.label.set_text('> Play' if not midi_engine.playing else '|| Pause')

    fig.canvas.draw_idle()


_playhead_timer = fig.canvas.new_timer(interval=60)
_playhead_timer.add_callback(_tick_playhead)
_playhead_timer.start()


# ===============================================================================
#  Status helpers
# ===============================================================================

def _refresh_midi_status():
    color = MIDI_COLOR if midi_engine.active else '#888888'
    midi_status_text.set_text(midi_engine.status)
    midi_status_text.set_color(color)
    playhead_line.set_visible(midi_engine.active)
    try:
        fig.canvas.draw_idle()
    except Exception:
        pass


# ===============================================================================
#  Callbacks
# ===============================================================================

def on_mode_change(label):
    is_clock = (label == 'clock')
    ax_bars.set_visible(is_clock)
    ax_audiofreq.set_visible(not is_clock)
    update()

radio_mode.on_clicked(on_mode_change)

# MIDI enable/disable
def on_midi_toggle(label):
    if midi_engine.active:
        midi_engine.stop()
    else:
        midi_engine.start()
chk_midi.on_clicked(on_midi_toggle)

# Play / Pause
def on_play_pause(event):
    if not midi_engine.active:
        return
    midi_engine.play_pause()
btn_playpause.on_clicked(on_play_pause)

# Reset
def on_reset(event):
    midi_engine.reset()
btn_reset.on_clicked(on_reset)

# Rescale Y
def on_rescale(event):
    for ax, lines in [(ax_comp, voice_lines),
                      (ax_comb, [combined_line, quantized_line])]:
        vmin = vmax = 0.0
        for ln in lines:
            _, yd = ln.get_data()
            if len(yd):
                vmin = min(vmin, float(np.min(yd)))
                vmax = max(vmax, float(np.max(yd)))
        if vmin == vmax:
            vmin, vmax = -1.0, 1.0
        m = (vmax - vmin) * 0.1
        ax.set_ylim(vmin - m, vmax + m)
    fig.canvas.draw_idle()
btn_rescale.on_clicked(on_rescale)

# S&H division label update
def on_sh_div_changed(val):
    sl_sh_div.valtext.set_text(SH_DIV_LABELS[int(val)])
    midi_engine.mark_dirty()
    update()
sl_sh_div.on_changed(on_sh_div_changed)

# All waveform-affecting sliders mark dirty + redraw
def _on_waveform_change(val=None):
    midi_engine.mark_dirty()
    update()

sl_bpm.on_changed(_on_waveform_change)
sl_bars.on_changed(_on_waveform_change)
sl_audiofreq.on_changed(_on_waveform_change)
sl_gamp.on_changed(_on_waveform_change)
sl_gdc.on_changed(_on_waveform_change)
sl_qrange.on_changed(_on_waveform_change)

# Quantizer
def on_quant_toggle(label):
    global quant_enabled
    quant_enabled = not quant_enabled
    midi_engine.mark_dirty()
    update()
chk_quant.on_clicked(on_quant_toggle)

def on_root_changed(val):
    sl_qroot.valtext.set_text(NOTE_NAMES[int(val)])
    midi_engine.mark_dirty()
    update()
sl_qroot.on_changed(on_root_changed)

def on_scale_changed(val):
    sl_qscale.valtext.set_text(SCALE_NAMES[int(val)])
    midi_engine.mark_dirty()
    update()
sl_qscale.on_changed(on_scale_changed)

# Per-voice
def make_voice_checkbox_cb(idx):
    def cb(label):
        st = voice_states[idx]
        if label == 'On':
            st.on = not st.on
        elif label == 'Show':
            st.show = not st.show
        midi_engine.mark_dirty()
        update()
    return cb

def make_shape_changed(idx):
    def cb(val):
        voice_sliders[idx]['shape'].valtext.set_text(SHAPES[int(val)])
        midi_engine.mark_dirty()
        update()
    return cb

def make_ratio_changed(idx):
    def cb(val):
        voice_sliders[idx]['ratio'].valtext.set_text(RATIO_LABELS[int(val)])
        midi_engine.mark_dirty()
        update()
    return cb

for i in range(4):
    vs = voice_sliders[i]
    vs['chk'].on_clicked(make_voice_checkbox_cb(i))
    vs['shape'].on_changed(make_shape_changed(i))
    vs['ratio'].on_changed(make_ratio_changed(i))
    vs['amp'].on_changed(_on_waveform_change)
    vs['phase'].on_changed(_on_waveform_change)


# ===============================================================================
#  Launch
# ===============================================================================

def on_close(event):
    _playhead_timer.stop()
    midi_engine.stop()
    clock_rx.stop()

fig.canvas.mpl_connect('close_event', on_close)

clock_rx.start()
update()
plt.show()
