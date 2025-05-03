import socket
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

def get_ip_address(interface):
    """Get the IP address of a specific network interface."""
    try:
        import netifaces
        addresses = netifaces.ifaddresses(interface)
        if socket.AF_INET in addresses:
            return addresses[socket.AF_INET][0]['addr']
        else:
            return f"No IP on {interface}"
    except ImportError:
        return "netifaces not installed"
    except ValueError:
        return f"Interface {interface} not found"
    except Exception as e:
        return f"IP Error: {e}"

try:
    # Initialize I2C
    serial = i2c(port=1, address=I2C_ADDR)
    device = sh1106(serial, width=128, height=64, rotate=0)
    
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except IOError:
        print(f"facing the issue with loading the font path :\n{FONT_PATH}")
        font = ImageFont.load_default()

    def display_text(text, text2):
        with canvas(device) as draw:
            draw.text((0, 0), text, fill=1, font=font)
            draw.text((2,17), text2, fill=1, font=font)

    # Get and display the IP address continuously
    while True:
        ip_address = get_ip_address(HOTSPOT_INTERFACE)
        display_text(f"{ip_address}",f"{PORT}")
        # You can adjust the sleep duration if needed, but keeping it running
        # without a significant delay will keep the display updated.
        # time.sleep(1)
except Exception as e:
    print(f"OLED Error: {e}")
    print("Check I2C enabled and address.")
