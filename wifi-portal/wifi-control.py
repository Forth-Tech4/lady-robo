from flask import Flask, render_template, request, jsonify
import subprocess
from wifi import Cell
import RPi.GPIO as GPIO  # Although imported, it's not used after removal
import time
import re
import asyncio
import websockets
import json
import threading
import depthai as dai
import cv2
import base64
import glob
import signal
import os
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont

# --- Configuration ---
I2C_ADDR = 0x3C
HOTSPOT_INTERFACE = "wlan0"
PORT = "8081"
FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'
FONT_SIZE = 12

app = Flask(__name__)
connected_websocket = None
camera_running = False

wifi_device = "wlan0"  # Assuming wlan0 for scanning, adjust if needed
hotspot_ssid = "jigo_rpi"
hotspot_password = "Jigo1257@"

# for the audio globals
audio_playing = False
audio_process = None

# --- Global variable to store the current IP address ---
current_ip = None

# --- Initialize I2C Display Globally ---
serial = None
device = None

try:
    serial = i2c(port=1, address=I2C_ADDR)
    device = sh1106(serial, width=128, height=64, rotate=0)
except Exception as e:
    print(f"Error initializing oled: {e}")
    print("Make sure I2C is enabled and the address is correct.")

async def websocket_handler(websocket):
    global connected_websocket
    connected_websocket = websocket
    try:
        async for message in websocket:
            pass
    except websockets.exceptions.ConnectionClosed:
        connected_websocket = None

# --- display text
def display_ip_on_oled(ip_address, port):
    """displays the given text on the I2C display"""
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except IOError:
        print(f"facing the issue with loading the font path :\n{FONT_PATH}")
        font = ImageFont.load_default()
    global device
    if device:
        try:
            with canvas(device) as draw:
                draw.text((0, 0), ip_address, fill=1, font=font)
                draw.text((2, 15), port, fill=1, font=font)
        except Exception as e:
            print(f"Error displaying on OLED: {e}")

def get_ip_address(interface):
    """Get the IP address of a specific network interface."""
    try:
        ip_result = subprocess.run(['ip', 'addr', 'show', 'wlan0'], capture_output=True, text=True)
        ip_address_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_result.stdout)
        if ip_address_match:
            ip_address = ip_address_match.group(1)
            return ip_address_match.group(1)
        else:
            return None
    except ImportError:
        return "netifaces not installed"
    except ValueError:
        return f"Interface {interface} not found"
    except Exception as e:
        return f"IP Error: {e}"

@app.route('/')
def index():
    return render_template('wifi-control.html')

@app.route('/wifi/scan', methods=['GET'])
def wifi_scan():
    try:
        cells = Cell.all('wlan0')
        networks = [cell.ssid for cell in cells if cell.ssid]
        return jsonify(networks)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

@app.route('/camera/test', methods=['GET'])
def camera_test():
    global camera_running
    if camera_running:
        return jsonify({'success': True, 'status': "Camera already running"})
    try:
        available_devices = dai.Device.getAllAvailableDevices()
        if not available_devices:
            return jsonify({
                'success': False,
                'error': "OAK-D Lite camera not connected. Please connect the camera and try again."
            })

        print(f"Found {len(available_devices)} devices")
        for device_info in available_devices:
            print(f" - {device_info.getMxId()}: {device_info.name}")

        # If we get here, at least one camera is available
        camera_running = True
        threading.Thread(target=run_camera_feed, daemon=True).start()
        return jsonify({'success': True, 'status': "Camera feed started"})
    except Exception as e:
        camera_running = False
        error_msg = str(e)
        if "Cannot find any device" in error_msg:
            error_msg = "OAK-D Lite camera not connected or not recognized. Please check the connection."
        return jsonify({'success': False, 'error': f"Failed to start camera: {error_msg}"})

