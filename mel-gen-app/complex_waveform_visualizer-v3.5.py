"""
Complex Waveform Visualizer  v3.5
===================================
Four waveform generators → per-voice clipper → sum → global clipper
  → optional quantizer → transpose → MIDI output.

V/oct standard: 0V = C4 (261.6 Hz), ±1V = ±1 octave, 1/12V = 1 semitone.
Double-click any slider to reset it to its default value.

Changes in v3.5
  - Audio output mode and the AudioEngine class removed. Real-time
    synthesis would need a plugin host (VST/AU); a standalone Python
    process can only route through a virtual audio loopback driver,
    which adds setup friction without buying anything for the
    melodic-sequencer use case. MIDI to a DAW is the supported path.
  - The clock/audio mode selector is gone. The Internal/External
    clock-source radio (added in v3.4) takes its place in the GLOBAL
    section header.

Changes in v3.4
  - Bars slider goes 0.5 to 16 in 0.5 steps (half-bar minimum).
  - Hz slider has exponential taper (slider stores log10(Hz); the
    value text shows the actual Hz with adaptive precision).
  - Per-voice saturation rewritten as a fixed soft-knee saturator
    (linear below 0.8*amp, tanh rolloff approaching ±amp). Decoupled
    from the global clipper mode.
  - Internal/External clock selector in the GLOBAL section.
  - Clock receiver now opens every available MIDI input port so DAWs
    routing clock to an IAC bus reach us automatically.

Changes in v3.2
  - Per-voice Hz toggle: each voice can run free (Hz slider) or
    tempo-synced (Ratio × complex period). Toggle per voice.
  - Transpose slider: post-quantizer semitone shift (±24), in Quantizer section.
  - Root and Transpose are now separate controls.
  - Clipping stage (from v3.1): per-voice saturation at 80% amp,
    global clipper with mode selector (hard/soft/fold/wrap).
  - Rescale Y button removed.
  - G.Amp scales signal range; G.DC shifts pre-quantizer (both affect pitch
    register in quantizer mode as expected).

MIDI Clock sync: enable Sync out on an IAC Bus port in Ableton
  Preferences → Link, Tempo & MIDI → Output Ports → IAC Driver → Sync ON

S&H playhead: phase advances continuously, sampled at each S&H division.
  Dirty flag ensures parameter changes take effect within one step.

Usage:   python3 complex_waveform_visualizer-v3.5.py
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
    'qtranspose': 0,        # post-quantizer semitone shift
    'midi_channel': 1, 'midi_gate': 0.5, 'midi_velocity': 100,
    'sh_div': 2,
    'clip_thresh': 4.0,
}

CLIP_MODES = ['hard', 'soft', 'fold', 'wrap']

# Per-voice Hz mode defaults
VOICE_HZ_DEFAULT = 110.0   # Hz when a voice is in free-Hz mode

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


def generate_voice_hz(shape_name, freq_hz, amplitude_v, phase_deg, t_seconds):
    """Generate a voice at a fixed Hz frequency, independent of complex period."""
    if freq_hz <= 0:
        return np.zeros_like(t_seconds)
    t_cycle = (t_seconds * freq_hz) % 1.0
    return amplitude_v * generate_waveform(shape_name, phase_deg, t_cycle)


def apply_clip(signal, threshold, mode):
    """Clip/waveshape a numpy array.

    hard - brick wall at +-threshold
    soft - tanh curve: knee at ~80% threshold, saturated at threshold
             out = threshold * tanh(3x/threshold) / tanh(3)
    fold - reflects signal back at boundary (wavefold)
    wrap - modulo wraparound within +-threshold
    """
    t = max(float(threshold), 1e-6)
    if mode == 'hard':
        return np.clip(signal, -t, t)
    elif mode == 'soft':
        return t * np.tanh(3.0 * signal / t) / np.tanh(3.0)
    elif mode == 'fold':
        period = 4.0 * t
        x = (signal + t) % period
        return np.where(x < 2.0 * t, x - t, 3.0 * t - x)
    elif mode == 'wrap':
        return ((signal + t) % (2.0 * t)) - t
    return signal


def soft_knee_saturate(signal, amp, knee=0.8):
    """Per-voice soft-knee saturator.

    Linear pass-through for |x| <= knee*amp; smooth tanh rolloff above the
    knee, asymptotically approaching ±amp. This shapes only the top
    (1 - knee) fraction of the amplitude range without affecting the
    body of the signal.

    Decoupled from the global clipper mode by design: per-voice saturation
    is a fixed soft-knee, and creative clipping modes (hard/fold/wrap)
    apply only to the summed signal in the global stage.
    """
    if amp <= 1e-9:
        return np.zeros_like(signal)
    t = knee * amp
    headroom = amp - t                       # = (1 - knee) * amp
    sign = np.sign(signal)
    mag  = np.abs(signal)
    over = np.maximum(mag - t, 0.0)
    # tanh rolloff in the headroom region; tanh(over/headroom) approaches 1
    saturated = headroom * np.tanh(over / max(headroom, 1e-9))
    out_mag   = np.minimum(mag, t) + saturated
    return sign * out_mag


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
    """Compute full voct_seq (length SAMPLES) for the MIDI engine.

    Signal chain:
      per voice: generate → per-voice clip (thresh = 0.8 * amp)
      sum → G.Amp scale → G.DC shift → global clip → quantizer → transpose
    """
    t = np.linspace(0, complex_s, SAMPLES, endpoint=False)
    active_wf = []
    for i in range(4):
        vs = voice_sliders[i]
        st = voice_states[i]
        if not st.on:
            continue
        amp_val = vs['amp'].val
        shape   = SHAPES[int(vs['shape'].val)]
        phase   = vs['phase'].val
        if voice_hz_modes[i]:
            wf = generate_voice_hz(shape, 10.0 ** vs['hz'].val, amp_val, phase, t)
        else:
            wf = generate_voice(shape, RATIO_VALUES[int(vs['ratio'].val)],
                                amp_val, phase, complex_s, t)
        # Per-voice soft-knee saturation: linear up to 0.8*amp, gentle
        # rolloff toward ±amp. Independent of global clip_mode.
        if amp_val > 1e-6:
            wf = soft_knee_saturate(wf, amp_val, knee=0.8)
        active_wf.append(wf)

    if not active_wf:
        return np.zeros(SAMPLES)

    combined = sl_gamp.val * np.sum(active_wf, axis=0) + sl_gdc.val
    combined  = apply_clip(combined, sl_clip_thresh.val, clip_mode)

    if quant_enabled:
        voct = quantize_to_scale(
            combined,
            root_note=int(sl_qroot.val),
            scale_degrees=SCALE_VALUES[int(sl_qscale.val)],
            octave_range=int(sl_qrange.val),
        )
        # Post-quantizer transpose in semitones
        voct = voct + int(sl_qtranspose.val) / 12.0
    else:
        voct = quantize_to_scale(
            combined, root_note=0,
            scale_degrees=SCALES['chromatic'],
            octave_range=int(sl_qrange.val),
        )
    return voct


# ===============================================================================
#  State
# ===============================================================================

class VoiceState:
    def __init__(self, on=True, show=True):
        self.on   = on
        self.show = show

voice_states    = [VoiceState() for _ in range(4)]
voice_hz_modes  = [False] * 4   # True = free Hz mode, False = ratio/clock mode
quant_enabled   = False
clip_mode       = 'soft'
clock_source    = 'internal'   # 'internal' or 'external'
slider_defaults = {}


# ===============================================================================
#  MIDI Clock Receiver
# ===============================================================================

class ClockReceiver:
    """Listens for MIDI clock ticks (0xF8) on all available input ports.

    Opens every existing physical/virtual MIDI input port at startup so
    that Ableton (or any DAW) can deliver clock to its own routing target
    (typically an IAC bus on macOS, loopMIDI on Windows) without the user
    needing to know our virtual port's name. Also opens a virtual port
    named 'CWV Clock In' as a fallback for hosts that auto-discover
    available destinations.

    Maintains a rolling BPM estimate from the last PPQN tick intervals
    (one quarter note of averaging). Returns None if clock has timed out.
    """

    def __init__(self):
        self._midiins    = []
        self._port_names = []
        self._lock       = threading.Lock()
        self._tick_times = collections.deque(maxlen=PPQN)
        self._last_tick  = None
        self._bpm_est    = None
        self.synced      = False

    def start(self):
        if not RTMIDI_AVAILABLE:
            return
        # Open every physical input port that's already available
        try:
            scanner = rtmidi.MidiIn()
            for port_idx, port_name in enumerate(scanner.get_ports()):
                try:
                    midi_in = rtmidi.MidiIn()
                    midi_in.ignore_types(sysex=True, timing=False, active_sense=True)
                    midi_in.open_port(port_idx)
                    midi_in.set_callback(self._on_message)
                    self._midiins.append(midi_in)
                    self._port_names.append(port_name)
                except Exception:
                    pass
            del scanner
        except Exception:
            pass
        # Always also open a virtual port so DAWs that auto-discover can find us
        try:
            virt = rtmidi.MidiIn()
            virt.ignore_types(sysex=True, timing=False, active_sense=True)
            virt.open_virtual_port('CWV Clock In')
            virt.set_callback(self._on_message)
            self._midiins.append(virt)
            self._port_names.append('CWV Clock In (virtual)')
        except Exception:
            pass

    def stop(self):
        for mi in self._midiins:
            try:
                mi.cancel_callback()
                mi.close_port()
            except Exception:
                pass
        self._midiins    = []
        self._port_names = []
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

    @property
    def port_summary(self):
        """Human-readable summary of which ports are being watched."""
        return ', '.join(self._port_names) if self._port_names else 'no ports'


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

            # Read transport parameters fresh every step.
            # Clock source is user-selectable: 'internal' uses the BPM slider
            # exclusively; 'external' follows incoming MIDI clock and falls
            # back to the slider only if external clock has not been received.
            if clock_source == 'external':
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
            else:
                bpm = sl_bpm.val

            bars           = float(sl_bars.val)
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
SL_H     = 0.015
GAP      = 0.003
CHK_H    = 0.020
SECT_GAP = 0.005

P_LEFT  = 0.42
P_RIGHT = 0.97
P_WIDTH = P_RIGHT - P_LEFT


# ===============================================================================
#  Build figure
# ===============================================================================

fig = plt.figure(figsize=(17, 12), facecolor=BG_DARK)
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

# Clock source: Internal (BPM slider) vs External (incoming MIDI clock)
y -= 0.040
ax_clock_src = fig.add_axes([L_MARGIN, y, L_WIDTH, 0.030], facecolor=BG_DARK)
radio_clock_src = RadioButtons(ax_clock_src, ['Int', 'Ext'], active=0,
                               activecolor=MIDI_COLOR)
style_check(radio_clock_src, MIDI_COLOR)

y -= SL_H + GAP * 2
ax_bpm, sl_bpm = make_slider(y, 'BPM', 20, 300, GLOBAL_DEFAULTS['bpm'], 1)

y -= SL_H + GAP
ax_bars, sl_bars = make_slider(y, 'Bars', 0.5, 16, GLOBAL_DEFAULTS['bars'], 0.5)

y -= SL_H + GAP
ax_gamp, sl_gamp = make_slider(y, 'G.Amp', 0, 2.0, 1.0, 0.01)

y -= SL_H + GAP
ax_gdc, sl_gdc = make_slider(y, 'G.DC (V)', -5.0, 5.0, 0.0, 0.01)


# -- Quantizer -----------------------------------------------------------------

y -= SECT_GAP + 0.012
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

y -= SL_H + GAP
ax_qtranspose, sl_qtranspose = make_slider(y, 'Transpose', -24, 24,
                                           GLOBAL_DEFAULTS['qtranspose'],
                                           1, '#ff6b9d')
sl_qtranspose.valtext.set_text('0')


# -- Clipper -------------------------------------------------------------------

CLIP_COLOR = '#ff9f43'

y -= SECT_GAP + 0.014
add_section_label(y, '-- CLIPPER --', CLIP_COLOR)

y -= 0.026
ax_clip_mode = fig.add_axes([L_MARGIN, y, L_WIDTH, 0.026], facecolor=BG_DARK)
ax_clip_mode.set_xticks([]); ax_clip_mode.set_yticks([])
for spine in ax_clip_mode.spines.values():
    spine.set_visible(False)
# Custom horizontal radio: one label per cell, click toggles selection
clip_mode_cells = []
def _draw_clip_radio():
    ax_clip_mode.clear()
    ax_clip_mode.set_xticks([]); ax_clip_mode.set_yticks([])
    ax_clip_mode.set_xlim(0, 1); ax_clip_mode.set_ylim(0, 1)
    ax_clip_mode.set_facecolor(BG_DARK)
    for spine in ax_clip_mode.spines.values():
        spine.set_visible(False)
    cells = []
    n = len(CLIP_MODES)
    for i, m in enumerate(CLIP_MODES):
        cx = (i + 0.15) / n
        ax_clip_mode.plot([cx], [0.5], 'o', markersize=8,
                          markerfacecolor=CLIP_COLOR if m == clip_mode else 'none',
                          markeredgecolor=CLIP_COLOR, markeredgewidth=1.2,
                          clip_on=False)
        ax_clip_mode.text((i + 0.30) / n, 0.5, m, color=CLIP_COLOR,
                          fontsize=9, va='center', ha='left')
        cells.append((i / n, (i + 1) / n, m))
    return cells
clip_mode_cells = _draw_clip_radio()

y -= SL_H + GAP + 0.004
ax_clip_thresh, sl_clip_thresh = make_slider(
    y, 'G.Thresh', 0.1, 10.0, GLOBAL_DEFAULTS['clip_thresh'], 0.05, CLIP_COLOR
)


# -- MIDI Out ------------------------------------------------------------------

y -= SECT_GAP + 0.016
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

# Transport: Play/Pause  Reset  — extra vertical gap below Velocity
y -= SECT_GAP + 0.034
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

y -= 0.020
midi_status_text = fig.text(
    L_MARGIN + L_WIDTH / 2, y, 'MIDI off',
    ha='center', fontsize=8, color='#888888', style='italic',
)
y -= 0.014
clock_status_text = fig.text(
    L_MARGIN + L_WIDTH / 2, y, 'Int',
    ha='center', fontsize=8, color='#888888', style='italic',
)


# -- Voices --------------------------------------------------------------------

voice_sliders = []

for v in range(4):
    d     = VOICE_DEFAULTS[v]
    vs    = {}
    color = VOICE_COLORS[v]

    y -= SECT_GAP + 0.004
    add_section_label(y, f'-- VOICE {v+1} --', color)

    y -= CHK_H + GAP
    # Three separate checkbox widgets laid out horizontally to avoid
    # matplotlib's vertical stacking overlap when options share one axes.
    chk_w = L_WIDTH / 3.0
    ax_chk_on = fig.add_axes([L_MARGIN, y, chk_w, CHK_H], facecolor=BG_DARK)
    chk_on = CheckButtons(ax_chk_on, ['On'], [True])
    style_check(chk_on, color)
    ax_chk_show = fig.add_axes([L_MARGIN + chk_w, y, chk_w, CHK_H], facecolor=BG_DARK)
    chk_show = CheckButtons(ax_chk_show, ['Show'], [True])
    style_check(chk_show, color)
    ax_chk_hz = fig.add_axes([L_MARGIN + 2 * chk_w, y, chk_w, CHK_H], facecolor=BG_DARK)
    chk_hz = CheckButtons(ax_chk_hz, ['Hz'], [False])
    style_check(chk_hz, color)
    vs['chk_on']   = chk_on
    vs['chk_show'] = chk_show
    vs['chk_hz']   = chk_hz

    y -= SL_H + GAP
    _, vs['shape'] = make_slider(y, 'Shape', 0, len(SHAPES) - 1, d['shape'], 1, color)
    vs['shape'].valtext.set_text(SHAPES[d['shape']])

    # Ratio slider (clock multiple mode)
    y -= SL_H + GAP
    ax_ratio, vs['ratio'] = make_slider(y, 'Ratio', 0, len(RATIO_LABELS) - 1,
                                        d['ratio'], 1, color)
    vs['ratio'].valtext.set_text(RATIO_LABELS[d['ratio']])
    vs['ax_ratio'] = ax_ratio

    # Hz slider (free Hz mode) — exponential taper.
    # Slider value is log10(Hz); helper voice_hz() returns the actual Hz.
    # Range: 0.1 Hz (log -1) to 2000 Hz (log ~3.301).
    ax_hz = fig.add_axes([L_MARGIN, y, L_WIDTH, SL_H], facecolor=BG_CTRL)
    HZ_LOG_MIN = -1.0       # 10^-1  = 0.1 Hz
    HZ_LOG_MAX = np.log10(2000.0)
    sl_hz = Slider(ax_hz, 'Hz', HZ_LOG_MIN, HZ_LOG_MAX,
                   valinit=np.log10(VOICE_HZ_DEFAULT),
                   valstep=None, color=color, initcolor='none')
    style_slider(sl_hz)
    # Display actual Hz in the value text rather than log10(Hz)
    def _make_hz_fmt(slider):
        def _fmt(v=None):
            hz = 10.0 ** slider.val
            if hz >= 100:
                slider.valtext.set_text(f'{hz:.0f}')
            elif hz >= 10:
                slider.valtext.set_text(f'{hz:.1f}')
            else:
                slider.valtext.set_text(f'{hz:.2f}')
        return _fmt
    _hz_fmt = _make_hz_fmt(sl_hz)
    _hz_fmt()
    sl_hz.on_changed(lambda v, f=_hz_fmt: f())
    slider_defaults[sl_hz] = np.log10(VOICE_HZ_DEFAULT)
    ax_hz.set_visible(False)
    vs['hz'] = sl_hz
    vs['ax_hz'] = ax_hz

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

    bpm      = sl_bpm.val
    bars     = float(sl_bars.val)

    complex_s   = (60.0 / bpm) * bars * 4.0
    total_beats = bars * 4.0

    t = np.linspace(0, complex_s, SAMPLES, endpoint=False)
    x = np.linspace(0, total_beats, SAMPLES, endpoint=False)

    ax_comb.set_xlabel('Beats', fontsize=10, color=TEXT_CLR)

    active_wf = []
    for i in range(4):
        vs = voice_sliders[i]
        st = voice_states[i]
        if st.on:
            amp_val = vs['amp'].val
            shape   = SHAPES[int(vs['shape'].val)]
            phase   = vs['phase'].val
            if voice_hz_modes[i]:
                wf = generate_voice_hz(shape, 10.0 ** vs['hz'].val, amp_val, phase, t)
            else:
                wf = generate_voice(shape, RATIO_VALUES[int(vs['ratio'].val)],
                                    amp_val, phase, complex_s, t)
            # Per-voice soft-knee saturation (decoupled from global mode)
            if amp_val > 1e-6:
                wf = soft_knee_saturate(wf, amp_val, knee=0.8)
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
        combined  = apply_clip(combined, sl_clip_thresh.val, clip_mode)
        if quant_enabled:
            root_name  = NOTE_NAMES[int(sl_qroot.val)]
            scale_name = SCALE_NAMES[int(sl_qscale.val)]
            q = quantize_to_scale(
                combined,
                root_note=int(sl_qroot.val),
                scale_degrees=SCALE_VALUES[int(sl_qscale.val)],
                octave_range=int(sl_qrange.val),
            )
            # Post-quantizer transpose
            q = q + int(sl_qtranspose.val) / 12.0
            quantized_line.set_data(x, q)
            quantized_line.set_visible(True)
            combined_line.set_data([], [])
            combined_line.set_visible(False)
            transpose_label = f'  T{int(sl_qtranspose.val):+d}st' if sl_qtranspose.val != 0 else ''
            ax_comb.set_ylabel(f'V/oct  (0V={root_name}4, {scale_name}{transpose_label})',
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

    bars        = float(sl_bars.val)
    total_beats = bars * 4.0

    phase    = midi_engine.phase
    beat_pos = phase * total_beats

    playhead_line.set_xdata([beat_pos, beat_pos])
    playhead_line.set_visible(True)

    # Clock status reflects the user's chosen source.
    synced_bpm = clock_rx.bpm
    if clock_source == 'external':
        if synced_bpm is not None:
            clock_status_text.set_text(f'Ext: {synced_bpm:.1f} BPM')
            clock_status_text.set_color(MIDI_COLOR)
        else:
            clock_status_text.set_text('Ext: waiting...')
            clock_status_text.set_color('#e74c3c')
    else:
        if synced_bpm is not None:
            # Internal selected, but external clock is incoming: surface that
            # so the user knows the source is available if they want it.
            clock_status_text.set_text(f'Int  (Ext avail: {synced_bpm:.0f})')
            clock_status_text.set_color('#888888')
        else:
            clock_status_text.set_text('Int')
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

def on_clock_source_change(label):
    global clock_source
    clock_source = 'external' if label == 'Ext' else 'internal'
    midi_engine.mark_dirty()
radio_clock_src.on_clicked(on_clock_source_change)

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

# Rescale Y removed in v3.2

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

sl_qtranspose.on_changed(_on_waveform_change)

# Clipper - custom horizontal radio click handler
def on_clip_mode_click(event):
    global clip_mode
    if event.inaxes is not ax_clip_mode:
        return
    if event.xdata is None:
        return
    for x0, x1, mode_name in clip_mode_cells:
        if x0 <= event.xdata <= x1:
            if mode_name != clip_mode:
                clip_mode = mode_name
                _draw_clip_radio()
                midi_engine.mark_dirty()
                update()
            break
fig.canvas.mpl_connect('button_press_event', on_clip_mode_click)
sl_clip_thresh.on_changed(_on_waveform_change)

# Per-voice
def make_voice_on_cb(idx):
    def cb(label):
        voice_states[idx].on = not voice_states[idx].on
        midi_engine.mark_dirty()
        update()
    return cb

def make_voice_show_cb(idx):
    def cb(label):
        voice_states[idx].show = not voice_states[idx].show
        midi_engine.mark_dirty()
        update()
    return cb

def make_voice_hz_cb(idx):
    def cb(label):
        vs = voice_sliders[idx]
        voice_hz_modes[idx] = not voice_hz_modes[idx]
        vs['ax_ratio'].set_visible(not voice_hz_modes[idx])
        vs['ax_hz'].set_visible(voice_hz_modes[idx])
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
    vs['chk_on'].on_clicked(make_voice_on_cb(i))
    vs['chk_show'].on_clicked(make_voice_show_cb(i))
    vs['chk_hz'].on_clicked(make_voice_hz_cb(i))
    vs['shape'].on_changed(make_shape_changed(i))
    vs['ratio'].on_changed(make_ratio_changed(i))
    vs['hz'].on_changed(_on_waveform_change)
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
