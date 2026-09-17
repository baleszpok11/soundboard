"""Offline clip processing for the Sound Editor.

Everything here works on a whole clip at once and takes as long as it
takes, which is what separates it from audio_engine.py: that has to
fill a block before the sound card asks again.
"""

import numpy as np
from scipy.signal import butter, lfilter, sosfilt

from .audio_engine import _resample

BASS_CUTOFF_HZ = 200.0
TREBLE_CUTOFF_HZ = 4000.0
MID_CENTRE_HZ = 1000.0
MID_Q = 1.0
# A telephone line is roughly this much of the spectrum and nothing else.
PHONE_LOW_HZ = 300.0
PHONE_HIGH_HZ = 3000.0
ECHO_DELAY_S = 0.25
ECHO_FLOOR = 0.01  # repeats are dropped once they fall below this
ECHO_MAX_REPEATS = 24
STUTTER_EDGE_S = 0.002  # a hard cut mid-waveform clicks; this is short enough to be inaudible

# Phase vocoder frame size. 2048 at 48 kHz is ~43 ms, the usual
# compromise: long enough to separate low notes, short enough that
# transients don't smear too badly. The hop is a quarter of it, so a
# Hann window overlap-adds flat.
N_FFT = 2048
MIN_N_FFT = 256
OVERLAP = 4


def _low_shelf_coeffs(gain_db, cutoff_hz, sample_rate, slope=1.0):
    """RBJ Audio EQ Cookbook low-shelf biquad coefficients."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * cutoff_hz / sample_rate
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / 2 * np.sqrt((A + 1 / A) * (1 / slope - 1) + 2)
    sqrt_A = np.sqrt(A)

    b0 = A * ((A + 1) - (A - 1) * cos_w0 + 2 * sqrt_A * alpha)
    b1 = 2 * A * ((A - 1) - (A + 1) * cos_w0)
    b2 = A * ((A + 1) - (A - 1) * cos_w0 - 2 * sqrt_A * alpha)
    a0 = (A + 1) + (A - 1) * cos_w0 + 2 * sqrt_A * alpha
    a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
    a2 = (A + 1) + (A - 1) * cos_w0 - 2 * sqrt_A * alpha

    b = np.array([b0, b1, b2]) / a0
    a = np.array([a0, a1, a2]) / a0
    return b, a


def apply_bass(data, gain_db, sample_rate, cutoff_hz=BASS_CUTOFF_HZ):
    if gain_db == 0 or len(data) == 0:
        return data
    b, a = _low_shelf_coeffs(gain_db, cutoff_hz, sample_rate)
    filtered = np.empty_like(data)
    for ch in range(data.shape[1]):
        filtered[:, ch] = lfilter(b, a, data[:, ch])
    return filtered.astype(np.float32)


def _high_shelf_coeffs(gain_db, cutoff_hz, sample_rate, slope=1.0):
    """RBJ Audio EQ Cookbook high-shelf biquad coefficients."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * cutoff_hz / sample_rate
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / 2 * np.sqrt((A + 1 / A) * (1 / slope - 1) + 2)
    sqrt_A = np.sqrt(A)

    b0 = A * ((A + 1) + (A - 1) * cos_w0 + 2 * sqrt_A * alpha)
    b1 = -2 * A * ((A - 1) + (A + 1) * cos_w0)
    b2 = A * ((A + 1) + (A - 1) * cos_w0 - 2 * sqrt_A * alpha)
    a0 = (A + 1) - (A - 1) * cos_w0 + 2 * sqrt_A * alpha
    a1 = 2 * ((A - 1) - (A + 1) * cos_w0)
    a2 = (A + 1) - (A - 1) * cos_w0 - 2 * sqrt_A * alpha

    return np.array([b0, b1, b2]) / a0, np.array([a0, a1, a2]) / a0