def run_camera_feed():
    global camera_running
    try:
        pipeline = dai.Pipeline()
        cam_rgb = pipeline.create(dai.node.ColorCamera)
        xout_video = pipeline.create(dai.node.XLinkOut)

        xout_video.setStreamName("video")

        cam_rgb.setBoardSocket(dai.CameraBoardSocket.CAM_A)
        cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        # Lower resolution for better performance over WebSocket
        cam_rgb.setVideoSize(640, 480)

        cam_rgb.video.link(xout_video.input)

        print("Initializing OAK-D camera...")
        try:
            with dai.Device(pipeline) as device:
                print("Connected to Device")
                device_info = device.getDeviceInfo()
                print(f"Device name: {device_info.name}")

                time.sleep(1)
                video = device.getOutputQueue(name="video", maxSize=4, blocking=False)

                frames_sent = 0
                last_time = time.time()
                frame_interval = 0.1  # Limit to 10 frames per second to reduce bandwidth

                print("Starting camera feed...")
                while camera_running:
                    videoIn = video.get()
                    current_time = time.time()
                    if videoIn is not None and (current_time - last_time) >= frame_interval:
                        frame = videoIn.getCvFrame()

                        # Resize to reduce bandwidth
                        frame = cv2.resize(frame, (640, 480))

                        # Lower JPEG quality for faster transmission
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                        _, buffer = cv2.imencode('.jpg', frame, encode_param)
                        frame_base64 = base64.b64encode(buffer).decode('utf-8')

                        # Send frame synchronously via our helper
                        success = run_async(send_camera_frame(frame_base64))

                        if success:
                            frames_sent += 1
                            last_time = current_time

                            # Print FPS every 30 frames
                            if frames_sent % 30 == 0:
                                print(f"Camera running, sent {frames_sent} frames")
                        else:
                            print("Failed to send frame - websocket may be disconnected")
                            time.sleep(0.5)  # Wait a bit longer if sending failed

                    time.sleep(0.01)  # Small sleep to prevent CPU hogging
        except RuntimeError as e:
            if "Cannot find any device" in str(e):
                print("OAK-D camera disconnected during operation")
                # Notify the client about the disconnection
                run_async(send_camera_status("Camera disconnected. Please check the connection."))
            raise  # Re-raise the exception to be caught by the outer try block

    except Exception as e:
        print(f"Camera error: {e}")
        # Print the full stack trace for debugging
        import traceback
        traceback.print_exc()

        # Try to send error to client
        error_msg = str(e)
        if "Cannot find any device" in error_msg:
            error_msg = "OAK-D Lite camera not connected or not recognized. Please check the connection."
        run_async(send_camera_status(f"Camera error: {error_msg}"))
    finally:
        camera_running = False
        print("Camera feed stopped")

async def send_camera_frame(frame_base64):
    global connected_websocket
    if connected_websocket:
        try:
            await connected_websocket.send(json.dumps({'type': 'camera_frame', 'frame': frame_base64}))
            return True
        except Exception as e:
            print(f"Error sending camera frame: {e}")
            return False
    return False

@app.route('/speaker/test', methods=['GET'])
def speaker_test():
    """Play a test .wav file from the audio folder"""
    global audio_playing, audio_process
    
    # If already playing, return status
    if audio_playing:
        return jsonify({'success': True, 'status': "Audio already playing"})
    
    try:
        # Check audio devices
        device_result = subprocess.run(['aplay', '-l'], capture_output=True, text=True)
        if "no soundcards found" in device_result.stderr:
            run_async(send_speaker_status("No audio devices found. Please connect a speaker."))
            return jsonify({'success': False, 'error': "No audio devices found. Please connect a speaker."})
        
        # Get first .wav file from audio folder
        audio_files = glob.glob("audio/*.wav")
        if not audio_files:
            run_async(send_speaker_status("No .wav files found in audio folder."))
            return jsonify({'success': False, 'error': "No .wav files found in audio folder."})
        
        # Get first audio file
        audio_file = audio_files[0]
        file_name = os.path.basename(audio_file)
        
        # Start playing audio in a separate thread
        threading.Thread(target=play_audio, args=(audio_file,), daemon=True).start()
        
        return jsonify({'success': True, 'status': f"Playing audio: {file_name}"})
    except Exception as e:
        run_async(send_speaker_status(f"Error playing audio: {str(e)}"))
        return jsonify({'success': False, 'error': f"Error playing audio: {str(e)}"})

