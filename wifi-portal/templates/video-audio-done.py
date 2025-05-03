from flask import Flask, render_template, request, jsonify
import subprocess
from wifi import Cell, Scheme
import RPi.GPIO as GPIO
import time
import re
import asyncio
import websockets
import json
import threading
import depthai as dai
import cv2
import base64
import numpy as np
from functools import partial
import os
import glob
import signal

# Create a shared event loop that can be accessed from different threads
event_loop = asyncio.new_event_loop()

app = Flask(__name__)
connected_websocket = None  # Global variable to store the WebSocket connection
camera_running = False  # Global var to store camera state

# for the audio globals
audio_playing = False
audio_process = None

# setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO_PIN = 17
GPIO.setwarnings(False)  # disable warnings
GPIO.setup(GPIO_PIN, GPIO.OUT)

async def websocket_handler(websocket, path=None):  # Make path parameter optional
    global connected_websocket
    connected_websocket = websocket
    try:
        async for message in websocket:
            data = json.loads(message)
            if data['type'] == 'zoom':
                # Handle zoom in/out commands if needed
                print(f"Zoom command received: {data['direction']}")
    except websockets.exceptions.ConnectionClosed:
        connected_websocket = None
    except Exception as e:
        print(f"WebSocket error: {e}")
        connected_websocket = None

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

# Helper function to run coroutines from synchronous code
# Fixed run_async function - uses the global event loop
def run_async(coroutine):
    global event_loop
    try:
        # Always use run_coroutine_threadsafe with our global event loop
        future = asyncio.run_coroutine_threadsafe(coroutine, event_loop)
        return future.result(timeout=5)  # 5 second timeout
    except Exception as e:
        print(f"Async operation error: {e}")
        return None

