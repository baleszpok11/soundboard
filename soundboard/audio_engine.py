"""Audio mixing: microphone plus triggered clips into the output device."""

import collections
import os
import queue
import threading
import time

import numpy as np
try:
    import sounddevice as sd
except OSError as e:
    # sounddevice bundles PortAudio only on Windows and macOS.
    sd = None
    PORTAUDIO_ERROR = e
else:
    PORTAUDIO_ERROR = None
import soundfile as sf


_scipy_signal = None


def scipy_signal():
    """scipy.signal, imported the first time something filters rather
    than at startup.

    It is the largest single cost of starting the app - more than half
    of the import time, and it drags in scipy.stats and scipy.interpolate
    on the way, neither of which anything here uses - and nothing that
    draws the window needs a filter. Nobody reaches it from the audio
    callback without the engine having warmed it first: half a second
    inside a callback is a dropout, so set_mic_effect() does the import
    while the effect is being chosen.
    """
    global _scipy_signal
    if _scipy_signal is None:
        import scipy.signal
        _scipy_signal = scipy.signal
    return _scipy_signal


SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 1024
MIC_TARGET_FRAMES = SAMPLE_RATE // 10  # 100 ms cushion against uneven mic delivery
MIC_MAX_FRAMES = SAMPLE_RATE // 2  # drop older mic audio beyond 500 ms
CLIP_CACHE_BYTES = 512 * 1024 * 1024  # decoded clips kept in memory
LIMITER_CEILING = 0.89  # about -1 dBFS
# The ramp a seek fades back in over: long enough to cover the step, far
# too short to hear as a fade.
SEEK_RAMP_FRAMES = SAMPLE_RATE // 200  # 5 ms
LIMITER_RELEASE_S = 0.3
# Ducking the mic under a clip. Down fast enough that the voice is out of
# the way before the clip is audible, back up slowly enough that a board
# full of short clips does not pump the voice in and out.
DUCK_ATTACK_S = 0.06
DUCK_RELEASE_S = 0.35
STOP_FADE_S = 0.08  # default length of the stop fade, once switched on
STOP_FADE_MIN_S = 0.05
STOP_FADE_MAX_S = 2.0
DUCK_MIN_DB = 3
DUCK_MUTE_DB = 40  # the top of the control is a real mute, not 40 dB down
STREAM_TIMEOUT_S = 2.0  # a running stream with no callbacks for this long is treated as lost
# Live mic effects. Only what can be done a block at a time belongs here;
# anything that needs the whole clip stays in dsp.py and the editor.
MIC_EFFECTS = ("none", "telephone", "robot", "drive", "pitch")
# Pitch: the slider's 0 to 1 spans this either way, with the middle at no
# shift, so one control does up and down.
MIC_PITCH_SEMITONES = 12.0
# The shifter's grain. Long enough that a voice keeps its body, short
# enough that the delay it adds is not something to talk over: a tap is
# read up to this far behind the input.
MIC_PITCH_WINDOW_S = 0.045
# Rumble, handling noise and the bottom of a desk fan, none of which a
# voice needs. Second order: enough to take it out, gentle enough that
# nothing above it is coloured.
MIC_HIGHPASS_HZ = 80.0
MIC_HIGHPASS_ORDER = 2
# The gate. Peak-based detection, because the start of a word has to open
# it: an RMS over a block is already late. Held open after the last peak
# so a word's tail is not cut, then let down over the release.
GATE_MIN_DB, GATE_MAX_DB = -70, -20
GATE_ATTACK_S = 0.005
GATE_HOLD_S = 0.12
GATE_RELEASE_S = 0.25
MIC_PHONE_BAND_HZ = (300.0, 3000.0)
# An integer number of cycles per second, so wrapping the oscillator at
# one second leaves the sine continuous.
MIC_ROBOT_HZ = 80.0
MIC_DRIVE_DB = 18.0
REPLAY_S = 30  # how much of the mic the replay buffer holds
RECORD_MAX_S = 180  # a forgotten recording stops here instead of filling memory
RECORD_MIN_S = 0.2  # shorter than this is a mis-click, not a clip
TEST_TONE_S = 0.8
TEST_TONE_HZ = 440.0
TEST_TONE_LEVEL = 0.4
TEST_TONE_FADE_S = 0.05


def pitch_semitones(amount):
    """The mic effect slider as semitones: the middle is no shift, each
    end is MIC_PITCH_SEMITONES away from it. One control covers both
    directions, which is what the picker's single slider can offer."""
    return (min(1.0, max(0.0, amount)) * 2.0 - 1.0) * MIC_PITCH_SEMITONES


def duck_depth(amount_db):
    """How far down the mic goes for a setting in dB. The top of the
    range silences it outright rather than leaving the 1% a literal
    -40 dB would: that end of the control is Soundpad's "block voice",
    and it should mean what it says."""
    if amount_db >= DUCK_MUTE_DB:
        return 1.0
    return 1.0 - 10 ** (-amount_db / 20)


def _resample(data, orig_sr, target_sr):
    if orig_sr == target_sr or len(data) == 0:
        return data
    target_len = int(round(data.shape[0] * target_sr / orig_sr))
    orig_idx = np.arange(data.shape[0])
    target_idx = np.linspace(0, data.shape[0] - 1, num=target_len)
    resampled = np.empty((target_len, data.shape[1]), dtype=np.float32)
    for ch in range(data.shape[1]):
        resampled[:, ch] = np.interp(target_idx, orig_idx, data[:, ch])
    return resampled


def _match_channels(data, channels):
    if data.shape[1] == channels:
        return data
    if data.shape[1] == 1:
        return np.repeat(data, channels, axis=1)
    if channels == 1:
        return data.mean(axis=1, keepdims=True).astype(np.float32)
    return data[:, :channels]