def _peaking_coeffs(gain_db, centre_hz, sample_rate, q=MID_Q):
    """RBJ Audio EQ Cookbook peaking EQ biquad coefficients."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * centre_hz / sample_rate
    cos_w0 = np.cos(w0)
    alpha = np.sin(w0) / (2 * q)

    b = np.array([1 + alpha * A, -2 * cos_w0, 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * cos_w0, 1 - alpha / A])
    return b / a[0], a / a[0]


def _filter(data, b, a):
    filtered = np.empty_like(data)
    for ch in range(data.shape[1]):
        filtered[:, ch] = lfilter(b, a, data[:, ch])
    return filtered.astype(np.float32)


def apply_treble(data, gain_db, sample_rate, cutoff_hz=TREBLE_CUTOFF_HZ):
    if gain_db == 0 or len(data) == 0:
        return data
    return _filter(data, *_high_shelf_coeffs(gain_db, cutoff_hz, sample_rate))


def apply_mid(data, gain_db, sample_rate, centre_hz=MID_CENTRE_HZ):
    if gain_db == 0 or len(data) == 0:
        return data
    return _filter(data, *_peaking_coeffs(gain_db, centre_hz, sample_rate))


def apply_telephone(data, sample_rate, low_hz=PHONE_LOW_HZ, high_hz=PHONE_HIGH_HZ):
    """Throw away everything a phone line would: a band-pass, steep
    enough that what is left sounds like it came down a wire."""
    if len(data) == 0:
        return data
    top = min(high_hz, sample_rate / 2 * 0.99)
    if top <= low_hz:
        return data
    sos = butter(4, [low_hz, top], btype="band", fs=sample_rate, output="sos")
    filtered = np.empty_like(data)
    for ch in range(data.shape[1]):
        filtered[:, ch] = sosfilt(sos, data[:, ch])
    return filtered.astype(np.float32)


def apply_drive(data, drive_db):
    """Overdrive: push the signal hard into tanh, which bends the peaks
    over rather than shearing them. Loud goes in, loud and dirty comes
    out, and the level takes care of itself - tanh can't exceed 1."""
    if drive_db <= 0 or len(data) == 0:
        return data
    return np.tanh(data * 10 ** (drive_db / 20)).astype(np.float32)


def apply_robot(data, hz, sample_rate):
    """Ring modulation: multiply by a sine. Every partial splits in two,
    which is why it sounds metallic rather than pitched."""
    if hz <= 0 or len(data) == 0:
        return data
    t = np.arange(len(data), dtype=np.float32) / sample_rate
    return (data * np.sin(2 * np.pi * hz * t, dtype=np.float32)[:, None]).astype(np.float32)


def apply_width(data, width):
    """Mid/side: 0 collapses to mono, 1 leaves it alone, 2 pushes the
    difference between the channels out twice as far."""
    if width == 1.0 or len(data) == 0 or data.shape[1] < 2:
        return data
    mid = data.mean(axis=1, keepdims=True)
    return (mid + (data - mid) * width).astype(np.float32)


