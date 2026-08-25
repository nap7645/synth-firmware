"""
Complex Waveform Visualizer
============================
Four waveform generators → sum → optional quantizer → voltage output.
Melody sequencer (clock mode) or complex oscillator (audio mode).

V/oct standard: 0V = C4 (261.6 Hz), ±1V = ±1 octave, 1/12V = 1 semitone.
Double-click any slider to reset it to its default value.

Usage:   python3 complex_waveform_visualizer.py
Requires: numpy, matplotlib
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, RadioButtons, CheckButtons, Button
import threading
import time

try:
    import rtmidi
    RTMIDI_AVAILABLE = True
except ImportError:
    RTMIDI_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════

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
}
SCALE_NAMES = list(SCALES.keys())
SCALE_VALUES = list(SCALES.values())
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

VOICE_COLORS = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12']
BG_DARK = '#1a1a2e'
BG_PANEL = '#16213e'
BG_CTRL = '#0f1626'
TEXT_CLR = '#cccccc'

SAMPLES = 4096

# Per-voice defaults: shape_idx, ratio_idx, amplitude(V), phase(deg)
# Shapes: 0=sine, 1=triangle, 2=square, 3=ramp up, 4=ramp dn
# Ratio idx 5 = '1x'
VOICE_DEFAULTS = [
    {'shape': 0, 'ratio': 5, 'amp': 1.0, 'phase': 0},    # sine 1x
    {'shape': 2, 'ratio': 5, 'amp': 1.0, 'phase': 0},    # square 1x
    {'shape': 3, 'ratio': 5, 'amp': 1.0, 'phase': 0},    # ramp up 1x
    {'shape': 4, 'ratio': 5, 'amp': 1.0, 'phase': 0},    # ramp dn 1x
]

# Global defaults
GLOBAL_DEFAULTS = {
    'bpm': 130, 'pulses': 16, 'audio_freq': 110,
    'gamp': 1.0, 'gdc': 0.0,
    'qroot': 0, 'qscale': 1, 'qrange': 2,
    'midi_channel': 1, 'midi_gate': 0.5, 'midi_velocity': 100,
}

MIDI_PORT_NAME = 'Complex Waveform Out'


# ═══════════════════════════════════════════════════════════════════════════════
#  Waveform Engine
# ═══════════════════════════════════════════════════════════════════════════════

def generate_waveform(shape_name, phase_deg, t_cycle):
    """Generate waveform over fractional cycle position. Returns [-1, +1]."""
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
    """Generate one voice's output in volts."""
    freq_hz = ratio_value / complex_period_s
    if freq_hz <= 0:
        return np.zeros_like(t_seconds)
    t_cycle = (t_seconds * freq_hz) % 1.0
    return amplitude_v * generate_waveform(shape_name, phase_deg, t_cycle)


def quantize_to_scale(voltage, root_note, scale_degrees, octave_range):
    """Normalize voltage to bipolar V/oct, snap to scale degrees.

    Eurorack V/oct standard:
      0V  = C4 (middle C) + root offset
      +1V = one octave up
      -1V = one octave down
      1/12V = one semitone

    The combined waveform's range is mapped to ±(octave_range/2) volts,
    centered on 0V, then snapped to the nearest valid scale degree.
    """
    v_min = np.min(voltage)
    v_max = np.max(voltage)
    if v_max - v_min < 1e-9:
        return np.zeros_like(voltage)

    # Map signal range to [-octave_range/2, +octave_range/2]
    half = octave_range / 2.0
    normalized = (voltage - v_min) / (v_max - v_min) * octave_range - half

    # Build valid V/oct values centered on 0V
    # Root note offset in volts: root_note semitones = root_note/12 V
    root_offset = root_note / 12.0
    valid_voct = []
    for octave in range(-octave_range, octave_range + 1):
        for d in scale_degrees:
            v = octave + d / 12.0
            valid_voct.append(v)
    valid_voct = np.array(sorted(set(valid_voct)))
    # Keep only values within our range (with a little margin)
    valid_voct = valid_voct[(valid_voct >= -half - 0.1) & (valid_voct <= half + 0.1)]

    if len(valid_voct) == 0:
        return np.zeros_like(voltage)

    # Snap to nearest
    idx = np.searchsorted(valid_voct, normalized, side='left')
    idx = np.clip(idx, 0, len(valid_voct) - 1)
    idx_r = idx
    idx_l = np.clip(idx - 1, 0, len(valid_voct) - 1)
    best = np.where(
        np.abs(normalized - valid_voct[idx_l]) < np.abs(normalized - valid_voct[idx_r]),
        idx_l, idx_r
    )
    return valid_voct[best]


