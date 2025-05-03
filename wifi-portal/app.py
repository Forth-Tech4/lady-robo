from flask import Flask, render_template, request, jsonify
import subprocess
from wifi import Cell, Scheme
import json
import os

app = Flask(__name__)

wifi_device = "wlan0"  # Assuming wlan0 for scanning, adjust if needed
hotspot_ssid = "jigo_rpi"
hotspot_password = "Jigo1257@"

def connect_wifi(ssid, password):
    """Attempts to connect to the specified Wi-Fi network using nmcli."""
    try:
        # Check if a connection with the SSID already exists
        check_connection_command = f"nmcli con show '{ssid}'"
        subprocess.run(check_connection_command, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        connect_command = f"nmcli con up id '{ssid}'"
    except subprocess.CalledProcessError:
        # Connection doesn't exist, so create a new one
        connect_command = f"nmcli dev wifi connect '{ssid}' password '{password}' ifname '{wifi_device}'"

    process = subprocess.Popen(connect_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(timeout=30)  # Add a timeout to prevent indefinite blocking

    if process.returncode == 0:
        return True, stdout.decode()
    else:
        return False, stderr.decode()

def connect_hotspot():
    """Connects to the default hotspot."""
    return connect_wifi(hotspot_ssid, hotspot_password)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/wifi/scan')
def wifi_scan():
    try:
        cells = Cell.all(wifi_device)
        networks = sorted(list(set([cell.ssid for cell in cells if cell.ssid]))) # Remove duplicates and sort
        return jsonify(networks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/wifi/connect', methods=['POST'])
def wifi_connect():
    data = request.get_json()
    ssid = data.get('ssid')
    password = data.get('password')

    if not ssid:
        return jsonify({"error": "SSID is required"}), 400

    success, message = connect_wifi(ssid, password)

    if success:
        return jsonify({"message": f"Successfully connected to {ssid}", "details": message})
    else:
        print(f"Connection to '{ssid}' failed: {message}")
        print("Attempting to connect to the default hotspot...")
        hotspot_success, hotspot_message = connect_hotspot()
        if hotspot_success:
            return jsonify({"message": f"Failed to connect to '{ssid}'. Connected to default hotspot '{hotspot_ssid}'.", "details": hotspot_message})
        else:
            return jsonify({"error": f"Failed to connect to '{ssid}' and also failed to connect to the default hotspot '{hotspot_ssid}'. Details: {hotspot_message}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8081)
