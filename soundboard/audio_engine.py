"""Audio mixing: microphone plus triggered clips into the output device."""

import collections
import os
import queue
import threading
import time

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi
try:
    import sounddevice as sd
except OSError as e:
    # sounddevice bundles PortAudio only on Windows and macOS.
    sd = None
    PORTAUDIO_ERROR = e
else:
    PORTAUDIO_ERROR = None
import soundfile as sf

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 1024
MIC_TARGET_FRAMES = SAMPLE_RATE // 10  # 100 ms cushion against uneven mic delivery
MIC_MAX_FRAMES = SAMPLE_RATE // 2  # drop older mic audio beyond 500 ms
CLIP_CACHE_BYTES = 512 * 1024 * 1024  # decoded clips kept in memory
LIMITER_CEILING = 0.89  # about -1 dBFS
LIMITER_RELEASE_S = 0.3
STREAM_TIMEOUT_S = 2.0  # a running stream with no callbacks for this long is treated as lost
# Live mic effects. Only what can be done a block at a time belongs here;
# anything that needs the whole clip stays in dsp.py and the editor.
MIC_EFFECTS = ("none", "telephone", "robot", "drive")
MIC_PHONE_BAND_HZ = (300.0, 3000.0)
# An integer number of cycles per second, so wrapping the oscillator at
# one second leaves the sine continuous.
MIC_ROBOT_HZ = 80.0
MIC_DRIVE_DB = 18.0
RECORD_MAX_S = 180  # a forgotten recording stops here instead of filling memory
RECORD_MIN_S = 0.2  # shorter than this is a mis-click, not a clip
TEST_TONE_S = 0.8
TEST_TONE_HZ = 440.0
TEST_TONE_LEVEL = 0.4
TEST_TONE_FADE_S = 0.05


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


