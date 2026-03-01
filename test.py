import subprocess
import numpy as np
import cv2

RTSP_URL = "rtsp://192.168.1.158:554/mainStream10"

# If you know camera resolution, set these.
# If not, use Option C first to detect width/height automatically.
W, H = 1920, 1080

cmd = [
    "ffmpeg",
    "-rtsp_transport", "tcp",
    "-stimeout", "5000000",          # 5s connect timeout
    "-i", RTSP_URL,
    "-an",
    "-fflags", "nobuffer",
    "-flags", "low_delay",
    "-f", "rawvideo",
    "-pix_fmt", "bgr24",
    "-vsync", "0",
    "pipe:1",
]

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**8)

frame_size = W * H * 3

print("FFmpeg stream started. Press q to quit.")

while True:
    raw = proc.stdout.read(frame_size)
    if len(raw) != frame_size:
        print("Frame incomplete / stream dropped. Exiting...")
        break

    frame = np.frombuffer(raw, np.uint8).reshape((H, W, 3))

    cv2.imshow("RTSP via FFmpeg", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

proc.terminate()
cv2.destroyAllWindows()