# ═══════════════════════════════════════════════════════════════════════════════
#  State
# ═══════════════════════════════════════════════════════════════════════════════

class VoiceState:
    def __init__(self, on=True, show=True):
        self.on = on
        self.show = show

voice_states = [VoiceState() for _ in range(4)]
quant_enabled = False

# Track all sliders + defaults for double-click reset
# Populated during UI construction below
slider_defaults = {}  # maps slider object -> default value


# ═══════════════════════════════════════════════════════════════════════════════
#  MIDI Engine
# ═══════════════════════════════════════════════════════════════════════════════

class MidiEngine:
    """Continuous looping MIDI playback in a background thread.

    Reads current visualizer state at the top of each sequence loop so that
    BPM, pulse count, quantizer, and MIDI parameter changes take effect
    immediately on the next cycle.
    """

    def __init__(self):
        self._thread = None
        self._stop_evt = threading.Event()
        self._midiout = None
        self._last_note = None   # track hanging notes for clean note-off
        self._last_channel = 0
        self.active = False
        self.status = 'MIDI off'

    # ── Port management ───────────────────────────────────────────────────────

    def _open_port(self):
        if not RTMIDI_AVAILABLE:
            self.status = 'rtmidi not installed'
            return False
        try:
            self._midiout = rtmidi.MidiOut()
            self._midiout.open_virtual_port(MIDI_PORT_NAME)
            self.status = f'Port: {MIDI_PORT_NAME}'
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

    # ── Note helpers ──────────────────────────────────────────────────────────

    def _note_on(self, channel, note, velocity):
        if self._midiout is None:
            return
        self._midiout.send_message([0x90 | channel, note, velocity])
        self._last_note = note
        self._last_channel = channel

    def _note_off(self, channel, note):
        if self._midiout is None:
            return
        self._midiout.send_message([0x80 | channel, note, 0])

    def _kill_hanging_note(self):
        if self._last_note is not None:
            self._note_off(self._last_channel, self._last_note)
            self._last_note = None

    # ── Conversion ────────────────────────────────────────────────────────────

    @staticmethod
    def voct_to_midi(voct):
        """Convert V/oct float to MIDI note number. 0V = C4 = 60."""
        return int(round(np.clip(60 + voct * 12, 0, 127)))

    # ── Sequence snapshot ─────────────────────────────────────────────────────

    def _get_sequence(self):
        """Sample the current waveform state and return a list of MIDI notes,
        one per pulse. Called at the top of each loop iteration."""
        try:
            bpm      = sl_bpm.val
            pulses   = int(sl_pulses.val)
            is_clock = radio_mode.value_selected == 'clock'

            if is_clock:
                complex_s = (60.0 / bpm) * pulses
            else:
                complex_s = 1.0 / max(sl_audiofreq.val, 0.001)

            t = np.linspace(0, complex_s, SAMPLES, endpoint=False)

            # Build combined waveform (mirrors update())
            active_wf = []
            for i in range(4):
                vs = voice_sliders[i]
                st = voice_states[i]
                if st.on:
                    shape_name = SHAPES[int(vs['shape'].val)]
                    ratio_val  = RATIO_VALUES[int(vs['ratio'].val)]
                    wf = generate_voice(
                        shape_name=shape_name,
                        ratio_value=ratio_val,
                        amplitude_v=vs['amp'].val,
                        phase_deg=vs['phase'].val,
                        complex_period_s=complex_s,
                        t_seconds=t,
                    )
                    active_wf.append(wf)

            if not active_wf:
                return [None] * pulses, bpm, pulses

            combined = sl_gamp.val * np.sum(active_wf, axis=0) + sl_gdc.val

            # Quantize
            if quant_enabled:
                voct_seq = quantize_to_scale(
                    combined,
                    root_note=int(sl_qroot.val),
                    scale_degrees=SCALE_VALUES[int(sl_qscale.val)],
                    octave_range=int(sl_qrange.val),
                )
            else:
                # On-the-fly quantize to chromatic for MIDI even without quantizer
                voct_seq = quantize_to_scale(
                    combined,
                    root_note=0,
                    scale_degrees=SCALES['chromatic'],
                    octave_range=int(sl_qrange.val),
                )

            # Sample one value per pulse at evenly-spaced indices
            indices = np.linspace(0, SAMPLES - 1, pulses, dtype=int)
            notes = [self.voct_to_midi(voct_seq[idx]) for idx in indices]
            return notes, bpm, pulses

        except Exception:
            return [None] * 16, 130, 16

    # ── Playback thread ───────────────────────────────────────────────────────

    def _run(self):
        while not self._stop_evt.is_set():
            notes, bpm, pulses = self._get_sequence()
            pulse_dur = 60.0 / bpm          # seconds per pulse
            channel   = int(sl_midi_ch.val) - 1   # 0-indexed for rtmidi
            velocity  = int(sl_midi_vel.val)
            gate_frac = sl_midi_gate.val

            for note in notes:
                if self._stop_evt.is_set():
                    break

                t_pulse_start = time.perf_counter()

                if note is not None:
                    self._kill_hanging_note()
                    self._note_on(channel, note, velocity)

                    gate_dur = pulse_dur * gate_frac
                    time.sleep(gate_dur)
                    self._note_off(channel, note)
                    self._last_note = None

                    remaining = pulse_dur - gate_dur
                    if remaining > 0:
                        time.sleep(remaining)
                else:
                    time.sleep(pulse_dur)

        self._kill_hanging_note()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if self.active:
            return
        if not self._open_port():
            _refresh_midi_status()
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.active = True
        _refresh_midi_status()

    def stop(self):
        if not self.active:
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._close_port()
        self.active = False
        self.status = 'MIDI off'
        _refresh_midi_status()


