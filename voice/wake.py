"""
Wake Word Detection - Listens for "Hey Jarvis" using OpenWakeWord
"""

import numpy as np
import time
import threading
from typing import Optional, Callable, Dict
from collections import deque
from pathlib import Path
import json

VOICE_CONFIG_PATH = Path(__file__).parent.parent / "voice-config.json"


def load_config() -> dict:
    with open(VOICE_CONFIG_PATH) as f:
        return json.load(f)


class WakeWordDetector:
    """
    Detects wake words in audio streams using OpenWakeWord.
    """
    
    def __init__(
        self,
        model_name: str = "hey_jarvis_v0.1",
        threshold: float = 0.5,
        cooldown_seconds: float = 2.0,
        on_wake: Optional[Callable[[str, float], None]] = None,
        buffer_seconds: float = 1.5  # Process 1.5s windows instead of 100ms chunks
    ):
        self.model_name = model_name
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.on_wake = on_wake
        self.buffer_seconds = buffer_seconds

        self.model = None
        self.last_wake_time: Dict[str, float] = {}
        self.last_global_wake_time: float = 0  # Global cooldown across all rooms
        self.wake_lock = threading.Lock()  # Prevent race conditions in multi-room detection

        # Sliding buffer for each room (holds ~1.5s of audio)
        self.audio_buffers: Dict[str, deque] = {}
        self.buffer_size = int(16000 * buffer_seconds)  # samples

        self._load_model()
        
    def _load_model(self):
        """Load the OpenWakeWord model."""
        try:
            from openwakeword import Model
            
            # OpenWakeWord has built-in models including "hey_jarvis"
            # Custom models can be loaded from file paths
            self.model = Model(
                wakeword_models=[self.model_name],
                inference_framework="onnx"
            )
            print(f"Loaded wake word model: {self.model_name}")
            
        except ImportError:
            print("OpenWakeWord not installed. Run: pip install openwakeword")
            raise
        except Exception as e:
            print(f"Error loading wake word model: {e}")
            # Try with default model
            try:
                from openwakeword import Model
                self.model = Model(inference_framework="onnx")
                print("Loaded default wake word models")
            except Exception as e2:
                print(f"Could not load any wake word model: {e2}")
                raise
            
    def process_audio(self, audio: np.ndarray, room: str) -> Optional[float]:
        """
        Process audio chunk and check for wake word using buffered approach.

        Args:
            audio: Audio samples (float32, -1 to 1)
            room: Room identifier

        Returns:
            Confidence score if wake word detected, None otherwise
        """
        if self.model is None:
            return None

        # Check cooldown (both per-room and global to prevent multi-room duplicates)
        now = time.time()
        last_wake = self.last_wake_time.get(room, 0)
        if now - last_wake < self.cooldown_seconds:
            return None

        # Global cooldown: if ANY room detected recently, ignore this detection
        if now - self.last_global_wake_time < self.cooldown_seconds:
            return None

        # Initialize buffer for this room if needed
        if room not in self.audio_buffers:
            self.audio_buffers[room] = deque(maxlen=self.buffer_size)

        # Add incoming chunk to buffer
        self.audio_buffers[room].extend(audio)

        # Only process when buffer is full (1.5 seconds accumulated)
        if len(self.audio_buffers[room]) < self.buffer_size:
            return None

        # Convert buffered audio to numpy array and int16
        buffer_audio = np.array(self.audio_buffers[room], dtype=np.float32)
        audio_int16 = (buffer_audio * 32767).astype(np.int16)

        # Run prediction on the full buffer
        prediction = self.model.predict(audio_int16)

        # Check all models for detection
        for model_name, scores in prediction.items():
            # Handle both scalar and array predictions
            if np.isscalar(scores):
                score = float(scores)
            elif hasattr(scores, '__len__') and len(scores) > 0:
                score = float(scores[-1])  # Most recent prediction
            else:
                continue

            # Log high scores that are close to detection
            if score > 0.1:
                rms = np.sqrt(np.mean(buffer_audio**2))
                print(f"[{room}] Wake score: {score:.4f} (threshold: {self.threshold}, RMS: {rms:.3f})")

            if score >= self.threshold:
                # Use lock to prevent race condition when multiple rooms detect simultaneously
                with self.wake_lock:
                    # Check global cooldown again inside the lock
                    if now - self.last_global_wake_time < self.cooldown_seconds:
                        print(f"[{room}] Wake word detected! (confidence: {score:.2f}) - blocked by global cooldown")
                        return None

                    self.last_wake_time[room] = now
                    self.last_global_wake_time = now  # Block all rooms temporarily

                    print(f"[{room}] Wake word detected! (confidence: {score:.2f})")

                    if self.on_wake:
                        self.on_wake(room, score)

                    # Clear buffer after detection
                    self.audio_buffers[room].clear()

                    return score

        return None
        
    def reset_cooldown(self, room: str):
        """Reset cooldown for a room (e.g., after processing command)."""
        self.last_wake_time[room] = 0


