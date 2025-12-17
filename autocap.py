from picamera2 import Picamera2
from libcamera import controls
import time
import os
import re
import cv2
import serial
from PIL import Image

ser = serial.Serial(
    port='/dev/serial0',
    baudrate=115200,
    timeout=1
)
time.sleep(2)
picam = Picamera2(0)

config = picam.create_preview_configuration(
    main={"size": (1280, 1080), "format": "BGR888"}
)

def get_next_filename(directory, prefix="image", ext=".jpg"):
    os.makedirs(directory, exist_ok=True)

    files = os.listdir(directory)
    numbers = []

    for f in files:
        match = re.match(rf"{prefix}(\d+){ext}", f)
        if match:
            numbers.append(int(match.group(1)))

    next_num = max(numbers) + 1 if numbers else 1
    return os.path.join(directory, f"{prefix}{next_num}{ext}")

save_directory = f'/cam/pictures'

picam.configure(config)

picam.start()
picam.set_controls({'AfMode': controls.AfModeEnum.Continuous})

print("Picam started.")

try:
    while True:
        jpg_path = get_next_filename(save_directory, "image", ".jpg")
        picam.capture_file(jpg_path)

        # VVV   This one for webp use   VVV

        # webp_path = get_next_filename(save_directory, "image", ".webp")
        # frame = picam.capture_array()
        # small = cv2.resize(frame, (240, 240))
        # cv2.imwrite(webp_path, small)
        # img = Image.open(webp_path)
        # img.save(webp_path, 'webp', quality=25)

        ser.write("GG")
        time.sleep(1)
        line_bytes = ser.readline()
        if line_bytes:
            decoded_line = line_bytes.decode('utf-8').rstrip()
        gps_line = decoded_line.strip().split(',')
        lat = gps_line[0]
        lon = gps_line[1]
        t = time.localtime()
        c = time.strftime("%H:%M:%S", t)
        with open('image_log.txt', 'a') as l:
            l.write(f"Image saved at : {c}\nGPS position at : lat [{lat}], lon [{lon}]")        
        time.sleep(60)

except Exception as e:
    print(f"An error occred {e}")

picam.stop()