midi_engine = MidiEngine()


# ═══════════════════════════════════════════════════════════════════════════════
#  Layout
# ═══════════════════════════════════════════════════════════════════════════════

L_MARGIN = 0.08
L_WIDTH  = 0.24
SL_H     = 0.018
GAP      = 0.003
CHK_H    = 0.024
SECT_GAP = 0.008

P_LEFT   = 0.42
P_RIGHT  = 0.97
P_WIDTH  = P_RIGHT - P_LEFT


# ═══════════════════════════════════════════════════════════════════════════════
#  Build figure
# ═══════════════════════════════════════════════════════════════════════════════

fig = plt.figure(figsize=(17, 10), facecolor=BG_DARK)
fig.canvas.manager.set_window_title('Complex Waveform Visualizer')

# ── Plot axes ────────────────────────────────────────────────────────────────

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
ax_comb.set_xlabel('Beats (clock pulses)', fontsize=10)
plt.setp(ax_comp.get_xticklabels(), visible=False)

voice_lines = []
for i in range(4):
    ln, = ax_comp.plot([], [], color=VOICE_COLORS[i], linewidth=1.2,
                       alpha=0.85, label=f'V{i+1}')
    voice_lines.append(ln)
ax_comp.legend(loc='upper right', fontsize=8, facecolor=BG_PANEL,
               edgecolor='#444', labelcolor=TEXT_CLR, ncol=4)

