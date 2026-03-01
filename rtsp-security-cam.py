import av
import cv2
import time
from datetime import datetime

# ================== CONFIG ==================
CAMERA_RTSP = "rtsp://admin:admin123@192.168.1.158:554/subStream6"

# CAMERA_RTSP = "rtsp://admin:admin123@192.168.1.158:554/mainStream1"
WINDOW_NAME = "RTSP Stream Viewer (PyAV / FFmpeg)"
RECONNECT_DELAY = 2  # seconds

# rtsp://admin:admin123@192.168.1.158:554/Streaming/Channels/1

# ================== FFmpeg / PyAV OPTIONS ==================
# Notes:
# - rtsp_transport=tcp helps avoid UDP packet loss (common reason for stream dropping)
# - stimeout sets connect timeout (microseconds)
# - rw_timeout can help during read stalls (microseconds)
# - buffer_size increases socket buffer to reduce drops
# - fflags=nobuffer + flags=low_delay reduces latency buffering
# - reconnect_* options attempt reconnect on failures (works on many FFmpeg builds)

ffmpeg_options = {
    "rtsp_transport": "tcp",
    "stimeout": "10000000",       # 10s connect timeout (us)
    "rw_timeout": "10000000",     # 10s read timeout (us)
    "buffer_size": "10485760",    # 10 MB socket buffer
    "max_delay": "500000",        # 0.5s max demux delay (us)
    "analyzeduration": "1000000", # 1s
    "probesize": "1000000",       # 1MB
    # Reconnect behavior (if your FFmpeg build supports it)
    "reconnect": "1",
    "reconnect_streamed": "1",
    "reconnect_delay_max": "2",
}

print("RTSP Stream Viewer (Direct Camera → PyAV/FFmpeg)")
print("=" * 60)
print(f"Camera RTSP: {CAMERA_RTSP}")
print("Press 'q' to quit.\n")

cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

while True:
    container = None
    try:
        print(f"{datetime.now().strftime('%H:%M:%S')} - Connecting (PyAV/FFmpeg)...")

        container = av.open(CAMERA_RTSP, options=ffmpeg_options)

        # Pick the first video stream
        video_stream = next((s for s in container.streams if s.type == "video"), None)
        if video_stream is None:
            raise RuntimeError("No video stream found in RTSP feed")

        # Lower decode latency (helps for live streams)
        video_stream.thread_type = "AUTO"

        print(f"{datetime.now().strftime('%H:%M:%S')} - Connected")
        print(f"  → Resolution: {video_stream.width}x{video_stream.height}")
        print(f"  → Codec: {video_stream.codec_context.name}")
        print(f"  → FPS: {video_stream.average_rate}")

        # Decode loop
        for frame in container.decode(video=0):
            img = frame.to_ndarray(format="bgr24")
            cv2.imshow(WINDOW_NAME, img)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("Closing...")
                raise KeyboardInterrupt

    except KeyboardInterrupt:
        break

    except (av.AVError, OSError, RuntimeError) as e:
        # AVError covers many FFmpeg read/decode/network issues
        print(f"{datetime.now().strftime('%H:%M:%S')} - Stream error: {type(e).__name__}: {e}")
        print(f"Reconnecting in {RECONNECT_DELAY} seconds...\n")
        time.sleep(RECONNECT_DELAY)

    except Exception as e:
        print(f"{datetime.now().strftime('%H:%M:%S')} - Unexpected error: {type(e).__name__}: {e}")
        print(f"Reconnecting in {RECONNECT_DELAY} seconds...\n")
        time.sleep(RECONNECT_DELAY)

    finally:
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

cv2.destroyAllWindows()
print("Exited cleanly.")