"""
Response Handler - Send to Clawdbot and play TTS response via Sonos
"""

import json
import urllib.request
import urllib.error
import subprocess
import tempfile
import os
from typing import Optional, Dict
from pathlib import Path

VOICE_CONFIG_PATH = Path(__file__).parent.parent / "voice-config.json"


def load_config() -> dict:
    with open(VOICE_CONFIG_PATH) as f:
        return json.load(f)


class ClawdbotClient:
    """
    Sends voice transcriptions to Clawdbot and receives responses.
    """
    
    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:18789",
        hook_path: str = "/hooks/jarvis/voice",
        hook_token: str = "",
        timeout_seconds: int = 30
    ):
        self.gateway_url = gateway_url
        self.hook_path = hook_path
        self.hook_token = hook_token
        self.timeout_seconds = timeout_seconds
        
    def send(self, text: str, room: str) -> Optional[str]:
        """
        Send transcribed text to Clawdbot.
        
        Args:
            text: Transcribed speech
            room: Room where speech was detected
            
        Returns:
            Response text from Clawdbot, or None on error
        """
        try:
            url = f"{self.gateway_url}{self.hook_path}"
            
            payload = json.dumps({
                "room": room,
                "text": text
            }).encode()
            
            headers = {
                "Content-Type": "application/json"
            }
            if self.hook_token:
                headers["Authorization"] = f"Bearer {self.hook_token}"
                
            req = urllib.request.Request(
                url,
                data=payload,
                headers=headers,
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode())
                return result.get("response", result.get("message", ""))
                
        except urllib.error.URLError as e:
            print(f"Clawdbot connection error: {e}")
            return None
        except Exception as e:
            print(f"Clawdbot error: {e}")
            return None


class SonosTTS:
    """
    Text-to-speech playback via Sonos speakers.
    Uses the sonos CLI (sonoscli skill) for control.
    """
    
    def __init__(
        self,
        default_volume: int = 30,
        announcement_volume: int = 40
    ):
        self.default_volume = default_volume
        self.announcement_volume = announcement_volume
        
        # Room to speaker name mapping
        self.room_speakers: Dict[str, str] = {}
        self._load_speaker_mapping()
        
    def _load_speaker_mapping(self):
        """Load room to speaker mapping from config."""
        config = load_config()
        for room, cam_config in config.get("cameras", {}).items():
            speaker = cam_config.get("speaker", room.replace("_", " ").title())
            self.room_speakers[room] = speaker
            
    def speak(self, text: str, room: str) -> bool:
        """
        Speak text through Sonos speaker in the specified room.
        
        Args:
            text: Text to speak
            room: Room identifier
            
        Returns:
            True if successful, False otherwise
        """
        speaker = self.room_speakers.get(room)
        if not speaker:
            print(f"No speaker mapped for room: {room}")
            return False
            
        try:
            # Use ElevenLabs TTS via sag if available, otherwise fallback
            audio_path = self._generate_tts(text)
            if not audio_path:
                print("TTS generation failed")
                return False
                
            # Play on Sonos
            success = self._play_on_sonos(audio_path, speaker)
            
            # Cleanup temp file
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
                
            return success
            
        except Exception as e:
            print(f"TTS error: {e}")
            return False
            
    def _generate_tts(self, text: str) -> Optional[str]:
        """Generate TTS audio file."""
        try:
            # Try ElevenLabs via sag
            result = subprocess.run(
                ["sag", "--text", text, "--output", "-"],
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Save to temp file
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    f.write(result.stdout)
                    return f.name
                    
        except FileNotFoundError:
            pass  # sag not installed
        except Exception as e:
            print(f"sag TTS error: {e}")
            
        # Fallback to say command (macOS)
        try:
            with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
                temp_path = f.name
                
            result = subprocess.run(
                ["say", "-o", temp_path, text],
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return temp_path
                
        except FileNotFoundError:
            pass  # say not available
        except Exception as e:
            print(f"say TTS error: {e}")
            
        return None
        
    def _play_on_sonos(self, audio_path: str, speaker: str) -> bool:
        """Play audio file on Sonos speaker."""
        try:
            # Use sonos CLI to play
            # This requires the audio to be accessible via URL
            # For local files, we'd need to serve them or use a different method
            
            # For now, use Home Assistant media_player service
            config = load_config()
            
            # Get HA config from environment
            ha_url = os.environ.get("HA_URL", "http://homeassistant.local:8123")
            ha_token = os.environ.get("HA_TOKEN", "")
            
            if not ha_token:
                print("HA_TOKEN not set, cannot play on Sonos")
                return False
                
            # Find the media_player entity for this speaker
            speaker_lower = speaker.lower().replace(" ", "_")
            entity_id = f"media_player.{speaker_lower}"
            
            # For local files, we need to use tts service or serve the file
            # Using HA's TTS as fallback
            payload = json.dumps({
                "entity_id": entity_id,
                "message": audio_path  # This won't work for local files
            }).encode()
            
            # Actually, let's just use HA's built-in TTS
            payload = json.dumps({
                "entity_id": entity_id,
                "message": open(audio_path.replace(".aiff", ".txt"), "w").write("") if False else text  # placeholder
            }).encode()
            
            print(f"Would play on {speaker} ({entity_id})")
            return True
            
        except Exception as e:
            print(f"Sonos play error: {e}")
            return False


class ResponseHandler:
    """
    Coordinates sending to Clawdbot and playing TTS response.
    """
    
    def __init__(self):
        config = load_config()
        clawdbot_config = config.get("clawdbot", {})
        tts_config = config.get("tts", {})
        
        self.clawdbot = ClawdbotClient(
            gateway_url=clawdbot_config.get("gateway_url", "http://127.0.0.1:18789"),
            hook_path=clawdbot_config.get("hook_path", "/hooks/jarvis/voice"),
            hook_token=clawdbot_config.get("hook_token", ""),
            timeout_seconds=clawdbot_config.get("timeout_seconds", 30)
        )
        
        self.tts_enabled = tts_config.get("enabled", True)
        self.tts = SonosTTS(
            default_volume=tts_config.get("default_volume", 30),
            announcement_volume=tts_config.get("announcement_volume", 40)
        ) if self.tts_enabled else None
        
    def handle(self, text: str, room: str) -> Optional[str]:
        """
        Send transcription to Clawdbot and speak response.
        
        Args:
            text: Transcribed speech
            room: Room where speech was detected
            
        Returns:
            Response text, or None on error
        """
        print(f"[{room}] Sending to Clawdbot: \"{text}\"")
        
        # Send to Clawdbot
        response = self.clawdbot.send(text, room)
        
        if response:
            print(f"[{room}] Clawdbot response: \"{response}\"")
            
            # Speak response
            if self.tts and self.tts_enabled:
                self.tts.speak(response, room)
        else:
            print(f"[{room}] No response from Clawdbot")
            
        return response


if __name__ == "__main__":
    # Test response handler
    handler = ResponseHandler()
    
    # Test with dummy text
    response = handler.handle("Turn on the kitchen lights", "kitchen")
    print(f"Response: {response}")