combined_line, = ax_comb.plot([], [], color='#e0e0e0', linewidth=1.4,
                              label='Combined')
quantized_line, = ax_comb.plot([], [], color='#ff6b9d', linewidth=1.4,
                               alpha=0.9, drawstyle='steps-post', label='Quantized')
ax_comb.legend(loc='upper right', fontsize=8, facecolor=BG_PANEL,
               edgecolor='#444', labelcolor=TEXT_CLR)


# ── Helpers ──────────────────────────────────────────────────────────────────

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
    """Create a slider and register its default for double-click reset."""
    ax = fig.add_axes([L_MARGIN, y_pos, L_WIDTH, SL_H], facecolor=BG_CTRL)
    sl = Slider(ax, label, vmin, vmax, valinit=vinit, valstep=vstep,
                color=color, initcolor='none')
    style_slider(sl)
    slider_defaults[sl] = vinit
    return ax, sl


# ═══════════════════════════════════════════════════════════════════════════════
#  Controls
# ═══════════════════════════════════════════════════════════════════════════════

y = 0.96
add_section_label(y, '── GLOBAL ──')

y -= 0.045
ax_mode = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.5, 0.035], facecolor=BG_DARK)
radio_mode = RadioButtons(ax_mode, ['clock', 'audio'], active=0,
                          activecolor='#3498db')
style_check(radio_mode)

y -= SL_H + GAP * 2
ax_bpm, sl_bpm = make_slider(y, 'BPM', 20, 300, 130, 1)

y -= SL_H + GAP
ax_pulses, sl_pulses = make_slider(y, 'Pulses', 1, 64, 16, 1)

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


# ── Quantizer ────────────────────────────────────────────────────────────────
y -= SECT_GAP + 0.02
add_section_label(y, '── QUANTIZER ──', '#ff6b9d')

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

# Rescale Y button
y -= SECT_GAP + 0.01
ax_rescale = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.5, 0.025], facecolor='#2a2a4a')
btn_rescale = Button(ax_rescale, 'Rescale Y', color='#2a2a4a', hovercolor='#3a3a5a')
btn_rescale.label.set_color(TEXT_CLR)
btn_rescale.label.set_fontsize(9)


# ── MIDI Out ─────────────────────────────────────────────────────────────────

MIDI_COLOR = '#00d4aa'

y -= SECT_GAP + 0.02
add_section_label(y, '── MIDI OUT ──', MIDI_COLOR)

y -= CHK_H + GAP
ax_midi_on = fig.add_axes([L_MARGIN, y, L_WIDTH * 0.5, CHK_H], facecolor=BG_DARK)
chk_midi = CheckButtons(ax_midi_on, ['Enable'], [False])
style_check(chk_midi, MIDI_COLOR)

y -= SL_H + GAP
_, sl_midi_ch = make_slider(y, 'Channel', 1, 16,
                            GLOBAL_DEFAULTS['midi_channel'], 1, MIDI_COLOR)

y -= SL_H + GAP
_, sl_midi_gate = make_slider(y, 'Gate', 0.05, 1.0,
                              GLOBAL_DEFAULTS['midi_gate'], 0.01, MIDI_COLOR)

y -= SL_H + GAP
_, sl_midi_vel = make_slider(y, 'Velocity', 1, 127,
                             GLOBAL_DEFAULTS['midi_velocity'], 1, MIDI_COLOR)

y -= GAP + 0.022
midi_status_text = fig.text(
    L_MARGIN + L_WIDTH / 2, y, 'MIDI off',
    ha='center', fontsize=8, color='#888888',
    style='italic',
)


# ── Voices (no DC offset) ───────────────────────────────────────────────────

voice_sliders = []

