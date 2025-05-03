#!/usr/bin/env python3
import RPi.GPIO as GPIO
import asyncio
import time
import os
import subprocess
import signal
import logging
import pathlib

# Setup logging directory and file
LOG_DIR = "/var/log"
LOG_FILE = "system_monitor.log"
LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)

# Create log directory if it doesn't exist
def setup_logging():
    try:
        # Check if we can write to /var/log, if not use home directory
        if not os.access(LOG_DIR, os.W_OK):
            home_dir = os.path.expanduser("~")
            LOG_DIR = home_dir
            LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)
            print(f"Cannot write to /var/log, using {LOG_PATH} instead")
        
        # Ensure the directory exists
        pathlib.Path(os.path.dirname(LOG_PATH)).mkdir(parents=True, exist_ok=True)
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(LOG_PATH),
                logging.StreamHandler()
            ]
        )
        return logging.getLogger(__name__)
    except Exception as e:
        # Fallback to console-only logging if file logging fails
        print(f"Error setting up log file: {e}")
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler()]
        )
        return logging.getLogger(__name__)

# Initialize logger
logger = setup_logging()

# Constants
GPIO_LED_PIN = 24
GPIO_BUTTON_PIN = 23
INTERNET_CHECK_INTERVAL = 1  # seconds
BUTTON_POLL_INTERVAL = 0.1  # seconds
SHUTDOWN_HOLD_TIME = 3  # seconds to hold button for shutdown
PING_TIMEOUT = 1  # seconds

# Setup GPIO
def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(GPIO_LED_PIN, GPIO.OUT)
    GPIO.setup(GPIO_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    logger.info("GPIO pins configured")

def cleanup_gpio():
    GPIO.cleanup()
    logger.info("GPIO cleaned up")

# LED patterns
async def startup_sequence():
    """Indicate system startup with rapid blinking sequence"""
    logger.info("Performing startup LED sequence")
    for _ in range(5):  # Flash quickly 5 times
        GPIO.output(GPIO_LED_PIN, GPIO.HIGH)
        await asyncio.sleep(0.1)
        GPIO.output(GPIO_LED_PIN, GPIO.LOW)
        await asyncio.sleep(0.1)
    
async def shutdown_sequence():
    """Indicate system shutdown with fading pattern"""
    logger.info("Performing shutdown LED sequence")
    pwm = GPIO.PWM(GPIO_LED_PIN, 100)  # Create PWM instance with 100Hz frequency
    pwm.start(0)  # Start with LED off
    
    # Brighten
    for duty in range(0, 101, 5):
        pwm.ChangeDutyCycle(duty)
        await asyncio.sleep(0.05)
    
    # Dim
    for duty in range(100, -1, -5):
        pwm.ChangeDutyCycle(duty)
        await asyncio.sleep(0.05)
    
    pwm.stop()

def check_internet():
    """Check internet connectivity by pinging Google's DNS"""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(PING_TIMEOUT), '8.8.8.8'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error checking internet: {e}")
        return False

async def blink_led(stop_event):
    """
    Manage LED indications:
    - Internet available: LED on steadily
    - No internet: LED blinks
    """
    try:
        logger.info("Starting LED monitoring task")
        
        # Perform startup sequence
        await startup_sequence()
        
        while not stop_event.is_set():
            if check_internet():
                GPIO.output(GPIO_LED_PIN, GPIO.HIGH)  # Steady on when internet available
                await asyncio.sleep(INTERNET_CHECK_INTERVAL)
            else:
                # Blink when no internet
                GPIO.output(GPIO_LED_PIN, GPIO.HIGH)
                await asyncio.sleep(0.5)
                GPIO.output(GPIO_LED_PIN, GPIO.LOW)
                await asyncio.sleep(0.5)
                
    except asyncio.CancelledError:
        logger.info("LED task cancelled")
    except Exception as e:
        logger.error(f"Error in LED task: {e}")

async def monitor_button(stop_event):
    """Monitor button presses for shutdown"""
    try:
        logger.info("Starting button monitoring task")
        button_press_time = 0
        
        while not stop_event.is_set():
            # Button is pressed (LOW because of pull-up)
            if GPIO.input(GPIO_BUTTON_PIN) == GPIO.LOW:
                if button_press_time == 0:  # First detection of press
                    button_press_time = time.time()
                    logger.info("Button press detected")
                
                # Check if button has been held long enough for shutdown
                elif time.time() - button_press_time >= SHUTDOWN_HOLD_TIME:
                    logger.warning(f"Shutdown initiated after {SHUTDOWN_HOLD_TIME}s button press")
                    stop_event.set()
                    await shutdown_sequence()
                    
                    # Execute shutdown command
                    logger.warning("Executing system shutdown")
                    subprocess.run(['sudo', 'shutdown', '-h', 'now'])
                    break
            else:
                # Reset timer when button is released
                if button_press_time != 0:
                    logger.debug("Button released")
                    button_press_time = 0
                    
            await asyncio.sleep(BUTTON_POLL_INTERVAL)
            
    except asyncio.CancelledError:
        logger.info("Button monitoring task cancelled")
    except Exception as e:
        logger.error(f"Error in button monitoring task: {e}")

async def main():
    stop_event = asyncio.Event()
    
    # Setup signal handlers for graceful termination
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, initiating shutdown")
        stop_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal_handler)
    
    setup_gpio()
    
    try:
        # Create tasks
        led_task = asyncio.create_task(blink_led(stop_event))
        button_task = asyncio.create_task(monitor_button(stop_event))
        
        # Wait for tasks to complete
        await asyncio.gather(led_task, button_task)
        
    except Exception as e:
        logger.error(f"Error in main function: {e}")
    finally:
        # Ensure cleanup happens
        cleanup_gpio()
        logger.info("System monitor stopped")

if __name__ == '__main__':
    try:
        # Log startup information
        logger.info("System monitor starting")
        logger.info(f"Using log file: {LOG_PATH}")
        
        # Run the main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service stopped by keyboard interrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