class AudioBufferWithWakeDetection:
    """
    Combines audio buffering with wake word detection.
    When wake word is detected, captures subsequent audio for transcription.
    """
    
    def __init__(
        self,
        detector: WakeWordDetector,
        capture_seconds: float = 7.0,
        pre_wake_seconds: float = 0.5,
        sample_rate: int = 16000,
        on_capture_complete: Optional[Callable[[np.ndarray, str], None]] = None
    ):
        self.detector = detector
        self.capture_seconds = capture_seconds
        self.pre_wake_seconds = pre_wake_seconds
        self.sample_rate = sample_rate
        self.on_capture_complete = on_capture_complete
        
        # Pre-wake buffer (circular)
        pre_wake_samples = int(pre_wake_seconds * sample_rate)
        self.pre_buffer: Dict[str, deque] = {}
        self.pre_buffer_size = pre_wake_samples
        
        # Capture state per room
        self.capturing: Dict[str, bool] = {}
        self.capture_buffer: Dict[str, list] = {}
        self.capture_start_time: Dict[str, float] = {}
        
    def process_audio(self, audio: np.ndarray, room: str):
        """
        Process audio chunk - detect wake word or capture speech.
        """
        # Initialize room state
        if room not in self.pre_buffer:
            self.pre_buffer[room] = deque(maxlen=self.pre_buffer_size)
            self.capturing[room] = False
            self.capture_buffer[room] = []
            
        # If capturing, add to capture buffer
        if self.capturing[room]:
            self.capture_buffer[room].append(audio)
            
            # Check if capture duration reached
            elapsed = time.time() - self.capture_start_time[room]
            if elapsed >= self.capture_seconds:
                self._finish_capture(room)
            return
            
        # Add to pre-buffer
        self.pre_buffer[room].extend(audio)
        
        # Check for wake word
        confidence = self.detector.process_audio(audio, room)
        
        if confidence is not None:
            print(f"[{room}] Wake word detected! (confidence: {confidence:.2f})")
            self._start_capture(room)
            
    def _start_capture(self, room: str):
        """Start capturing audio after wake word."""
        self.capturing[room] = True
        self.capture_start_time[room] = time.time()

        # Don't include pre-wake audio (contains "Hey Jarvis")
        # Start fresh to capture only the command that follows
        self.capture_buffer[room] = []
        self.pre_buffer[room].clear()
        
    def _finish_capture(self, room: str):
        """Finish capture and send for transcription."""
        self.capturing[room] = False
        
        # Concatenate all captured audio
        if self.capture_buffer[room]:
            audio = np.concatenate(self.capture_buffer[room])
            self.capture_buffer[room] = []
            
            print(f"[{room}] Captured {len(audio) / self.sample_rate:.1f}s of audio")
            
            if self.on_capture_complete:
                self.on_capture_complete(audio, room)
                
    def cancel_capture(self, room: str):
        """Cancel ongoing capture."""
        self.capturing[room] = False
        self.capture_buffer[room] = []


if __name__ == "__main__":
    # Test wake word detection
    config = load_config()
    wake_config = config.get("wake_word", {})
    
    def on_wake(room: str, confidence: float):
        print(f"\n🎤 WAKE WORD DETECTED in {room}! (confidence: {confidence:.2f})\n")
    
    detector = WakeWordDetector(
        model_name=wake_config.get("model", "hey_jarvis_v0.1"),
        threshold=wake_config.get("threshold", 0.5),
        cooldown_seconds=wake_config.get("cooldown_seconds", 2.0),
        on_wake=on_wake
    )
    
    print("Wake word detector initialized")
    print(f"Model: {detector.model_name}")
    print(f"Threshold: {detector.threshold}")
    print("\nListening for wake word... (feed audio to detector.process_audio)")