for v in range(4):
    d = VOICE_DEFAULTS[v]
    vs = {}
    color = VOICE_COLORS[v]

    y -= SECT_GAP + 0.015
    add_section_label(y, f'── VOICE {v+1} ──', color)

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
    _, vs['phase'] = make_slider(y, 'Phase (°)', 0, 360, d['phase'], 1, color)

    voice_sliders.append(vs)


# ═══════════════════════════════════════════════════════════════════════════════
#  Double-click to reset any slider
# ═══════════════════════════════════════════════════════════════════════════════

def on_figure_click(event):
    """Reset a slider to default on double-click."""
    if event.dblclick:
        for sl, default_val in slider_defaults.items():
            if sl.ax == event.inaxes:
                sl.set_val(default_val)
                break

fig.canvas.mpl_connect('button_press_event', on_figure_click)


# ═══════════════════════════════════════════════════════════════════════════════
#  Update
# ═══════════════════════════════════════════════════════════════════════════════

def update(val=None):
    """Recalculate and redraw all waveforms."""
    is_clock = radio_mode.value_selected == 'clock'
    if is_clock:
        complex_s = (60.0 / sl_bpm.val) * sl_pulses.val
    else:
        complex_s = 1.0 / max(sl_audiofreq.val, 0.001)

    t = np.linspace(0, complex_s, SAMPLES, endpoint=False)

    if is_clock:
        x = t / (60.0 / sl_bpm.val)
        ax_comb.set_xlabel('Beats (clock pulses)', fontsize=10, color=TEXT_CLR)
    else:
        x = t * 1000.0
        ax_comb.set_xlabel('Time (ms)', fontsize=10, color=TEXT_CLR)

    active_wf = []
    for i in range(4):
        vs = voice_sliders[i]
        st = voice_states[i]

        if st.on:
            shape_name = SHAPES[int(vs['shape'].val)]
            ratio_val = RATIO_VALUES[int(vs['ratio'].val)]
            wf = generate_voice(
                shape_name=shape_name,
                ratio_value=ratio_val,
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
            root_name = NOTE_NAMES[int(sl_qroot.val)]
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
            ax_comb.set_ylabel(f'V/oct  (0V = {root_name}4, {scale_name})',
                               fontsize=10, color='#ff6b9d')
        else:
            combined_line.set_data(x, combined)
            combined_line.set_visible(True)
            quantized_line.set_data([], [])
            quantized_line.set_visible(False)
            ax_comb.set_ylabel('Volts', fontsize=10, color=TEXT_CLR)
    else:
        combined_line.set_data([], [])
        combined_line.set_visible(False)
        quantized_line.set_data([], [])
        quantized_line.set_visible(False)

    # X limits
    ax_comp.set_xlim(x[0], x[-1])
    ax_comb.set_xlim(x[0], x[-1])

    # Y-axis: expand only, never contract
    comp_min, comp_max = 0.0, 0.0
    for i in range(4):
        xd, yd = voice_lines[i].get_data()
        if len(yd) > 0:
            comp_min = min(comp_min, np.min(yd))
            comp_max = max(comp_max, np.max(yd))
    if comp_min == comp_max:
        comp_min, comp_max = -1.0, 1.0
    m = (comp_max - comp_min) * 0.1
    cur_lo, cur_hi = ax_comp.get_ylim()
    ax_comp.set_ylim(min(cur_lo, comp_min - m), max(cur_hi, comp_max + m))

    comb_min, comb_max = 0.0, 0.0
    for line in [combined_line, quantized_line]:
        xd, yd = line.get_data()
        if len(yd) > 0:
            comb_min = min(comb_min, np.min(yd))
            comb_max = max(comb_max, np.max(yd))
    if comb_min == comb_max:
        comb_min, comb_max = -1.0, 1.0
    m = (comb_max - comb_min) * 0.1
    cur_lo, cur_hi = ax_comb.get_ylim()
    ax_comb.set_ylim(min(cur_lo, comb_min - m), max(cur_hi, comb_max + m))

    fig.canvas.draw_idle()


# ═══════════════════════════════════════════════════════════════════════════════
#  Callbacks
# ═══════════════════════════════════════════════════════════════════════════════

def on_mode_change(label):
    is_clock = (label == 'clock')
    ax_bpm.set_visible(is_clock)
    ax_pulses.set_visible(is_clock)
    ax_audiofreq.set_visible(not is_clock)
    update()

radio_mode.on_clicked(on_mode_change)

# MIDI status label refresh (called by MidiEngine on start/stop)
def _refresh_midi_status():
    status = midi_engine.status
    color  = MIDI_COLOR if midi_engine.active else '#888888'
    midi_status_text.set_text(status)
    midi_status_text.set_color(color)
    fig.canvas.draw_idle()

# MIDI checkbox
def on_midi_toggle(label):
    if midi_engine.active:
        midi_engine.stop()
    else:
        midi_engine.start()

chk_midi.on_clicked(on_midi_toggle)

# Rescale Y
def on_rescale(event):
    comp_min, comp_max = 0.0, 0.0
    for i in range(4):
        xd, yd = voice_lines[i].get_data()
        if len(yd) > 0:
            comp_min = min(comp_min, np.min(yd))
            comp_max = max(comp_max, np.max(yd))
    if comp_min == comp_max:
        comp_min, comp_max = -1.0, 1.0
    m = (comp_max - comp_min) * 0.1
    ax_comp.set_ylim(comp_min - m, comp_max + m)

    comb_min, comb_max = 0.0, 0.0
    for line in [combined_line, quantized_line]:
        xd, yd = line.get_data()
        if len(yd) > 0:
            comb_min = min(comb_min, np.min(yd))
            comb_max = max(comb_max, np.max(yd))
    if comb_min == comb_max:
        comb_min, comb_max = -1.0, 1.0
    m = (comb_max - comb_min) * 0.1
    ax_comb.set_ylim(comb_min - m, comb_max + m)
    fig.canvas.draw_idle()

btn_rescale.on_clicked(on_rescale)

# Global sliders
sl_bpm.on_changed(update)
sl_pulses.on_changed(update)
sl_audiofreq.on_changed(update)
sl_gamp.on_changed(update)
sl_gdc.on_changed(update)
sl_qrange.on_changed(update)

# Quantizer checkbox
def on_quant_toggle(label):
    global quant_enabled
    quant_enabled = not quant_enabled
    update()
chk_quant.on_clicked(on_quant_toggle)

# Root/scale text labels
def on_root_changed(val):
    sl_qroot.valtext.set_text(NOTE_NAMES[int(val)])
    update()
sl_qroot.on_changed(on_root_changed)

def on_scale_changed(val):
    sl_qscale.valtext.set_text(SCALE_NAMES[int(val)])
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
        update()
    return cb

def make_shape_changed(idx):
    def cb(val):
        voice_sliders[idx]['shape'].valtext.set_text(SHAPES[int(val)])
        update()
    return cb

def make_ratio_changed(idx):
    def cb(val):
        voice_sliders[idx]['ratio'].valtext.set_text(RATIO_LABELS[int(val)])
        update()
    return cb

for i in range(4):
    vs = voice_sliders[i]
    vs['chk'].on_clicked(make_voice_checkbox_cb(i))
    vs['shape'].on_changed(make_shape_changed(i))
    vs['ratio'].on_changed(make_ratio_changed(i))
    vs['amp'].on_changed(update)
    vs['phase'].on_changed(update)


# ═══════════════════════════════════════════════════════════════════════════════
#  Launch
# ═══════════════════════════════════════════════════════════════════════════════

def on_close(event):
    midi_engine.stop()

fig.canvas.mpl_connect('close_event', on_close)

update()
plt.show()
