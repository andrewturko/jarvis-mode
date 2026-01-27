#!/usr/bin/env python3
"""
Jarvis Voice Service
Main orchestrator - connects audio streams, wake word detection, STT, and Clawdbot.
"""

import argparse
import signal
import sys
import time
import json
import numpy as np
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stream import MultiRoomAudioStream, AudioStream, load_config
from wake import WakeWordDetector, AudioBufferWithWakeDetection
from transcribe import Transcriber, VoiceActivityDetector
from respond import ResponseHandler


class JarvisVoiceService:
    """
    Main voice service that:
    1. Captures audio from UniFi cameras via RTSP
    2. Listens for wake word ("Hey Jarvis")
    3. Captures and transcribes speech
    4. Sends to Clawdbot for processing
    5. Plays TTS response on Sonos
    """
    
    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config()
        self.running = False
        
        # Initialize components
        self._init_wake_detector()
        self._init_transcriber()
        self._init_response_handler()
        self._init_audio_streams()
        
    def _init_wake_detector(self):
        """Initialize wake word detection."""
        wake_config = self.config.get("wake_word", {})
        
        self.wake_detector = WakeWordDetector(
            model_name=wake_config.get("model", "hey_jarvis_v0.1"),
            threshold=wake_config.get("threshold", 0.5),
            cooldown_seconds=wake_config.get("cooldown_seconds", 2.0),
            on_wake=self._on_wake_detected
        )
        
        stt_config = self.config.get("stt", {})
        audio_config = self.config.get("audio", {})
        
        self.audio_buffer = AudioBufferWithWakeDetection(
            detector=self.wake_detector,
            capture_seconds=stt_config.get("capture_seconds", 7.0),
            pre_wake_seconds=0.5,
            sample_rate=audio_config.get("sample_rate", 16000),
            on_capture_complete=self._on_capture_complete
        )
        
    def _init_transcriber(self):
        """Initialize speech-to-text."""
        stt_config = self.config.get("stt", {})
        
        self.transcriber = Transcriber(
            engine=stt_config.get("engine", "faster-whisper"),
            model=stt_config.get("model", "base.en"),
            device=stt_config.get("device", "auto"),
            compute_type=stt_config.get("compute_type", "int8"),
            language=stt_config.get("language", "en")
        )
        
    def _init_response_handler(self):
        """Initialize Clawdbot client and TTS."""
        self.response_handler = ResponseHandler()
        
    def _init_audio_streams(self):
        """Initialize audio streams from cameras."""
        self.audio_streams = MultiRoomAudioStream(
            on_audio=self._on_audio
        )
        
    def _on_audio(self, audio: np.ndarray, room: str):
        """Handle incoming audio chunk."""
        self.audio_buffer.process_audio(audio, room)
        
    def _on_wake_detected(self, room: str, confidence: float):
        """Handle wake word detection."""
        print(f"\n{'='*50}")
        print(f"🎤 WAKE WORD DETECTED in {room}!")
        print(f"   Confidence: {confidence:.2f}")
        print(f"   Listening for command...")
        print(f"{'='*50}\n")
        
    def _on_capture_complete(self, audio: np.ndarray, room: str):
        """Handle completed audio capture - transcribe and respond."""
        print(f"[{room}] Transcribing {len(audio) / 16000:.1f}s of audio...")
        
        # Transcribe
        text, confidence = self.transcriber.transcribe(audio)
        
        if not text or len(text.strip()) < 2:
            print(f"[{room}] No speech detected or transcription empty")
            return
            
        print(f"[{room}] Transcribed: \"{text}\" (confidence: {confidence:.2f})")
        
        # Filter out wake word from transcription if present
        text_lower = text.lower()
        for phrase in ["hey jarvis", "hey jarvis,", "jarvis", "jarvis,"]:
            if text_lower.startswith(phrase):
                text = text[len(phrase):].strip()
                break
                
        if not text:
            print(f"[{room}] Only wake word detected, no command")
            return
            
        # Send to Clawdbot and respond
        self.response_handler.handle(text, room)
        
    def start(self):
        """Start the voice service."""
        if self.running:
            return
            
        print("\n" + "="*60)
        print("  JARVIS VOICE SERVICE")
        print("="*60)
        print(f"\nConfiguration:")
        print(f"  Wake word: {self.config['wake_word'].get('model', 'hey_jarvis')}")
        print(f"  STT engine: {self.config['stt'].get('engine', 'faster-whisper')}")
        print(f"  Cameras: {list(self.config['cameras'].keys())}")
        print(f"\nStarting audio streams...")
        
        self.running = True
        self.audio_streams.start()
        
        print("\n✓ Voice service running. Say 'Hey Jarvis' to activate.")
        print("  Press Ctrl+C to stop.\n")
        
    def stop(self):
        """Stop the voice service."""
        if not self.running:
            return
            
        print("\nStopping voice service...")
        self.running = False
        self.audio_streams.stop()
        print("Voice service stopped.")
        
    def run(self):
        """Run the voice service (blocking)."""
        self.start()
        
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def main():
    parser = argparse.ArgumentParser(description="Jarvis Voice Service")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to voice config file"
    )
    parser.add_argument(
        "--test-audio",
        action="store_true",
        help="Test audio capture only (no wake word)"
    )
    parser.add_argument(
        "--test-wake",
        action="store_true",
        help="Test wake word detection (from mic)"
    )
    parser.add_argument(
        "--room",
        type=str,
        default="kitchen",
        help="Room for testing (default: kitchen)"
    )
    
    args = parser.parse_args()
    
    if args.test_audio:
        # Test audio capture from RTSP
        print("Testing audio capture...")
        config = load_config()
        
        room = args.room
        if room not in config["cameras"]:
            print(f"Room '{room}' not in config. Available: {list(config['cameras'].keys())}")
            return
            
        nvr_ip = config["unifi_protect"]["nvr_ip"]
        rtsp_path = config["cameras"][room]["rtsp_path"]
        rtsp_url = f"rtsp://{nvr_ip}:7447/{rtsp_path}"
        
        def print_level(audio: np.ndarray, room: str):
            level = np.abs(audio).mean()
            bars = int(level * 100)
            print(f"[{room}] {'█' * min(bars, 50)}{' ' * max(0, 50 - bars)} {level:.4f}")
        
        print(f"Connecting to {rtsp_url}...")
        stream = AudioStream(room, rtsp_url, on_audio=print_level)
        
        try:
            stream.start()
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stream.stop()
            
    elif args.test_wake:
        # Test wake word from system mic
        print("Testing wake word detection from system microphone...")
        print("Say 'Hey Jarvis' to test.\n")
        
        try:
            import sounddevice as sd
        except ImportError:
            print("sounddevice not installed. Run: pip install sounddevice")
            return
            
        config = load_config()
        wake_config = config.get("wake_word", {})
        
        def on_wake(room: str, confidence: float):
            print(f"\n🎤 WAKE WORD DETECTED! (confidence: {confidence:.2f})\n")
        
        detector = WakeWordDetector(
            model_name=wake_config.get("model", "hey_jarvis_v0.1"),
            threshold=wake_config.get("threshold", 0.5),
            on_wake=on_wake
        )
        
        def audio_callback(indata, frames, time_info, status):
            audio = indata[:, 0].astype(np.float32)
            detector.process_audio(audio, "mic")
        
        with sd.InputStream(
            samplerate=16000,
            channels=1,
            callback=audio_callback,
            blocksize=1600  # 100ms chunks
        ):
            print("Listening... Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
                
    else:
        # Run full service
        service = JarvisVoiceService(config_path=args.config)
        
        # Handle signals
        def signal_handler(sig, frame):
            service.stop()
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        service.run()


if __name__ == "__main__":
    main()