class _ActiveSound:
    def __init__(self, data, key=None, gain=1.0, loop=False):
        self.data = data
        self.key = key
        self.gain = gain
        self.loop = loop
        self.position = 0

    def read(self, frames):
        channels = self.data.shape[1]
        if not len(self.data):
            # An empty clip has nothing to wrap around, so looping it
            # would spin forever.
            return np.zeros((frames, channels), dtype=np.float32), True
        parts = []
        needed = frames
        while needed > 0:
            end = min(self.position + needed, len(self.data))
            parts.append(self.data[self.position:end])
            needed -= end - self.position
            self.position = end
            if self.position < len(self.data):
                continue
            if not self.loop:
                break
            self.position = 0  # wrap and keep filling the same block
        chunk = np.concatenate(parts) * self.gain if parts else np.zeros((0, channels), dtype=np.float32)
        finished = not self.loop and self.position >= len(self.data)
        if len(chunk) < frames:
            pad = np.zeros((frames - len(chunk), channels), dtype=np.float32)
            chunk = np.vstack([chunk, pad]) if len(chunk) else pad
        return chunk, finished


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

    def _wet(self, block):
        if self._active == "telephone":
            return self._telephone(block)
        if self._active == "robot":
            return self._robot(block)
        if self._active == "drive":
            return np.tanh(block * 10 ** (MIC_DRIVE_DB / 20)).astype(np.float32)
        return block

    def _telephone(self, block):
        channels = block.shape[1]
        if self._sos is None or channels != self._channels:
            low, high = MIC_PHONE_BAND_HZ
            self._sos = butter(4, [low, high], btype="band", fs=SAMPLE_RATE, output="sos")
            self._channels = channels
            self._zi = None
        if self._zi is None:
            if not len(block):
                return block
            # Start each section settled on the first sample, so the
            # filter doesn't thump while it fills. (sections, 2, channels)
            self._zi = (sosfilt_zi(self._sos)[:, :, None] * block[0]).astype(np.float32)
        out = np.empty_like(block)
        for ch in range(channels):
            out[:, ch], self._zi[:, :, ch] = sosfilt(
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
        self.mic_effects = _MicEffectChain()
        self._output_limiter = _Limiter()
        self._monitor_limiter = _Limiter()
        self.on_error = None
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

    def stop_all(self):
        with self._lock:
            self._active_sounds = []
            self._active_sounds_monitor = []

    def set_loop(self, key, loop):
        """Change looping on clips already playing, so switching it off
        doesn't leave one running until it's stopped by hand."""
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if sound.key == key:
                    sound.loop = loop

    def stop_key(self, key):
        with self._lock:
            self._active_sounds = [s for s in self._active_sounds if s.key != key]
            self._active_sounds_monitor = [s for s in self._active_sounds_monitor if s.key != key]

    def active_keys(self):
        """Every key currently playing, mapped to how far that clip has
        got (0 to 1). One pass under the lock, so the board can poll all
        its entries at once."""
        progress = {}
        with self._lock:
            for sound in (*self._active_sounds, *self._active_sounds_monitor):
                if sound.key is None or not len(sound.data):
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
                if sound.key == key and len(sound.data):
                    return min(1.0, sound.position / len(sound.data))
        return None

    def set_mic_effect(self, name, amount):
        """Change the live mic effect. Takes the lock so the callback
        never reads a half-applied change."""
        with self._lock:
            self.mic_effects.set(name, amount)

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

    def _on_output(self, outdata, frames, time_info, status):
        self._last_callback["output"] = time.monotonic()
        with self._lock:
            if status:
                self.dropouts += 1
            mixed = self._pull_mic_frames(frames, self.output_channels)
            mixed = self.mic_effects.process(mixed)
            # Ramp gain changes over the block so muting doesn't click.
            target = self.mic_gain if self.mic_enabled else 0.0
            if target != self._mic_level:
                mixed *= np.linspace(self._mic_level, target, frames, dtype=np.float32)[:, None]
                self._mic_level = target
            else:
                mixed *= target
            mixed += self._mix_sounds("_active_sounds", frames, self.output_channels)
        outdata[:] = self._output_limiter.process(mixed)
        self.output_peak = self._output_limiter.peak

    def _on_monitor_output(self, outdata, frames, time_info, status):
        with self._lock:
            if status:
                self.dropouts += 1
            mixed = self._mix_sounds("_active_sounds_monitor", frames, self.monitor_channels)
        outdata[:] = self._monitor_limiter.process(mixed)

    def _mix_sounds(self, attr, frames, channels):
        """Sum one block from each clip in the named list, drop finished
        clips, and apply the soundboard volume. Caller holds the lock."""
        mixed = np.zeros((frames, channels), dtype=np.float32)
        still_active = []
        for sound in getattr(self, attr):
            chunk, finished = sound.read(frames)
            mixed += chunk
            if not finished:
                still_active.append(sound)
        setattr(self, attr, still_active)
        mixed *= self.sound_gain
        return mixed

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

    def play_data(self, data, samplerate, key=None, gain=1.0, loop=False):
        """Queue audio for playback. A clip with the same key that is
        still playing is stopped first, so re-triggering restarts it."""
        if self.output_stream is None and self.monitor_stream is None:
            raise RuntimeError("No output device selected.")
        self._queue_clip(_resample(data, samplerate, SAMPLE_RATE), key, gain, loop)

    def _queue_clip(self, resampled, key, gain, loop=False):
        main_data = _match_channels(resampled, self.output_channels)
        monitor_data = _match_channels(resampled, self.monitor_channels)
        with self._lock:
            if key is not None:
                self._active_sounds = [s for s in self._active_sounds if s.key != key]
                self._active_sounds_monitor = [s for s in self._active_sounds_monitor if s.key != key]
            if self.output_stream is not None:
                self._active_sounds.append(_ActiveSound(main_data, key, gain, loop))
            if self.monitor_stream is not None and not self.monitor_muted:
                self._active_sounds_monitor.append(_ActiveSound(monitor_data, key, gain, loop))

    def play(self, path, gain=1.0, loop=False, key=None):
        """Queue a file for playback without blocking the caller. Errors
        are reported through on_error. `key` identifies what is playing
        for stopping and restarting; it defaults to the file, which a
        board entry that can play several files overrides so all of them
        answer to the one entry."""
        self._requests.put((path, gain, True, loop, key))

    def preload(self, paths):
        """Decode files into the cache in the background."""
        for path in paths:
            self._requests.put((path, 1.0, False, False, None))

    def _worker(self):
        while True:
            path, gain, play, loop, key = self._requests.get()
            try:
                if play and self.output_stream is None and self.monitor_stream is None:
                    raise RuntimeError("No output device selected.")
                data = self._load_clip(path)
                if play:
                    self._queue_clip(data, key or os.path.abspath(path), gain, loop)
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
