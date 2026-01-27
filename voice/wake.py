"""
Wake Word Detection - Listens for "Hey Jarvis" using OpenWakeWord
"""

import numpy as np
import time
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
        on_wake: Optional[Callable[[str, float], None]] = None
    ):
        self.model_name = model_name
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.on_wake = on_wake
        
        self.model = None
        self.last_wake_time: Dict[str, float] = {}
        
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
        Process audio chunk and check for wake word.
        
        Args:
            audio: Audio samples (float32, -1 to 1)
            room: Room identifier
            
        Returns:
            Confidence score if wake word detected, None otherwise
        """
        if self.model is None:
            return None
            
        # Check cooldown
        now = time.time()
        last_wake = self.last_wake_time.get(room, 0)
        if now - last_wake < self.cooldown_seconds:
            return None
            
        # Convert to int16 for OpenWakeWord
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Run prediction
        prediction = self.model.predict(audio_int16)
        
        # Check all models for detection
        for model_name, scores in prediction.items():
            if len(scores) > 0:
                score = scores[-1]  # Most recent prediction
                if score >= self.threshold:
                    self.last_wake_time[room] = now
                    
                    if self.on_wake:
                        self.on_wake(room, score)
                        
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
        
        # Include pre-wake audio
        self.capture_buffer[room] = [np.array(self.pre_buffer[room])]
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
