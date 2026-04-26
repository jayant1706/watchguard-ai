"""
WatchGuard AI — WatchDetector
Uses OpenCV's built-in Haar-cascade face detector (no external model needed).
Provides:
  - is_watching(frame, sensitivity)  → bool
  - annotate_frame(frame)            → annotated BGR frame
"""

import cv2
import numpy as np


class WatchDetector:
    """
    Detects whether the user is present (face visible) in a webcam frame.

    Parameters
    ----------
    scale_factor : float
        How much the image size is reduced at each image scale (cascade param).
    min_neighbors : int
        How many neighbours each candidate rectangle should retain.
    min_face_px : int
        Minimum face width/height in pixels; smaller blobs are ignored.
    """

    def __init__(
        self,
        scale_factor: float = 1.15,
        min_neighbors: int = 4,
        min_face_px: int = 60,
    ):
        self._scale_factor  = scale_factor
        self._min_neighbors = min_neighbors
        self._min_face_px   = min_face_px

        # Load the frontal-face cascade that ships with OpenCV
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._face_cascade = cv2.CascadeClassifier(cascade_path)
        if self._face_cascade.empty():
            raise RuntimeError(
                "Could not load OpenCV face cascade. "
                "Reinstall opencv-python: pip install --upgrade opencv-python"
            )

        # Cached detections (updated by annotate_frame / is_watching)
        self._last_faces: list = []

    # ── Public API ────────────────────────────────────────────────────

    def is_watching(self, frame: np.ndarray, sensitivity: float = 0.6) -> bool:
        """
        Return True if at least one face is confidently detected in *frame*.

        sensitivity : 0.1 (lenient) … 1.0 (strict)
            Mapped to min_neighbors so that higher sensitivity = stricter check.
        """
        faces = self._detect(frame, sensitivity)
        return len(faces) > 0

    def annotate_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Return a copy of *frame* with coloured bounding boxes drawn around
        detected faces.  Uses the most recent cached detections so calling
        this after is_watching() costs no extra detection work.
        """
        out = frame.copy()
        for (x, y, w, h) in self._last_faces:
            # Green rectangle
            cv2.rectangle(out, (x, y), (x + w, y + h), (99, 230, 102), 2)
            # Small label
            cv2.putText(
                out, "face",
                (x, max(y - 6, 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (99, 230, 102), 1, cv2.LINE_AA,
            )
        return out

    # ── Internal ──────────────────────────────────────────────────────

    def _detect(self, frame: np.ndarray, sensitivity: float) -> list:
        """Run Haar-cascade detection and cache results."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)   # improve detection in dim conditions

        # Map sensitivity → min_neighbors
        #   sensitivity 1.0  → strict  (high min_neighbors, fewer false positives)
        #   sensitivity 0.1  → lenient (low  min_neighbors, more detections)
        min_n = max(1, int(self._min_neighbors * sensitivity / 0.6))

        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=self._scale_factor,
            minNeighbors=min_n,
            minSize=(self._min_face_px, self._min_face_px),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )

        self._last_faces = list(faces) if len(faces) > 0 else []
        return self._last_faces
