"""
download_model.py
-----------------
Downloads the MediaPipe FaceLandmarker model required by detector.py (v4).
Run this once before starting WatchGuard:

    python download_model.py
"""

import urllib.request
import os
import sys

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)

SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "face_landmarker.task")

def download():
    if os.path.isfile(SAVE_PATH):
        size_mb = os.path.getsize(SAVE_PATH) / 1_000_000
        print(f"[download_model] Model already exists ({size_mb:.1f} MB): {SAVE_PATH}")
        return

    print(f"[download_model] Downloading FaceLandmarker model...")
    print(f"  From: {MODEL_URL}")
    print(f"  To:   {SAVE_PATH}")

    def progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            mb  = downloaded / 1_000_000
            sys.stdout.write(f"\r  {pct}%  ({mb:.1f} MB)")
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(MODEL_URL, SAVE_PATH, reporthook=progress)
        print()
        size_mb = os.path.getsize(SAVE_PATH) / 1_000_000
        print(f"[download_model] Done — {size_mb:.1f} MB saved to {SAVE_PATH}")
    except Exception as e:
        print(f"\n[download_model] ERROR: {e}")
        print("  Check your internet connection and try again.")
        sys.exit(1)

if __name__ == "__main__":
    download()