def test_tone(seconds=TEST_TONE_S, hz=TEST_TONE_HZ):
    """A short sine for checking that audio reaches the output device.
    Faded at both ends so it can't click."""
    frames = int(SAMPLE_RATE * seconds)
    t = np.arange(frames, dtype=np.float32) / SAMPLE_RATE
    wave = TEST_TONE_LEVEL * np.sin(2 * np.pi * hz * t).astype(np.float32)
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / TEST_TONE_FADE_S).astype(np.float32)
    return (wave * fade).reshape(-1, 1).repeat(CHANNELS, axis=1)


class Fades(collections.namedtuple("Fades", "fade_in fade_out crossfade")):
    """Per-sound fade times in seconds. All zero is what a board without
    any of these keys gets, and is exactly the old behaviour."""

    __slots__ = ()

    def __new__(cls, fade_in=0.0, fade_out=0.0, crossfade=0.0):
        return super().__new__(cls, max(0.0, fade_in), max(0.0, fade_out),
                               max(0.0, crossfade))

    @property
    def idle(self):
        return not (self.fade_in or self.fade_out or self.crossfade)


NO_FADES = Fades()


class _ActiveSound:
    """One clip on its way to the output, with its own envelope.

    Three things shape it, and they multiply: the fade in over the first
    `fade_in` seconds, the fade out over the last `fade_out` seconds, and
    the fade that a stop asks for, which can begin at any point. A
    looping clip fades in once rather than on every pass, and never fades
    out, because it has no end to fade at - the seam is what it gets
    instead.
    """

    def __init__(self, data, key=None, gain=1.0, loop=False, fades=NO_FADES):
        self.data = data
        self.key = key
        self.gain = gain
        self.loop = loop
        self.position = 0
        self.played = 0  # frames emitted, counted across loop passes
        self.stopping = False
        self.paused = False
        # A seek jumps into the middle of a waveform, which is a step in
        # the signal and a click. A few milliseconds of ramp covers it.
        self._ramp_total = 0
        self._ramp_left = 0
        self._fade_in = int(fades.fade_in * SAMPLE_RATE)
        self._fade_out = int(fades.fade_out * SAMPLE_RATE)
        self._stop_total = 0
        self._stop_left = 0
        self._passes = 0
        # The loop seam: the clip's tail faded into its head, played in
        # place of the head on every pass but the first, so a loop that
        # was not cut at a zero crossing does not tick once a bar. Only
        # the overlap is kept, not a second copy of the clip.
        self._seam = None
        self._body = len(data)
        if loop and fades.crossfade > 0 and len(data) > 1:
            overlap = min(int(fades.crossfade * SAMPLE_RATE), len(data) // 2)
            if overlap > 0:
                self._body = len(data) - overlap
                ramp = np.linspace(0.0, 1.0, overlap, dtype=np.float32)[:, None]
                tail = data[self._body:self._body + overlap]
                self._seam = (tail * (1.0 - ramp) + data[:overlap] * ramp).astype(np.float32)

    def begin_stop(self, frames):
        """Start fading out towards silence over this many frames. The
        mixer keeps reading until the envelope lands, so a stop is a fade
        rather than a cut; zero frames still stops it dead."""
        if self.stopping:
            return  # a second stop must not restart the fade
        self.stopping = True
        self._stop_total = max(0, frames)
        self._stop_left = self._stop_total

    def seek(self, fraction):
        """Jump to a fraction of the clip, 0 to 1. Keeps playing from
        there rather than restarting: the clip is already decoded and in
        the mix, and a restart would reload it and lose the loop pass it
        is on."""
        if not len(self.data):
            return
        position = int(min(1.0, max(0.0, fraction)) * len(self.data))
        self.position = min(position, max(0, self._body - 1))
        self._ramp_total = self._ramp_left = SEEK_RAMP_FRAMES

    def read(self, frames):
        channels = self.data.shape[1]
        if not len(self.data):
            # An empty clip has nothing to wrap around, so looping it
            # would spin forever.
            return np.zeros((frames, channels), dtype=np.float32), True
        if self.paused and not self.stopping:
            # Silence, and the position stays where it is. The clip is
            # still in the mix, so resuming is a flag rather than a
            # reload.
            return np.zeros((frames, channels), dtype=np.float32), False
        if self.stopping and self._stop_left <= 0:
            return np.zeros((frames, channels), dtype=np.float32), True
        start_played, start_position = self.played, self.position
        chunk = self._pull(frames)
        finished = not self.loop and self.position >= len(self.data)
        if len(chunk):
            envelope = self._envelope(len(chunk), start_played, start_position)
            chunk = chunk * envelope if envelope is not None else chunk * self.gain
        self.played += len(chunk)
        if self._ramp_left > 0:
            self._ramp_left -= len(chunk)
        if self.stopping:
            self._stop_left -= len(chunk)
            finished = finished or self._stop_left <= 0
        if len(chunk) < frames:
            pad = np.zeros((frames - len(chunk), channels), dtype=np.float32)
            chunk = np.vstack([chunk, pad]) if len(chunk) else pad
        return chunk, finished

    def _pull(self, frames):
        """The audio itself, wrapping a looping clip as many times as the
        block needs. Advances `position`; the envelope is applied by the
        caller, which kept the position this started at."""
        parts = []
        needed = frames
        while needed > 0:
            end = min(self.position + needed, self._body)
            parts.append(self._piece(self.position, end))
            needed -= end - self.position
            self.position = end
            if self.position < self._body:
                continue
            if not self.loop:
                break
            self.position = 0  # wrap and keep filling the same block
            self._passes += 1
        channels = self.data.shape[1]
        if not parts:
            return np.zeros((0, channels), dtype=np.float32)
        return np.concatenate(parts) if len(parts) > 1 else parts[0]

    def _piece(self, start, end):
        """Clip audio from `start` to `end`, with the crossfaded seam in
        place of the head once the clip has wrapped at least once."""
        if self._seam is None or not self._passes or start >= len(self._seam):
            return self.data[start:end]
        seamed = self._seam[start:min(end, len(self._seam))]
        if end <= len(self._seam):
            return seamed
        return np.concatenate([seamed, self.data[len(self._seam):end]])

    def _envelope(self, n, start_played, start_position):
        """The block's gain, or None when nothing shapes it and the flat
        gain will do. Multiplied together so a stop lands on top of
        whatever fade was already running."""
        envelope = None
        if self._fade_in and start_played < self._fade_in:
            steps = (start_played + np.arange(n, dtype=np.float32)) / self._fade_in
            envelope = np.clip(steps, 0.0, 1.0)
        # A looping clip has no end to fade out at; the seam covers its wrap.
        if self._fade_out and not self.loop:
            left = len(self.data) - (start_position + np.arange(n, dtype=np.float32))
            out = np.clip(left / self._fade_out, 0.0, 1.0)
            envelope = out if envelope is None else envelope * out
        if self._ramp_total and self._ramp_left > 0:
            done = (self._ramp_total - self._ramp_left) + np.arange(n, dtype=np.float32)
            ramp = np.clip(done / self._ramp_total, 0.0, 1.0)
            envelope = ramp if envelope is None else envelope * ramp
        if self.stopping and self._stop_total:
            left = self._stop_left - np.arange(n, dtype=np.float32)
            stop = np.clip(left / self._stop_total, 0.0, 1.0)
            envelope = stop if envelope is None else envelope * stop
        if envelope is None:
            return None
        return (envelope * self.gain).astype(np.float32)[:, None]


class _Limiter:
    """Block-based peak limiter. Gain drops instantly when a block would
    exceed the ceiling and recovers smoothly afterwards, so loud mixes
    get quieter instead of distorting."""

    def __init__(self):
        self.gain = 1.0
        self.peak = 0.0  # last block's peak, reused by the output meter
        blocks_per_second = SAMPLE_RATE / BLOCK_SIZE
        self.release = 1.0 - np.exp(-1.0 / (LIMITER_RELEASE_S * blocks_per_second))

    def process(self, block):
        peak = float(np.abs(block).max()) if block.size else 0.0
        self.peak = peak
        target = min(1.0, LIMITER_CEILING / peak) if peak > 0 else 1.0
        if target < self.gain:
            # A downward ramp would let the start of the block overshoot.
            self.gain = target
            block *= target
        else:
            new_gain = self.gain + (target - self.gain) * self.release
            block *= np.linspace(self.gain, new_gain, len(block), dtype=np.float32)[:, None]
            self.gain = new_gain
        np.clip(block, -1.0, 1.0, out=block)
        return block


class _Ducker:
    """Pulls the microphone down while a clip is playing and lets it back
    up once nothing is. Block-based like the limiter, and asymmetric for
    the same reason it is: the attack has to beat the clip it is making
    room for, while the release is what stops the voice pumping.

    `depth` is how far down the mic goes, 0 for off and 1 for silent, so
    Soundpad's "block voice" is just the top of the one control."""

    def __init__(self):
        self.depth = 0.0
        self.gain = 1.0
        blocks_per_second = SAMPLE_RATE / BLOCK_SIZE
        self._attack = 1.0 - np.exp(-1.0 / (DUCK_ATTACK_S * blocks_per_second))
        self._release = 1.0 - np.exp(-1.0 / (DUCK_RELEASE_S * blocks_per_second))

    def process(self, block, ducking):
        target = 1.0 - self.depth if ducking else 1.0
        if target == 1.0 and self.gain == 1.0:
            return block  # switched off, or already all the way back up
        rate = self._attack if target < self.gain else self._release
        new_gain = self.gain + (target - self.gain) * rate
        # Ramp across the block rather than stepping at its edge, which
        # is the same thing the mic mute and the limiter do.
        block *= np.linspace(self.gain, new_gain, len(block), dtype=np.float32)[:, None]
        self.gain = new_gain
        return block


class _MicCleanup:
    """What the microphone gets before any effect: a high-pass to drop
    rumble, and a gate to drop the room between words.

    Both are block-sized and keep their state across blocks, like the
    ducker and the limiter. The gain is ramped from the last block's
    value rather than stepped, so opening and closing the gate has
    nothing to click.
    """

    def __init__(self):
        self.gate_on = False
        self.threshold_db = -45.0
        self.highpass_on = True
        self._gain = 1.0
        self._hold_left = 0.0
        self._sos = None
        self._zi = None
        self._channels = 0

    def set(self, gate_on, threshold_db, highpass_on):
        """Caller holds the engine lock."""
        self.gate_on = bool(gate_on)
        self.threshold_db = float(threshold_db)
        self.highpass_on = bool(highpass_on)

    def process(self, block):
        if len(block):
            block = self._highpass(block)
            block = self._gate(block)
        return block

    def _highpass(self, block):
        if not self.highpass_on:
            return block
        # Without blocking for it: scipy is fetched in the background at
        # startup, and the first blocks after launch go through unfiltered
        # rather than making the callback wait half a second for it.
        sig = _scipy_signal
        if sig is None:
            return block
        channels = block.shape[1]
        if self._sos is None or channels != self._channels:
            self._sos = sig.butter(MIC_HIGHPASS_ORDER, MIC_HIGHPASS_HZ,
                                   btype="high", fs=SAMPLE_RATE, output="sos")
            self._channels = channels
            # Settled on the first sample, so the filter does not thump
            # while it fills.
            self._zi = (sig.sosfilt_zi(self._sos)[:, :, None] * block[0]).astype(np.float32)
        out = np.empty_like(block)
        for ch in range(channels):
            out[:, ch], self._zi[:, :, ch] = sig.sosfilt(
                self._sos, block[:, ch], zi=self._zi[:, :, ch])
        return out

    def _gate(self, block):
        if not self.gate_on:
            self._gain = 1.0
            self._hold_left = 0.0
            return block
        frames = len(block)
        seconds = frames / SAMPLE_RATE
        peak = float(np.abs(block).max())
        above = peak > 10 ** (self.threshold_db / 20)
        if above:
            self._hold_left = GATE_HOLD_S
        else:
            self._hold_left = max(0.0, self._hold_left - seconds)
        target = 1.0 if above or self._hold_left > 0 else 0.0
        # Open fast enough for the front of a word, close slowly enough
        # that the tail of one is not chopped off. The gain moves at a
        # fixed rate rather than by a fraction of what is left, so the
        # attack and release times above are how long it actually takes -
        # and closing reaches silence rather than creeping towards it.
        step = seconds / (GATE_ATTACK_S if target > self._gain else GATE_RELEASE_S)
        if target > self._gain:
            gain = min(target, self._gain + step)
        else:
            gain = max(target, self._gain - step)
        if gain == self._gain:
            block = block * gain if gain != 1.0 else block
        else:
            block = block * np.linspace(self._gain, gain, frames, dtype=np.float32)[:, None]
            self._gain = gain
        return block.astype(np.float32)


class _PitchShifter:
    """A live pitch shift: two taps into a ring of recent input, read at
    the shifted rate and crossfaded where they wrap.

    Not the phase vocoder dsp.py uses. That works on a whole clip and
    takes as long as it takes; this has to hand back a block before the
    card asks for the next one, which rules it out. Two taps half a
    window apart, with an equal-power crossfade, is the cheap shifter
    that has been in hardware since the eighties: a few numpy operations
    per block, and the sound of it is a voice, which is what it is for.
    """

    def __init__(self, semitones=0.0):
        self.semitones = semitones
        self._window = max(2, int(MIC_PITCH_WINDOW_S * SAMPLE_RATE))
        self._history = None
        self._phase = 0.0
        # The ring starts empty, so a tap crossing the end of the silence
        # would step straight into the signal. The fade goes on the way
        # in rather than on the way out: what is stored rises from
        # nothing, so wherever a tap reads there is no edge to hit.
        self._filling = self._window

    def process(self, block):
        frames, channels = block.shape
        if not frames:
            return block
        rate = 2.0 ** (self.semitones / 12.0)
        if self._history is None or self._history.shape[1] != channels:
            self._history = np.zeros((self._window + frames, channels), dtype=np.float32)
            self._filling = self._window
        if self._filling > 0:
            filled = min(frames, self._filling)
            done = (self._window - self._filling) + np.arange(filled, dtype=np.float32)
            block = block.copy()
            block[:filled] *= np.clip(done / self._window, 0.0, 1.0)[:, None]
            self._filling -= filled
        history = np.vstack([self._history, block])[-(self._window + frames):]
        self._history = history

        # Where each tap reads, as a delay that walks the window and
        # wraps: the wrap is the seam the second tap covers.
        t = np.arange(frames, dtype=np.float64)
        u = np.mod(self._phase + (1.0 - rate) * t / self._window, 1.0)
        self._phase = float(np.mod(self._phase + (1.0 - rate) * frames / self._window, 1.0))
        newest = len(history) - frames  # index in history of this block's first frame
        a = newest + t - u * self._window
        b = newest + t - np.mod(u + 0.5, 1.0) * self._window
        # The two gains sum to one rather than holding equal power: both
        # taps are reading the same voice a moment apart, so an
        # equal-power pair sums 3 dB hot instead of holding the level.
        gain_a = np.square(np.sin(np.pi * u)).astype(np.float32)
        gain_b = (1.0 - gain_a).astype(np.float32)
        index = np.arange(len(history), dtype=np.float64)
        out = np.empty_like(block)
        for ch in range(channels):
            column = history[:, ch]
            out[:, ch] = (np.interp(a, index, column) * gain_a
                          + np.interp(b, index, column) * gain_b)
        return out


class _MicEffectChain:
    """The live mic effects, applied a block at a time inside the output
    callback. Filter state and the oscillator's phase carry over between
    blocks, or every block boundary would click, and changing effect or
    amount is crossfaded over a block for the same reason: the mix falls
    to zero, the new effect is put in place, and the mix comes back up."""

    def __init__(self):
        self.name = "none"
        self.amount = 0.0  # 0 to 1
        self._active = "none"  # what the state below belongs to
        self._mix = 0.0
        self._zi = None
        self._sos = None
        self._channels = 0
        self._phase = 0
        self._pitch = None

    def set(self, name, amount):
        """Caller holds the engine lock, so the callback never sees half
        a change."""
        self.name = name if name in MIC_EFFECTS else "none"
        self.amount = min(1.0, max(0.0, amount))

    @property
    def idle(self):
        return self.name == "none" and self._active == "none" and self._mix == 0.0

    def process(self, block):
        if self.idle:
            return block
        target = 0.0 if self.name == "none" else self.amount
        if self.name == "pitch":
            # The slider is semitones here, not a wet mix: half a shifted
            # voice under the unshifted one is two people talking.
            target = 1.0
        if self.name != self._active:
            target = 0.0  # fade the old one out before swapping
        wet = self._wet(block) if self._active != "none" else block
        if self._mix != target:
            ramp = np.linspace(self._mix, target, len(block), dtype=np.float32)[:, None]
            block = block * (1.0 - ramp) + wet * ramp
            self._mix = target
        else:
            block = block * (1.0 - self._mix) + wet * self._mix
        if self._mix == 0.0 and self.name != self._active:
            self._swap(self.name)
        return block

    def _swap(self, name):
        self._active = name
        self._zi = None
        self._sos = None
        self._phase = 0
        self._pitch = None

    def _wet(self, block):
        if self._active == "telephone":
            return self._telephone(block)
        if self._active == "robot":
            return self._robot(block)
        if self._active == "drive":
            return np.tanh(block * 10 ** (MIC_DRIVE_DB / 20)).astype(np.float32)
        if self._active == "pitch":
            return self._pitch_shift(block)
        return block

    def _pitch_shift(self, block):
        if self._pitch is None:
            self._pitch = _PitchShifter()
        self._pitch.semitones = pitch_semitones(self.amount)
        return self._pitch.process(block)

    def _telephone(self, block):
        channels = block.shape[1]
        sig = scipy_signal()
        if self._sos is None or channels != self._channels:
            low, high = MIC_PHONE_BAND_HZ
            self._sos = sig.butter(4, [low, high], btype="band", fs=SAMPLE_RATE, output="sos")
            self._channels = channels
            self._zi = None
        if self._zi is None:
            if not len(block):
                return block
            # Start each section settled on the first sample, so the
            # filter doesn't thump while it fills. (sections, 2, channels)
            self._zi = (sig.sosfilt_zi(self._sos)[:, :, None] * block[0]).astype(np.float32)
        out = np.empty_like(block)
        for ch in range(channels):
            out[:, ch], self._zi[:, :, ch] = sig.sosfilt(
                self._sos, block[:, ch], zi=self._zi[:, :, ch])
        return out

    def _robot(self, block):
        frames = len(block)
        t = (self._phase + np.arange(frames, dtype=np.float32)) / SAMPLE_RATE
        self._phase = (self._phase + frames) % SAMPLE_RATE
        return (block * np.sin(2 * np.pi * MIC_ROBOT_HZ * t, dtype=np.float32)[:, None])


class AudioEngine:
    """Continuously mixes the selected microphone with triggered sound
    clips and writes the result to the selected output device, so voice
    and soundboard clips are heard together on the virtual mic. Can also
    mirror sound clips (not the mic) to a local monitor device so you
    can hear what's playing yourself, independently mutable."""

    def __init__(self):
        self._lock = threading.Lock()
        self.input_stream = None
        self.output_stream = None
        self.monitor_stream = None
        self.monitor_muted = False
        self.input_channels = CHANNELS
        self.output_channels = CHANNELS
        self.monitor_channels = CHANNELS
        self._mic_buffer = collections.deque()
        self._mic_frames = 0
        self._mic_ready = False
        # Raw mic blocks kept while recording a clip: None when idle, so
        # the input callback can tell the two apart in one check.
        self._recording = None
        self._recording_frames = 0
        self._recording_channels = 0
        # The replay buffer: the last REPLAY_S of mic audio, kept so a
        # clip can be saved after the thing worth keeping has happened.
        self.replay_enabled = False
        self._replay = collections.deque()
        self._replay_frames = 0
        self._replay_channels = 0
        self._active_sounds = []
        self._active_sounds_monitor = []
        self.dropouts = 0
        # Last block's peak on each side, for the level meters. Written
        # from the audio callbacks, read by the UI timer.
        self.input_peak = 0.0
        self.output_peak = 0.0
        self.mic_gain = 1.0
        self.mic_enabled = True  # False while muted or push-to-talk isn't held
        self._mic_level = 1.0  # gain applied to the last block, for smooth changes
        self.sound_gain = 1.0
        # How long stop_all and stop_key take. 0 cuts, which is what a
        # board does until the setting is switched on.
        self.stop_fade_s = 0.0
        self.mic_effects = _MicEffectChain()
        self.mic_cleanup = _MicCleanup()
        self._ducker = _Ducker()
        self._output_limiter = _Limiter()
        self._monitor_limiter = _Limiter()
        self.on_error = None
        # Called with the key of a clip that ended by itself - not one
        # that was stopped, and not a loop, which has no end. It runs on
        # the audio thread, so what it is given has to hand the work
        # straight to the Tk thread.
        self.on_finished = None
        self._ended = []  # keys that ended in the block being mixed
        self._started_at = 0.0
        self._last_callback = {}
        self._clip_cache = collections.OrderedDict()
        self._clip_cache_bytes = 0
        self._cache_lock = threading.Lock()
        # Decoding runs here, not in the caller: pynput's keyboard hook
        # blocks every keypress on the system until its callback returns.
        self._requests = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def _device_channels(device, output):
        info = sd.query_devices(device)
        key = "max_output_channels" if output else "max_input_channels"
        return max(1, min(CHANNELS, info[key]))

    @staticmethod
    def _extra_settings(device):
        # WASAPI shared mode only accepts the device's own sample rate
        # unless Windows is allowed to convert.
        hostapi = sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"]
        if hostapi == "Windows WASAPI":
            return sd.WasapiSettings(auto_convert=True)
        return None

    def start(self, input_device, output_device, monitor_device):
        self.stop()
        self._started_at = time.monotonic()
        self._last_callback = {}
        self._mic_level = self.mic_gain if self.mic_enabled else 0.0
        if input_device is not None:
            self.input_channels = self._device_channels(input_device, output=False)
            self.input_stream = sd.InputStream(
                device=input_device,
                channels=self.input_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                latency="high",
                dtype="float32",
                extra_settings=self._extra_settings(input_device),
                callback=self._on_input,
            )
            self.input_stream.start()
        if output_device is not None:
            self.output_channels = self._device_channels(output_device, output=True)
            self.output_stream = sd.OutputStream(
                device=output_device,
                channels=self.output_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                latency="high",
                dtype="float32",
                extra_settings=self._extra_settings(output_device),
                callback=self._on_output,
            )
            self.output_stream.start()
        if monitor_device is not None:
            self.monitor_channels = self._device_channels(monitor_device, output=True)
            self.monitor_stream = sd.OutputStream(
                device=monitor_device,
                channels=self.monitor_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                latency="high",
                dtype="float32",
                extra_settings=self._extra_settings(monitor_device),
                callback=self._on_monitor_output,
            )
            self.monitor_stream.start()

    def stop(self):
        for attr in ("input_stream", "output_stream", "monitor_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass  # the device may already be gone
                setattr(self, attr, None)
        with self._lock:
            self._mic_buffer.clear()
            self._mic_frames = 0
            self._mic_ready = False
            self._active_sounds = []
            self._active_sounds_monitor = []
            self.dropouts = 0
            self.input_peak = 0.0
            self.output_peak = 0.0
            self._output_limiter = _Limiter()
            self._monitor_limiter = _Limiter()

    def lost_streams(self):
        """Names ("input", "output") of open streams that have stopped
        or stopped calling back, e.g. because the device was unplugged."""
        now = time.monotonic()
        lost = []
        for name in ("input", "output"):
            stream = getattr(self, f"{name}_stream")
            if stream is None:
                continue
            last = self._last_callback.get(name, self._started_at)
            if not stream.active or now - last > STREAM_TIMEOUT_S:
                lost.append(name)
        return lost

    def stop_all(self, fade_s=None):
        """Fade everything out rather than cutting it dead. The clips stay
        in the mix until their envelopes land, which is what makes a panic
        key sound like a fade. `fade_s` of 0 still stops them where they
        are, which is what a profile switch or a device change wants."""
        self._begin_stop(lambda sound: True, fade_s)

    def set_loop(self, key, loop):
        """Change looping on clips already playing, so switching it off
        doesn't leave one running until it's stopped by hand."""
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if sound.key == key:
                    sound.loop = loop

    def stop_key(self, key, fade_s=None):
        self._begin_stop(lambda sound: sound.key == key, fade_s)

    def _begin_stop(self, matches, fade_s):
        # None means the configured fade; the callers that want a clean
        # cut ask for 0 rather than knowing what the setting is.
        if fade_s is None:
            fade_s = self.stop_fade_s
        frames = int(max(0.0, fade_s) * SAMPLE_RATE)
        with self._lock:
            if not frames:
                # No fade asked for: drop them now, as this always did.
                self._active_sounds = [s for s in self._active_sounds if not matches(s)]
                self._active_sounds_monitor = [
                    s for s in self._active_sounds_monitor if not matches(s)]
                return
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if matches(sound):
                    sound.begin_stop(frames)

    def pause_key(self, key, paused=True):
        """Hold a playing clip where it is, or let it go on again. The
        clip stays in the mix either way, so nothing is decoded twice and
        the position is kept by the clip itself."""
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if sound.key == key and not sound.stopping:
                    sound.paused = paused

    def toggle_pause(self, key):
        """Pause or resume whatever is playing under this key. Returns
        the state it ended up in, or None when nothing is playing."""
        with self._lock:
            found = [s for s in (*self._active_sounds, *self._active_sounds_monitor)
                     if s.key == key and not s.stopping]
            if not found:
                return None
            paused = not found[0].paused
            for sound in found:
                sound.paused = paused
            return paused

    def pause_all(self, paused=True):
        """Every clip at once, for one key on the keyboard rather than
        one per sound."""
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if not sound.stopping:
                    sound.paused = paused

    def anything_playing(self):
        """Whether a clip is in the mix, paused or not."""
        with self._lock:
            return any(not s.stopping for s in
                       (*self._active_sounds, *self._active_sounds_monitor))

    def paused_keys(self):
        """The keys of the clips that are holding rather than playing."""
        with self._lock:
            return {s.key for s in (*self._active_sounds, *self._active_sounds_monitor)
                    if s.paused and not s.stopping and s.key is not None}

    def seek_key(self, key, fraction):
        """Move the clip playing under this key to a fraction of its
        length. Both copies move together, so the cable and the
        headphones stay in step."""
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if sound.key == key and not sound.stopping:
                    sound.seek(fraction)

    def active_keys(self):
        """Every key currently playing, mapped to how far that clip has
        got (0 to 1). One pass under the lock, so the board can poll all
        its entries at once."""
        progress = {}
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                # A clip that has been told to stop is already gone as far
                # as the board is concerned: its row should not keep
                # showing as playing for the length of the fade.
                if sound.key is None or sound.stopping or not len(sound.data):
                    continue
                fraction = min(1.0, sound.position / len(sound.data))
                if fraction > progress.get(sound.key, -1.0):
                    progress[sound.key] = fraction
        return progress

    def playback_progress(self, key):
        """How far the clip playing under this key has got, 0 to 1, or None
        when nothing with that key is playing."""
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if sound.key == key and not sound.stopping and len(sound.data):
                    return min(1.0, sound.position / len(sound.data))
        return None

    def set_mic_cleanup(self, gate_on, threshold_db, highpass_on):
        """Change the gate and the high-pass. Takes the lock so the
        callback never reads half a change."""
        with self._lock:
            self.mic_cleanup.set(gate_on, threshold_db, highpass_on)

    def set_mic_effect(self, name, amount):
        """Change the live mic effect. Takes the lock so the callback
        never reads a half-applied change."""
        if name == "telephone":
            # The one effect that filters. Import scipy here, on the
            # thread that picked it, rather than leaving the callback to
            # do it between two blocks.
            scipy_signal()
        with self._lock:
            self.mic_effects.set(name, amount)

    def _keep_for_replay(self, block):
        """Caller holds the lock. A device swap changes the channel count
        under us, and old blocks of the wrong width cannot be joined to
        new ones, so the buffer restarts instead."""
        if block.shape[1] != self._replay_channels:
            self._replay.clear()
            self._replay_frames = 0
            self._replay_channels = block.shape[1]
        self._replay.append(block)
        self._replay_frames += len(block)
        while self._replay_frames - len(self._replay[0]) >= REPLAY_S * SAMPLE_RATE:
            self._replay_frames -= len(self._replay.popleft())

    def replay_seconds(self):
        """How much audio the replay buffer is holding."""
        with self._lock:
            return self._replay_frames / SAMPLE_RATE

    def take_replay(self):
        """The last REPLAY_S of microphone audio, or None when there is
        too little to be worth a clip. The buffer is left alone, so two
        saves a few seconds apart give two overlapping clips rather than
        one clip and one empty one."""
        with self._lock:
            parts = list(self._replay)
        if not parts:
            return None
        data = np.concatenate(parts)
        return data if len(data) >= RECORD_MIN_S * SAMPLE_RATE else None

    def start_recording(self):
        """Begin keeping the raw mic blocks the input callback receives.
        Recording is taken before mute, push-to-talk and the mic volume,
        so a muted mic still records what it hears. False when there is
        no microphone running to record from."""
        with self._lock:
            if self.input_stream is None:
                return False
            self._recording = []
            self._recording_frames = 0
            self._recording_channels = self.input_channels
            return True

    def recording_seconds(self):
        """How long the recording in progress is, or None when idle."""
        with self._lock:
            if self._recording is None:
                return None
            return self._recording_frames / SAMPLE_RATE

    def stop_recording(self):
        """Stop and return what was captured as float32 frames, or None
        if nothing worth keeping came in."""
        with self._lock:
            parts, self._recording = self._recording, None
            self._recording_frames = 0
        if not parts:
            return None
        data = np.concatenate(parts)
        return data if len(data) >= RECORD_MIN_S * SAMPLE_RATE else None

    def set_duck(self, depth):
        """How far the mic drops under a playing clip: 0 for off, 1 to
        silence it. Taken under the lock so the callback never reads a
        half-written change."""
        with self._lock:
            self._ducker.depth = min(1.0, max(0.0, depth))

    def set_monitor_muted(self, muted):
        with self._lock:
            self.monitor_muted = muted
            if muted:
                self._active_sounds_monitor = []

    def _on_input(self, indata, frames, time_info, status):
        self._last_callback["input"] = time.monotonic()
        with self._lock:
            if status:
                self.dropouts += 1
            self.input_peak = float(np.abs(indata).max()) if indata.size else 0.0
            # One copy serves both: the mixer only ever reads these blocks
            # (_take_mic concatenates into a fresh array before anything
            # scales it), so the recording cannot be altered underneath.
            block = indata.copy()
            self._mic_buffer.append(block)
            self._mic_frames += len(block)
            while self._mic_frames > MIC_MAX_FRAMES:
                self._mic_frames -= len(self._mic_buffer.popleft())
            if (self._recording is not None
                    and self._recording_frames < RECORD_MAX_S * SAMPLE_RATE
                    and block.shape[1] == self._recording_channels):
                self._recording.append(block)
                self._recording_frames += len(block)
            if self.replay_enabled:
                self._keep_for_replay(block)
            elif self._replay:
                self._replay.clear()
                self._replay_frames = 0

    def _on_output(self, outdata, frames, time_info, status):
        self._last_callback["output"] = time.monotonic()
        with self._lock:
            if status:
                self.dropouts += 1
            mixed = self._pull_mic_frames(frames, self.output_channels)
            # Clean first, then colour: gating what an effect has already
            # shaped means gating the effect's own tail.
            mixed = self.mic_cleanup.process(mixed)
            mixed = self.mic_effects.process(mixed)
            # Ramp gain changes over the block so muting doesn't click.
            target = self.mic_gain if self.mic_enabled else 0.0
            if target != self._mic_level:
                mixed *= np.linspace(self._mic_level, target, frames, dtype=np.float32)[:, None]
                self._mic_level = target
            else:
                mixed *= target
            # Before _mix_sounds, which drops the clips that end in this
            # block: what matters is whether one is playing into it.
            # A paused clip is not playing as far as the mic is
            # concerned: holding one would otherwise hold the duck down
            # with it.
            playing = any(not s.paused for s in self._active_sounds)
            sounds = self._mix_sounds("_active_sounds", frames, self.output_channels,
                                      report_ends=True)
            # Only the mic is ducked, and only here. The monitor callback
            # carries clips alone, so what you hear locally is untouched
            # and the duck reaches the cable, which is where the voice and
            # the clip are actually competing.
            mixed = self._ducker.process(mixed, playing)
            mixed += sounds
        outdata[:] = self._output_limiter.process(mixed)
        self.output_peak = self._output_limiter.peak
        self._report_ends()

    def _on_monitor_output(self, outdata, frames, time_info, status):
        with self._lock:
            if status:
                self.dropouts += 1
            # Only when there is no output stream to report them: with
            # both running, the same clip ends in both lists.
            mixed = self._mix_sounds("_active_sounds_monitor", frames, self.monitor_channels,
                                     report_ends=self.output_stream is None)
        outdata[:] = self._monitor_limiter.process(mixed)
        self._report_ends()

    def _mix_sounds(self, attr, frames, channels, report_ends=False):
        """Sum one block from each clip in the named list, drop finished
        clips, and apply the soundboard volume. Caller holds the lock.

        `report_ends` is set by one caller only: the two lists hold the
        same clip twice, once per stream, and a board that plays the next
        clip when one ends must hear about it once.
        """
        mixed = np.zeros((frames, channels), dtype=np.float32)
        still_active = []
        for sound in getattr(self, attr):
            chunk, finished = sound.read(frames)
            mixed += chunk
            if finished:
                # Stopped by hand, or swapped out for a re-trigger: that
                # is not a clip reaching its end.
                if report_ends and not sound.stopping and sound.key is not None:
                    self._ended.append(sound.key)
            else:
                still_active.append(sound)
        setattr(self, attr, still_active)
        mixed *= self.sound_gain
        return mixed

    def _report_ends(self):
        """Hand out the keys of the clips that ended in this block, with
        the lock released: the callback is somebody else's code, and
        calling it from inside the lock is how a deadlock gets written
        later."""
        if not self._ended:
            return
        ended, self._ended = self._ended, []
        if self.on_finished is not None:
            for key in ended:
                self.on_finished(key)

    def _pull_mic_frames(self, frames, channels):
        out = np.zeros((frames, channels), dtype=np.float32)
        if not self._mic_ready:
            if self._mic_frames < MIC_TARGET_FRAMES:
                return out
            self._mic_ready = True

        # The mic and output run on separate clocks. Nudge mic playback
        # speed by ~0.2% to keep the cushion near its target instead of
        # letting drift empty it (gaps) or overfill it (skips).
        step = max(1, frames // 500)
        need = frames
        if self._mic_frames < MIC_TARGET_FRAMES:
            need -= step
        elif self._mic_frames > MIC_TARGET_FRAMES * 2:
            need += step

        raw = self._take_mic(need)
        if len(raw) < need:
            self._mic_ready = False  # ran dry; rebuild the cushion first
            out[:len(raw)] = _match_channels(raw, channels)
            return out
        return _match_channels(_resample(raw, need, frames), channels)

    def _take_mic(self, count):
        parts, taken = [], 0
        while taken < count and self._mic_buffer:
            chunk = self._mic_buffer[0]
            take = min(count - taken, len(chunk))
            parts.append(chunk[:take])
            if take < len(chunk):
                self._mic_buffer[0] = chunk[take:]
            else:
                self._mic_buffer.popleft()
            taken += take
        self._mic_frames -= taken
        if not parts:
            return np.zeros((0, self.input_channels), dtype=np.float32)
        return np.concatenate(parts)

    def play_data(self, data, samplerate, key=None, gain=1.0, loop=False, fades=NO_FADES):
        """Queue audio for playback. A clip with the same key that is
        still playing is stopped first, so re-triggering restarts it."""
        if self.output_stream is None and self.monitor_stream is None:
            raise RuntimeError("No output device selected.")
        self._queue_clip(_resample(data, samplerate, SAMPLE_RATE), key, gain, loop, fades)

    def _queue_clip(self, resampled, key, gain, loop=False, fades=NO_FADES):
        main_data = _match_channels(resampled, self.output_channels)
        monitor_data = _match_channels(resampled, self.monitor_channels)
        with self._lock:
            if key is not None:
                self._active_sounds = [s for s in self._active_sounds if s.key != key]
                self._active_sounds_monitor = [s for s in self._active_sounds_monitor if s.key != key]
            if self.output_stream is not None:
                self._active_sounds.append(_ActiveSound(main_data, key, gain, loop, fades))
            if self.monitor_stream is not None and not self.monitor_muted:
                self._active_sounds_monitor.append(_ActiveSound(monitor_data, key, gain, loop, fades))

    def play(self, path, gain=1.0, loop=False, key=None, fades=NO_FADES):
        """Queue a file for playback without blocking the caller. Errors
        are reported through on_error. `key` identifies what is playing
        for stopping and restarting; it defaults to the file, which a
        board entry that can play several files overrides so all of them
        answer to the one entry."""
        self._requests.put((path, gain, True, loop, key, fades))

    def preload(self, paths):
        """Decode files into the cache in the background."""
        for path in paths:
            self._requests.put((path, 1.0, False, False, None, NO_FADES))

    def _worker(self):
        while True:
            path, gain, play, loop, key, fades = self._requests.get()
            try:
                if play and self.output_stream is None and self.monitor_stream is None:
                    raise RuntimeError("No output device selected.")
                data = self._load_clip(path)
                if play:
                    self._queue_clip(data, key or os.path.abspath(path), gain, loop, fades)
            except FileNotFoundError:
                if play and self.on_error is not None:
                    self.on_error(f"File not found: {path}")
            except Exception as e:
                if play and self.on_error is not None:
                    self.on_error(f"{os.path.basename(path)}: {e}")

    def _load_clip(self, path):
        """Return the file resampled to SAMPLE_RATE, from the cache when
        the file is unchanged."""
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
        with self._cache_lock:
            data = self._clip_cache.get(key)
            if data is not None:
                self._clip_cache.move_to_end(key)
                return data
        raw, samplerate = sf.read(path, dtype="float32", always_2d=True)
        data = _resample(raw, samplerate, SAMPLE_RATE)
        if data.nbytes > CLIP_CACHE_BYTES:
            return data
        with self._cache_lock:
            for old_key in [k for k in self._clip_cache if k[0] == key[0]]:
                self._clip_cache_bytes -= self._clip_cache.pop(old_key).nbytes
            self._clip_cache[key] = data
            self._clip_cache_bytes += data.nbytes
            while self._clip_cache_bytes > CLIP_CACHE_BYTES:
                _, old = self._clip_cache.popitem(last=False)
                self._clip_cache_bytes -= old.nbytes
        return data
