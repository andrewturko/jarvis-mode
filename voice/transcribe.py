"""
Speech-to-Text - Transcribe captured audio using Faster-Whisper
"""

import numpy as np
from typing import Optional, Tuple
from pathlib import Path
import json
import tempfile
import wave
import os

VOICE_CONFIG_PATH = Path(__file__).parent.parent / "voice-config.json"


def load_config() -> dict:
    with open(VOICE_CONFIG_PATH) as f:
        return json.load(f)


class Transcriber:
    """
    Transcribes audio using Faster-Whisper (local) or OpenAI Whisper API.
    """
    
    def __init__(
        self,
        engine: str = "faster-whisper",
        model: str = "base.en",
        device: str = "auto",
        compute_type: str = "int8",
        language: str = "en"
    ):
        self.engine = engine
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        
        self.model = None
        self._load_model()
        
    def _load_model(self):
        """Load the transcription model."""
        if self.engine == "faster-whisper":
            self._load_faster_whisper()
        elif self.engine == "openai":
            self._load_openai()
        else:
            raise ValueError(f"Unknown STT engine: {self.engine}")
            
    def _load_faster_whisper(self):
        """Load Faster-Whisper model."""
        try:
            from faster_whisper import WhisperModel
            
            # Determine device
            device = self.device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
                    
            print(f"Loading Faster-Whisper model: {self.model_name} on {device}")
            
            self.model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=self.compute_type
            )
            
            print(f"Faster-Whisper model loaded successfully")
            
        except ImportError:
            print("Faster-Whisper not installed. Run: pip install faster-whisper")
            raise
            
    def _load_openai(self):
        """Setup OpenAI Whisper API client."""
        try:
            import openai
            self.model = openai.OpenAI()
            print("OpenAI Whisper API client initialized")
        except ImportError:
            print("OpenAI not installed. Run: pip install openai")
            raise
            
    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000
    ) -> Tuple[str, float]:
        """
        Transcribe audio to text.
        
        Args:
            audio: Audio samples (float32, -1 to 1)
            sample_rate: Sample rate of audio
            
        Returns:
            Tuple of (transcribed text, confidence)
        """
        if self.model is None:
            return "", 0.0
            
        if self.engine == "faster-whisper":
            return self._transcribe_faster_whisper(audio, sample_rate)
        elif self.engine == "openai":
            return self._transcribe_openai(audio, sample_rate)
        else:
            return "", 0.0
            
    def _transcribe_faster_whisper(
        self,
        audio: np.ndarray,
        sample_rate: int
    ) -> Tuple[str, float]:
        """Transcribe using Faster-Whisper."""
        try:
            # Faster-Whisper expects float32 audio
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
                
            # Resample if needed (Whisper expects 16kHz)
            if sample_rate != 16000:
                # Simple resampling (for production, use scipy.signal.resample)
                ratio = 16000 / sample_rate
                new_length = int(len(audio) * ratio)
                indices = np.linspace(0, len(audio) - 1, new_length).astype(int)
                audio = audio[indices]
                
            # Transcribe
            segments, info = self.model.transcribe(
                audio,
                language=self.language if self.language != "auto" else None,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=200
                )
            )
            
            # Collect text from segments
            text_parts = []
            total_prob = 0.0
            segment_count = 0
            
            for segment in segments:
                text_parts.append(segment.text)
                total_prob += segment.avg_logprob
                segment_count += 1
                
            text = " ".join(text_parts).strip()
            confidence = np.exp(total_prob / max(segment_count, 1))  # Convert log prob to prob
            
            return text, confidence
            
        except Exception as e:
            print(f"Transcription error: {e}")
            return "", 0.0
            
    def _transcribe_openai(
        self,
        audio: np.ndarray,
        sample_rate: int
    ) -> Tuple[str, float]:
        """Transcribe using OpenAI Whisper API."""
        try:
            # Save audio to temp WAV file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = f.name
                
            # Convert to int16 for WAV
            audio_int16 = (audio * 32767).astype(np.int16)
            
            with wave.open(temp_path, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio_int16.tobytes())
                
            # Transcribe via API
            with open(temp_path, "rb") as audio_file:
                result = self.model.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=self.language if self.language != "auto" else None
                )
                
            os.unlink(temp_path)
            
            return result.text.strip(), 0.9  # API doesn't return confidence
            
        except Exception as e:
            print(f"OpenAI transcription error: {e}")
            return "", 0.0


class VoiceActivityDetector:
    """
    Simple voice activity detection to determine when speech ends.
    """
    
    def __init__(
        self,
        threshold: float = 0.01,
        silence_duration: float = 1.5,
        sample_rate: int = 16000
    ):
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate
        
        self.silence_samples = 0
        self.silence_threshold_samples = int(silence_duration * sample_rate)
        
    def process(self, audio: np.ndarray) -> bool:
        """
        Process audio chunk.
        
        Returns:
            True if speech has ended (silence detected), False otherwise
        """
        energy = np.abs(audio).mean()
        
        if energy < self.threshold:
            self.silence_samples += len(audio)
        else:
            self.silence_samples = 0
            
        return self.silence_samples >= self.silence_threshold_samples
        
    def reset(self):
        """Reset silence counter."""
        self.silence_samples = 0


if __name__ == "__main__":
    # Test transcription
    config = load_config()
    stt_config = config.get("stt", {})
    
    print("Initializing transcriber...")
    
    transcriber = Transcriber(
        engine=stt_config.get("engine", "faster-whisper"),
        model=stt_config.get("model", "base.en"),
        device=stt_config.get("device", "auto"),
        compute_type=stt_config.get("compute_type", "int8"),
        language=stt_config.get("language", "en")
    )
    
    # Test with silent audio
    test_audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
    text, confidence = transcriber.transcribe(test_audio)
    print(f"Test transcription: '{text}' (confidence: {confidence:.2f})")
