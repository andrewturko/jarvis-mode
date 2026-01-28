#!/usr/bin/env python3
"""
Jarvis Voice Control UI
Simple web interface to enable/disable voice service on port 8088
"""

from flask import Flask, render_template_string, jsonify, request
import subprocess
import os

app = Flask(__name__)

LAUNCHD_LABEL = "com.jarvis.voice"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Jarvis Voice Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 500px;
            width: 100%;
        }
        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .status {
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 30px;
            text-align: center;
            font-weight: 600;
            font-size: 18px;
        }
        .status.running {
            background: #d4edda;
            color: #155724;
        }
        .status.stopped {
            background: #f8d7da;
            color: #721c24;
        }
        .status.unknown {
            background: #fff3cd;
            color: #856404;
        }
        .controls {
            display: flex;
            gap: 15px;
            margin-bottom: 20px;
        }
        button {
            flex: 1;
            padding: 15px;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        .btn-start {
            background: #28a745;
            color: white;
        }
        .btn-start:hover {
            background: #218838;
        }
        .btn-stop {
            background: #dc3545;
            color: white;
        }
        .btn-stop:hover {
            background: #c82333;
        }
        .btn-refresh {
            background: #6c757d;
            color: white;
        }
        .btn-refresh:hover {
            background: #5a6268;
        }
        .info {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            margin-top: 20px;
        }
        .info h3 {
            font-size: 14px;
            color: #666;
            margin-bottom: 10px;
        }
        .info p {
            font-size: 13px;
            color: #888;
            line-height: 1.6;
        }
        .logs {
            background: #000;
            color: #0f0;
            padding: 15px;
            border-radius: 10px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            max-height: 200px;
            overflow-y: auto;
            margin-top: 20px;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .loading {
            animation: pulse 1.5s ease-in-out infinite;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎤 Jarvis Voice Control</h1>
        <p class="subtitle">UniFi Camera Voice Assistant</p>

        <div id="status" class="status unknown loading">
            Checking status...
        </div>

        <div class="controls">
            <button class="btn-start" onclick="startService()">▶️ Start</button>
            <button class="btn-stop" onclick="stopService()">⏹️ Stop</button>
            <button class="btn-refresh" onclick="updateStatus()">🔄 Refresh</button>
        </div>

        <div class="info">
            <h3>📊 System Info</h3>
            <p id="info">Loading...</p>
        </div>

        <div class="logs" id="logs">
            <div>Logs will appear here...</div>
        </div>
    </div>

    <script>
        async function updateStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                const statusEl = document.getElementById('status');
                statusEl.className = 'status ' + (data.running ? 'running' : 'stopped');
                statusEl.textContent = data.running ? '✅ Voice Service Running' : '⏸️ Voice Service Stopped';
                statusEl.classList.remove('loading');

                const infoEl = document.getElementById('info');
                infoEl.innerHTML = `
                    <strong>Status:</strong> ${data.running ? 'Active' : 'Inactive'}<br>
                    <strong>Cameras:</strong> ${data.cameras || 'N/A'}<br>
                    <strong>Wake Word:</strong> Hey Jarvis<br>
                    <strong>Log File:</strong> /tmp/jarvis-voice.log
                `;

                // Update logs if running
                if (data.running && data.logs) {
                    document.getElementById('logs').innerHTML = data.logs.split('\\n').slice(-15).map(line =>
                        `<div>${line}</div>`
                    ).join('');
                }
            } catch (error) {
                console.error('Failed to update status:', error);
            }
        }

        async function startService() {
            try {
                const response = await fetch('/api/start', { method: 'POST' });
                const data = await response.json();
                alert(data.message);
                setTimeout(updateStatus, 1000);
            } catch (error) {
                alert('Failed to start service: ' + error);
            }
        }

        async function stopService() {
            if (!confirm('Stop voice service?')) return;
            try {
                const response = await fetch('/api/stop', { method: 'POST' });
                const data = await response.json();
                alert(data.message);
                setTimeout(updateStatus, 1000);
            } catch (error) {
                alert('Failed to stop service: ' + error);
            }
        }

        // Update status on load and every 5 seconds
        updateStatus();
        setInterval(updateStatus, 5000);
    </script>
</body>
</html>
"""


def is_service_running():
    """Check if the voice service LaunchAgent is running."""
    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_LABEL],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def get_service_logs():
    """Read recent logs from the service."""
    log_path = "/tmp/jarvis-voice.log"
    try:
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                return f.read()
    except Exception:
        pass
    return ""


@app.route('/')
def index():
    """Main UI page."""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/status')
def status():
    """Get service status."""
    running = is_service_running()
    logs = get_service_logs() if running else ""

    # Count cameras from config
    try:
        import json
        config_path = os.path.join(
            os.path.dirname(__file__),
            '../voice-config.json'
        )
        with open(config_path) as f:
            config = json.load(f)
            cameras = len([c for c in config.get('cameras', {}).values() if c.get('enabled')])
    except Exception:
        cameras = "Unknown"

    return jsonify({
        'running': running,
        'cameras': cameras,
        'logs': logs
    })


@app.route('/api/start', methods=['POST'])
def start():
    """Start the voice service."""
    try:
        subprocess.run(
            ["launchctl", "load", "-w",
             f"/Users/{os.getlogin()}/Library/LaunchAgents/{LAUNCHD_LABEL}.plist"],
            check=True,
            capture_output=True,
            text=True
        )
        return jsonify({'success': True, 'message': 'Voice service started'})
    except subprocess.CalledProcessError as e:
        return jsonify({'success': False, 'message': f'Failed to start: {e.stderr}'}), 500


@app.route('/api/stop', methods=['POST'])
def stop():
    """Stop the voice service."""
    try:
        subprocess.run(
            ["launchctl", "unload", "-w",
             f"/Users/{os.getlogin()}/Library/LaunchAgents/{LAUNCHD_LABEL}.plist"],
            check=True,
            capture_output=True,
            text=True
        )
        return jsonify({'success': True, 'message': 'Voice service stopped'})
    except subprocess.CalledProcessError as e:
        return jsonify({'success': False, 'message': f'Failed to stop: {e.stderr}'}), 500


if __name__ == '__main__':
    print("🎤 Jarvis Voice Control UI")
    print("📡 Running on http://localhost:8088")
    print("🌐 Access from any device: http://<your-ip>:8088")
    app.run(host='0.0.0.0', port=8088, debug=False)