def get_audio_jack_alsa_id():
    """Get the ALSA identifiers for the 'headphones' audio device"""
    try:
        process = subprocess.run(['aplay', '-l'], capture_output=True, text=True, check=True)
        output = process.stdout
        for line in output.splitlines():
            if "card" in line and "device" in line and ("Headphones" in line or "headphone" in line):
                match = re.search(r"card (\d+): .*?, device (\d+):", line)
                if match:
                    card_num = match.group(1)
                    device_num = match.group(2)
                    return f"hw:{card_num},{device_num}"
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error running aplay -l: {e}")
        return None

def play_audio(audio_file):
    """Function to play audio file and handle status updates"""
    global audio_playing, audio_process

    try:
        audio_playing = True
        file_name = os.path.basename(audio_file)

        # Send status that audio is starting
        run_async(send_speaker_status(f"Starting playback of {file_name}"))

        alsa_id = get_audio_jack_alsa_id()
        if alsa_id:
            # start the audio playback process, specifying the audio jack
            audio_process = subprocess.Popen(['aplay', '-D', alsa_id, audio_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)

            stdout, stderr = audio_process.communicate()

            if audio_process.returncode == 0:
                run_async(send_speaker_status(f"Finished playing {file_name}"))
            else:
                error_msg = stderr.decode('utf-8') if stderr else "Unknown error"
                run_async(send_speaker_status(f"Error during playback: {error_msg}"))
        else:
            error_message = "Headphone audio device not found"
            print(error_message)
            run_async(send_speaker_status(f"Error: {error_message}"))

    except Exception as e:
        run_async(send_speaker_status(f"Error playing audio: {str(e)}"))
    finally:
        audio_playing = False
        audio_process = None

@app.route('/speaker/stop', methods=['GET'])
def speaker_stop():
    """Stop currently playing audio"""
    global audio_playing, audio_process
    
    if not audio_playing:
        return jsonify({'success': True, 'status': "No audio is playing"})
    
    try:
        # Terminate audio process if it exists
        if audio_process and audio_process.poll() is None:
            # Try to terminate gracefully first
            audio_process.terminate()
            time.sleep(0.1)
            
            # If still running, force kill
            if audio_process.poll() is None:
                os.killpg(os.getpgid(audio_process.pid), signal.SIGTERM)
        
        audio_playing = False
        run_async(send_speaker_status("Audio playback stopped"))
        return jsonify({'success': True, 'status': "Audio playback stopped"})
    except Exception as e:
        return jsonify({'success': False, 'error': f"Error stopping audio: {str(e)}"})


# send speaker status to client html
async def send_speaker_status(status_message):
    global connected_websocket
    if connected_websocket:
        try:
            await connected_websocket.send(json.dumps({
                'type': 'speaker_status', 
                'message': status_message
            }))
            return True
        except Exception as e:
            print(f"Error sending speaker status: {e}")
            return False
    return False


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

def background_ip_task():
    global current_ip
    while True:
        current_ip = get_ip_address(HOTSPOT_INTERFACE)
        display_ip_on_oled(f"{current_ip}", f"{PORT}")
        time.sleep(5)

async def main():
    start_server = websockets.serve(websocket_handler, "0.0.0.0", 8766)
    await start_server
    threading.Thread(target=lambda: background_ip_task()).start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8081, debug=True, use_reloader=False)).start()
    await asyncio.Future()

def run_async(coro):
    """Helper function to run an async coroutine from a synchronous context."""
    asyncio.run(coro)

async def send_camera_status(message):
    """Sends a camera status message to the WebSocket client."""
    global connected_websocket
    if connected_websocket:
        try:
            await connected_websocket.send(json.dumps({'type': 'camera_status', 'message': message}))
        except Exception as e:
            print(f"Error sending camera status: {e}")

def connect_hotspot():
    """Attempts to connect to the default Wi-Fi hotspot."""
    return connect_wifi(hotspot_ssid, hotspot_password)

if __name__ == '__main__':
    asyncio.run(main())
