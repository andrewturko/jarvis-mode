"""
Audio Stream - Pull RTSP audio from UniFi cameras via ffmpeg
"""

import subprocess
import threading
import queue
import numpy as np
import time
from typing import Optional, Callable, Dict
from pathlib import Path
import json

VOICE_CONFIG_PATH = Path(__file__).parent.parent / "voice-config.json"


def load_config() -> dict:
    """Load voice configuration."""
    with open(VOICE_CONFIG_PATH) as f:
        return json.load(f)


class AudioStream:
    """
    Captures audio from an RTSP stream using ffmpeg.
    Outputs raw PCM audio chunks for processing.
    """
    
    def __init__(
        self,
        room: str,
        rtsp_url: str,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 100,
        on_audio: Optional[Callable[[np.ndarray, str], None]] = None
    ):
        self.room = room
        self.rtsp_url = rtsp_url
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self.on_audio = on_audio
        
        self.chunk_size = int(sample_rate * chunk_duration_ms / 1000) * channels * 2  # 16-bit audio
        self.running = False
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        
    def start(self):
        """Start capturing audio."""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        """Stop capturing audio."""
        self.running = False
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread:
            self.thread.join(timeout=5)
            
    def _capture_loop(self):
        """Main capture loop - runs ffmpeg and reads audio."""
        while self.running:
            try:
                self._run_ffmpeg()
            except Exception as e:
                print(f"[{self.room}] Stream error: {e}")
                time.sleep(5)  # Retry after delay
                
    def _run_ffmpeg(self):
        """Run ffmpeg to capture RTSP audio stream."""
        cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-vn",  # No video
            "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-f", "s16le",
            "-loglevel", "error",
            "-"  # Output to stdout
        ]
        
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        print(f"[{self.room}] Started audio capture from {self.rtsp_url}")
        
        while self.running and self.process.poll() is None:
            data = self.process.stdout.read(self.chunk_size)
            if not data:
                break
                
            # Convert to numpy array
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            
            if self.on_audio:
                self.on_audio(audio, self.room)
                
        if self.process.poll() is not None and self.running:
            stderr = self.process.stderr.read().decode()
            if stderr:
                print(f"[{self.room}] ffmpeg error: {stderr}")


class MultiRoomAudioStream:
    """
    Manages audio streams from multiple cameras.
    """
    
    def __init__(self, on_audio: Callable[[np.ndarray, str], None]):
        self.config = load_config()
        self.on_audio = on_audio
        self.streams: Dict[str, AudioStream] = {}
        
    def start(self):
        """Start all enabled camera streams."""
        nvr_config = self.config.get("unifi_protect", {})
        audio_config = self.config.get("audio", {})
        cameras = self.config.get("cameras", {})
        
        nvr_ip = nvr_config.get("nvr_ip", "192.168.1.1")
        rtsp_port = nvr_config.get("rtsp_port", 7447)
        username = nvr_config.get("username", "")
        password = nvr_config.get("password", "")
        
        for room, cam_config in cameras.items():
            if not cam_config.get("enabled", True):
                continue
                
            rtsp_path = cam_config.get("rtsp_path", room)
            
            # Build RTSP URL
            if username and password:
                rtsp_url = f"rtsp://{username}:{password}@{nvr_ip}:{rtsp_port}/{rtsp_path}"
            else:
                rtsp_url = f"rtsp://{nvr_ip}:{rtsp_port}/{rtsp_path}"
            
            stream = AudioStream(
                room=room,
                rtsp_url=rtsp_url,
                sample_rate=audio_config.get("sample_rate", 16000),
                channels=audio_config.get("channels", 1),
                chunk_duration_ms=audio_config.get("chunk_duration_ms", 100),
                on_audio=self.on_audio
            )
            
            self.streams[room] = stream
            stream.start()
            
        print(f"Started {len(self.streams)} audio streams")
        
    def stop(self):
        """Stop all streams."""
        for stream in self.streams.values():
            stream.stop()
        self.streams.clear()


if __name__ == "__main__":
    # Test audio capture
    def print_audio(audio: np.ndarray, room: str):
        level = np.abs(audio).mean()
        bars = int(level * 50)
        print(f"[{room}] {'█' * bars}{' ' * (50 - bars)} {level:.4f}")
    
    config = load_config()
    
    # Test single stream
    nvr_ip = config["unifi_protect"]["nvr_ip"]
    test_room = list(config["cameras"].keys())[0]
    test_path = config["cameras"][test_room]["rtsp_path"]
    
    print(f"Testing audio from {test_room}...")
    print(f"RTSP URL: rtsp://{nvr_ip}:7447/{test_path}")
    print("Press Ctrl+C to stop\n")
    
    stream = AudioStream(
        room=test_room,
        rtsp_url=f"rtsp://{nvr_ip}:7447/{test_path}",
        on_audio=print_audio
    )
    
    try:
        stream.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stream.stop()
        print("\nStopped")