def apply_stutter(data, slice_s, sample_rate, repeats=2):
    """Hold each slice and play it `repeats` times, dropping what it
    covers, so the clip glitches without changing length."""
    size = int(slice_s * sample_rate)
    if size <= 0 or repeats < 2 or len(data) == 0:
        return data
    edge = min(int(STUTTER_EDGE_S * sample_rate), size // 2)
    shape = np.ones(size, dtype=np.float32)
    if edge:
        shape[:edge] = np.linspace(0, 1, edge, dtype=np.float32)
        shape[-edge:] = np.linspace(1, 0, edge, dtype=np.float32)
    out = data.copy()
    for start in range(0, len(data), size * repeats):
        held = data[start:start + size]
        if len(held) < size:
            break
        held = held * shape[:, None]
        for repeat in range(1, repeats):
            at = start + repeat * size
            take = min(size, len(out) - at)
            if take <= 0:
                break
            out[at:at + take] = held[:take]
    return out


def apply_echo(data, feedback, sample_rate, delay_s=ECHO_DELAY_S):
    """Repeats, each quieter than the last by `feedback`, with room at
    the end for them to die away rather than being cut off."""
    delay = int(delay_s * sample_rate)
    if feedback <= 0 or delay <= 0 or len(data) == 0:
        return data
    repeats = min(ECHO_MAX_REPEATS, int(np.ceil(np.log(ECHO_FLOOR) / np.log(min(feedback, 0.99)))))
    out = np.zeros((len(data) + delay * repeats, data.shape[1]), dtype=np.float32)
    out[:len(data)] = data
    gain = 1.0
    for repeat in range(1, repeats + 1):
        gain *= feedback
        at = delay * repeat
        out[at:at + len(data)] += data * gain
    return out


def reverse(data):
    return data[::-1]


def _frame_sizes(length):
    """Largest usable frame for this clip. A short clip gets a shorter
    frame rather than no stretching at all."""
    n_fft = N_FFT
    while n_fft > MIN_N_FFT and length < n_fft * 2:
        n_fft //= 2
    if length < n_fft * 2:
        return None, None
    return n_fft, n_fft // OVERLAP


def _stft(x, n_fft, hop, window):
    frames = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    return np.fft.rfft(x[idx] * window, axis=1).T


def _istft(spec, n_fft, hop, window):
    """Overlap-add, dividing out the window sum so the level is right
    even where the windows don't overlap (the two ends)."""
    frames = spec.shape[1]
    segments = np.fft.irfft(spec, n=n_fft, axis=0).T * window
    out = np.zeros(n_fft + hop * (frames - 1))
    weight = np.zeros_like(out)
    square = window ** 2
    for i in range(frames):
        at = i * hop
        out[at:at + n_fft] += segments[i]
        weight[at:at + n_fft] += square
    return out / np.maximum(weight, 1e-8)


def _stretch_channel(x, speed, n_fft, hop, window):
    padded = np.pad(x.astype(np.float64), n_fft // 2)
    spec = _stft(padded, n_fft, hop, window)
    magnitude = np.abs(spec)
    phase = np.angle(spec)

    # Where each bin's phase would land after one hop if it held exactly
    # the bin's centre frequency. What the signal actually does, minus
    # that, wrapped into +/-pi, is the bin's true frequency offset.
    expected = 2 * np.pi * hop * np.arange(spec.shape[0]) / n_fft
    deviation = phase[:, 1:] - phase[:, :-1] - expected[:, None]
    deviation -= 2 * np.pi * np.round(deviation / (2 * np.pi))
    advance = expected[:, None] + deviation

    steps = np.arange(0, spec.shape[1] - 1, speed)
    left = steps.astype(int)
    fraction = steps - left
    interpolated = magnitude[:, left] * (1 - fraction) + magnitude[:, left + 1] * fraction

    # Advance the phase by the true frequency once per output frame, so
    # the partials stay continuous however far the frames were moved.
    accumulated = np.empty_like(interpolated)
    accumulated[:, 0] = phase[:, 0]
    if len(left) > 1:
        accumulated[:, 1:] = phase[:, 0][:, None] + np.cumsum(advance[:, left[:-1]], axis=1)

    out = _istft(interpolated * np.exp(1j * accumulated), n_fft, hop, window)
    target = int(round(len(x) / speed))
    return out[n_fft // 2:n_fft // 2 + target]


def time_stretch(data, speed):
    """Make the clip `speed` times faster without moving the pitch.

    A phase vocoder: take the sound apart into overlapping frames, move
    the frames closer together or further apart, and rebuild the phase
    so the partials still line up. Percussive material smears a little
    at extreme settings - that is the price of not pulling in a large
    library for it.
    """
    if speed == 1.0 or len(data) == 0:
        return data
    n_fft, hop = _frame_sizes(len(data))
    if n_fft is None:
        # Too short to frame at all; nothing sensible to stretch.
        return data
    window = np.hanning(n_fft + 1)[:-1]  # periodic, so the overlap sums flat
    channels = [_stretch_channel(data[:, ch], speed, n_fft, hop, window)
                for ch in range(data.shape[1])]
    return np.column_stack(channels).astype(np.float32)


def pitch_shift(data, semitones, sample_rate):
    """Move the pitch without changing the length: stretch by the pitch
    ratio, then resample back by the same ratio. The resampling moves
    pitch and length together, and the stretch has already cancelled
    the length half of it."""
    if not semitones or len(data) == 0:
        return data
    ratio = 2 ** (semitones / 12)
    stretched = time_stretch(data, 1 / ratio)
    return _resample(stretched, sample_rate * ratio, sample_rate)