async def send_ip_address(ip_address):
    global connected_websocket
    if connected_websocket:
        print(f"Sending IP address: {ip_address}")
        try:
            await connected_websocket.send(json.dumps({'type': 'ip_address', 'ip': ip_address}))
            return True
        except Exception as e:
            print(f"Error sending IP address: {e}")
            return False
    return False

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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/wifi/scan', methods=['GET'])
def wifi_scan():
    try:
        cells = Cell.all('wlan0')
        networks = [{'ssid': cell.ssid, 'signal': cell.signal} for cell in cells if cell.ssid]
        # Sort by signal strength (higher is better)
        networks.sort(key=lambda x: x['signal'], reverse=True)
        return jsonify({'success': True, 'networks': networks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/wifi/connect', methods=['POST'])
def wifi_connect():
    data = request.get_json()
    ssid = data.get('ssid')
    password = data.get('password')
    
    if not ssid:
        return jsonify({'success': False, 'error': "SSID is required."})
    
    try:
        # Disable AP mode
        subprocess.run(['sudo', 'systemctl', 'stop', 'hostapd'], check=True)
        subprocess.run(['sudo', 'systemctl', 'stop', 'dnsmasq'], check=True)

        # Using the nmcli network manager CLI
        connect_command = ['sudo', 'nmcli', 'device', 'wifi', 'connect', ssid]
        if password:
            connect_command.extend(['password', password])
        
        subprocess.run(connect_command, check=True, timeout=30)

        # Check if the connection was successful
        time.sleep(5)  # Give it some time to connect
        result = subprocess.run(['nmcli', 'connection', 'show', ssid], capture_output=True, text=True)
        
        if result.returncode == 0:
            # Get the new IP address
            ip_result = subprocess.run(['ip', 'addr', 'show', 'wlan0'], capture_output=True, text=True)
            ip_address_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_result.stdout)
            
            if ip_address_match:
                new_ip_address = ip_address_match.group(1)
                # Send the new IP address through WebSocket - use our helper function
                run_async(send_ip_address(new_ip_address))
                return jsonify({'success': True, 'message': "Wi-Fi connected successfully.", 'ip': new_ip_address})
            else:
                raise subprocess.CalledProcessError(1, "Failed to get new IP address.")
        else:
            raise subprocess.CalledProcessError(1, "nmcli connection failed.")
    except subprocess.CalledProcessError as e:
        # Connection failed, restart AP mode
        subprocess.run(['sudo', 'systemctl', 'start', 'hostapd'], check=True)
        subprocess.run(['sudo', 'systemctl', 'start', 'dnsmasq'], check=True)
        return jsonify({'success': False, 'error': f"Wi-Fi connection failed: {str(e)}"})
    except subprocess.TimeoutExpired:
        # Connection timed out, restart AP mode
        subprocess.run(['sudo', 'systemctl', 'start', 'hostapd'], check=True)
        subprocess.run(['sudo', 'systemctl', 'start', 'dnsmasq'], check=True)
        return jsonify({'success': False, 'error': "Wi-Fi connection timed out. Please check SSID and password."})
    except Exception as e:
        # Any other exception, restart AP mode
        try:
            subprocess.run(['sudo', 'systemctl', 'start', 'hostapd'], check=True)
            subprocess.run(['sudo', 'systemctl', 'start', 'dnsmasq'], check=True)
        except:
            pass  # Ignore errors in cleanup
        return jsonify({'success': False, 'error': f"Wi-Fi connection error: {str(e)}"})

@app.route('/gpio/status', methods=['GET'])
def gpio_status():
    status = GPIO.input(GPIO_PIN)
    return jsonify({'success': True, 'status': status})

@app.route('/gpio/toggle', methods=['POST'])
def gpio_toggle():
    try:
        current_status = GPIO.input(GPIO_PIN)
        GPIO.output(GPIO_PIN, not current_status)  # toggle the gpio
        new_status = GPIO.input(GPIO_PIN)
        return jsonify({'success': True, 'status': new_status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/camera/test', methods=['GET'])
def camera_test():
    global camera_running

    if camera_running:
        return jsonify({'success': True, 'status': "Camera already running"})

    # First check if OAK-D camera is available before starting the thread
    try:
        # Try to get available devices
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

@app.route('/camera/stop', methods=['GET'])
def camera_stop():
    global camera_running
    if camera_running:
        camera_running = False
        time.sleep(0.5)  # Give the camera thread time to stop
        return jsonify({'success': True, 'status': "Camera feed stopped"})
    else:
        return jsonify({'success': True, 'status': "Camera not running"})

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

@app.route('/speaker/check', methods=['GET'])
def speaker_check():
    """Check if audio device is available"""
    try:
        # Run the aplay -l command to list audio devices
        result = subprocess.run(['aplay', '-l'], capture_output=True, text=True)
        
        if "no soundcards found" in result.stderr:
            return jsonify({'success': False, 'error': "No audio devices found. Please connect a speaker."})
        
        # Get list of audio files
        audio_files = glob.glob("audio/*.wav")
        if not audio_files:
            return jsonify({'success': False, 'error': "No .wav files found in audio folder."})
        
        # Return success with list of available audio files
        audio_file_names = [os.path.basename(file) for file in audio_files]
        return jsonify({
            'success': True, 
            'message': "Audio device and files found", 
            'files': audio_file_names
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f"Error checking audio: {str(e)}"})

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

@app.route('/speaker/play/<filename>', methods=['GET'])
def speaker_play_file(filename):
    """Play a specific .wav file from the audio folder"""
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
        
        # Check if file exists
        audio_file = os.path.join("audio", filename)
        if not os.path.exists(audio_file) or not os.path.isfile(audio_file):
            run_async(send_speaker_status(f"Audio file '{filename}' not found."))
            return jsonify({'success': False, 'error': f"Audio file '{filename}' not found."})
        
        # Start playing audio in a separate thread
        threading.Thread(target=play_audio, args=(audio_file,), daemon=True).start()
        
        return jsonify({'success': True, 'status': f"Playing audio: {filename}"})
    except Exception as e:
        run_async(send_speaker_status(f"Error playing audio: {str(e)}"))
        return jsonify({'success': False, 'error': f"Error playing audio: {str(e)}"})

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

def play_audio(audio_file):
    """Function to play audio file and handle status updates"""
    global audio_playing, audio_process
    
    try:
        audio_playing = True
        file_name = os.path.basename(audio_file)
        
        # Send status that audio is starting
        run_async(send_speaker_status(f"Starting playback of {file_name}"))
        
        # Start the audio playback process
        audio_process = subprocess.Popen(
            ['aplay', audio_file], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid  # This allows us to kill the process group later if needed
        )
        
        # Wait for the process to complete
        stdout, stderr = audio_process.communicate()
        
        # Check if process completed successfully
        if audio_process.returncode == 0:
            run_async(send_speaker_status(f"Finished playing {file_name}"))
        else:
            error_msg = stderr.decode('utf-8') if stderr else "Unknown error"
            run_async(send_speaker_status(f"Error during playback: {error_msg}"))
            
    except Exception as e:
        run_async(send_speaker_status(f"Error playing audio: {str(e)}"))
    finally:
        audio_playing = False
        audio_process = None

# Add a new function to send camera status messages
async def send_camera_status(status_message):
    global connected_websocket
    if connected_websocket:
        try:
            await connected_websocket.send(json.dumps({
                'type': 'camera_status', 
                'message': status_message
            }))
            return True
        except Exception as e:
            print(f"Error sending camera status: {e}")
            return False
    return False

def start_event_loop():
    """Function to run the event loop in a separate thread"""
    asyncio.set_event_loop(event_loop)
    event_loop.run_forever()

# Add a function to periodically check audio devices and send updates
def monitor_audio_devices():
    """Thread function to monitor audio devices and send status updates if changed"""
    last_state = None
    
    while True:
        try:
            # Check if audio device is available
            result = subprocess.run(['aplay', '-l'], capture_output=True, text=True)
            current_state = "no soundcards found" not in result.stderr
            
            # If state changed, send update
            if last_state is not None and current_state != last_state:
                if current_state:
                    run_async(send_speaker_status("Audio device connected"))
                else:
                    run_async(send_speaker_status("Audio device disconnected"))
            
            last_state = current_state
            
            # Sleep for a while before checking again
            time.sleep(5)
        except Exception:
            # Ignore errors in monitoring thread
            time.sleep(5)

async def start_websocket_server():
    """Start the WebSocket server"""
    start_server = websockets.serve(websocket_handler, "0.0.0.0", 8765)
    await start_server
    print("WebSocket server started on port 8765")

if __name__ == '__main__':
    # Start the event loop in a separate thread
    threading.Thread(target=start_event_loop, daemon=True).start()
    
    # Start the websocket server using the shared event loop
    asyncio.run_coroutine_threadsafe(start_websocket_server(), event_loop)
    
    # Run Flask application
    app.run(host='0.0.0.0', debug=True, use_reloader=False)


