import depthai as dai
import cv2
import signal
import sys

# Global flag to control the camera loop
camera_running = True

def signal_handler(sig, frame):
    """Handles Ctrl+C to exit gracefully."""
    global camera_running
    print("\nCtrl+C pressed. Exiting...")
    camera_running = False

signal.signal(signal.SIGINT, signal_handler)

def run_camera_feed():
    """Initializes and runs the OAK-D Lite camera feed, displaying it in a window.
    Exits on 'q' key press or Ctrl+C.
    """
    try:
        # Create pipeline
        pipeline = dai.Pipeline()

        # Define source - color camera
        cam_rgb = pipeline.create(dai.node.ColorCamera)
        xout_video = pipeline.create(dai.node.XLinkOut)

        xout_video.setStreamName("video")

        cam_rgb.setBoardSocket(dai.CameraBoardSocket.CAM_A)
        cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)

        cam_rgb.video.link(xout_video.input)

        print("Initializing OAK-D camera...")
        with dai.Device(pipeline) as device:
            print("Connected to Device")
            device_info = device.getDeviceInfo()
            print(f"Device name: {device_info.name}")

            video_queue = device.getOutputQueue(name="video", maxSize=4, blocking=False)

            print("Starting camera feed display...")
            while camera_running:
                in_video = video_queue.get()

                if in_video is not None:
                    frame = in_video.getCvFrame()
                    if frame is not None:
                        cv2.imshow("OAK-Lite Feed", frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("Exiting on 'q' key press.")
                    camera_running = False

    except RuntimeError as e:
        if "Cannot find any device" in str(e):
            print("Error: OAK-D Lite camera not connected or not recognized.")
        else:
            print(f"Runtime Error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        cv2.destroyAllWindows()
        print("Camera feed display stopped.")

if __name__ == '__main__':
    run_camera_feed()