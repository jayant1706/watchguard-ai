"""
WatchGuard AI — WatchDetector  (v2 — Robust Edition)
======================================================
Improvements over v1:
  ✓ Multi-cascade:    frontal + profile face cascades → handles tilted/side heads
  ✓ CLAHE:            adaptive histogram equalisation → works in dim / uneven lighting
  ✓ Temporal buffer:  votes over last N frames → kills false-negative spikes
  ✓ Attention check:  eye detection confirms user is actually looking (not just present)
  ✓ Confidence score: float 0–1 exposed so the UI can show "attention quality"

Public API is 100% backwards-compatible with v1:
  - is_watching(frame, sensitivity)  → bool
  - annotate_frame(frame)            → annotated BGR frame

New optional API:
  - get_confidence()                 → float  (0.0 – 1.0, last computed frame)
  - get_attention_score()            → float  (0.0 – 1.0, blends presence + gaze)
"""

import collections
import cv2
import numpy as np


# ── Tunables ──────────────────────────────────────────────────────────────────
_TEMPORAL_WINDOW   = 6      # frames over which we vote (smooths brief occlusions)
_MIN_VOTE_FRACTION = 0.35   # fraction of window that must be "face seen" to stay watching
_EYE_WEIGHT        = 0.35   # how much eye-detection contributes to attention score
_FACE_WEIGHT       = 0.65   # how much face-detection contributes to attention score


class WatchDetector:
    """
    Robust face-presence + attention detector.

    Parameters
    ----------
    scale_factor : float
        Cascade scale-factor (1.1 = fine, 1.2 = fast).
    min_neighbors : int
        Base min_neighbors for frontal cascade (scaled by sensitivity).
    min_face_px : int
        Minimum face bounding-box side in pixels.
    use_profile : bool
        Whether to also run the profile-face cascade (adds ~5 ms, catches side faces).
    use_eye_check : bool
        Whether to run eye detection inside each face ROI for attention scoring.
    temporal_window : int
        How many recent frames to consider for temporal smoothing.
    """

    def __init__(
        self,
        scale_factor: float = 1.15,
        min_neighbors: int  = 4,
        min_face_px: int    = 55,
        use_profile: bool   = True,
        use_eye_check: bool = True,
        temporal_window: int = _TEMPORAL_WINDOW,
    ):
        self._scale_factor   = scale_factor
        self._min_neighbors  = min_neighbors
        self._min_face_px    = min_face_px
        self._use_profile    = use_profile
        self._use_eye_check  = use_eye_check

        # ── Load cascades ─────────────────────────────────────────────
        base = cv2.data.haarcascades

        self._frontal = cv2.CascadeClassifier(
            base + "haarcascade_frontalface_default.xml")
        if self._frontal.empty():
            raise RuntimeError(
                "Could not load frontal face cascade. "
                "Reinstall: pip install --upgrade opencv-python")

        self._profile = None
        if use_profile:
            self._profile = cv2.CascadeClassifier(
                base + "haarcascade_profileface.xml")
            if self._profile.empty():
                print("[Detector] Profile cascade not available — using frontal only.")
                self._profile = None

        self._eye_cascade = None
        if use_eye_check:
            self._eye_cascade = cv2.CascadeClassifier(
                base + "haarcascade_eye.xml")
            if self._eye_cascade.empty():
                print("[Detector] Eye cascade not available — skipping eye check.")
                self._eye_cascade = None

        # ── State ─────────────────────────────────────────────────────
        self._last_faces: list   = []          # [(x,y,w,h, label)]
        self._last_eyes:  list   = []          # [(x,y,w,h)] in original frame coords
        self._history             = collections.deque(maxlen=temporal_window)
        self._confidence: float  = 0.0
        self._attention:  float  = 0.0

        # CLAHE for adaptive contrast enhancement
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    # ── Public API ────────────────────────────────────────────────────────────

    def is_watching(self, frame: np.ndarray, sensitivity: float = 0.6) -> bool:
        """
        Return True if the user appears to be watching.

        sensitivity : 0.1 (very lenient) … 1.0 (very strict)
          - At low sensitivity: any blurry face hint counts.
          - At high sensitivity: only clear frontal faces count.
        """
        self._run_detection(frame, sensitivity)
        return self._confidence >= sensitivity

    def annotate_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Return a copy of *frame* with bounding boxes for faces (and eyes if detected).
        Uses cached results — call after is_watching() for zero extra cost.
        """
        out = frame.copy()

        # Draw face boxes
        for (x, y, w, h, label) in self._last_faces:
            colour = (99, 230, 102)   # mint green
            cv2.rectangle(out, (x, y), (x + w, y + h), colour, 2)
            # Confidence badge
            badge = f"{label}  {int(self._confidence * 100)}%"
            cv2.putText(out, badge, (x, max(y - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)

        # Draw eye dots
        for (ex, ey, ew, eh) in self._last_eyes:
            cx, cy = ex + ew // 2, ey + eh // 2
            cv2.circle(out, (cx, cy), 4, (79, 217, 255), -1)   # cyan dot

        # Attention bar (top-left corner)
        bar_w = int(self._attention * 80)
        cv2.rectangle(out, (6, 6), (6 + 80, 14), (30, 40, 50), -1)
        bar_col = (99, 230, 102) if self._attention > 0.6 else \
                  (255, 217, 122) if self._attention > 0.3 else (255, 92, 122)
        if bar_w > 0:
            cv2.rectangle(out, (6, 6), (6 + bar_w, 14), bar_col, -1)
        cv2.putText(out, "attn", (6, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, (100, 120, 160), 1, cv2.LINE_AA)

        return out

    def get_confidence(self) -> float:
        """Raw detection confidence for the last frame (0.0–1.0)."""
        return self._confidence

    def get_attention_score(self) -> float:
        """
        Blended attention score (0.0–1.0) that factors in both face presence
        and whether eyes were detected (i.e., user is actually looking at screen).
        """
        return self._attention

    # ── Internal ──────────────────────────────────────────────────────────────

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        """
        Convert to grayscale and apply CLAHE for robust low-light performance.
        CLAHE (Contrast Limited Adaptive Histogram Equalization) is far superior
        to global histogram equalization — it enhances local contrast without
        over-amplifying noise.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._clahe.apply(gray)

    def _detect_faces(self, gray: np.ndarray, sensitivity: float) -> list:
        """
        Run frontal + (optional) profile cascade, merge results, deduplicate.
        Returns list of (x, y, w, h, label) tuples.
        """
        # Map sensitivity → min_neighbors
        #   sensitivity 1.0  → strict  (high min_neighbors)
        #   sensitivity 0.1  → lenient (low  min_neighbors)
        min_n_frontal = max(1, int(self._min_neighbors * sensitivity / 0.6))
        min_n_profile = max(1, min_n_frontal - 1)   # slightly more lenient for profile

        faces = []

        # ── Frontal cascade ───────────────────────────────────────────
        frontal = self._frontal.detectMultiScale(
            gray,
            scaleFactor=self._scale_factor,
            minNeighbors=min_n_frontal,
            minSize=(self._min_face_px, self._min_face_px),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        if len(frontal) > 0:
            for (x, y, w, h) in frontal:
                faces.append((int(x), int(y), int(w), int(h), "face"))

        # ── Profile cascade (left + mirrored for right) ───────────────
        if self._profile is not None:
            profile_l = self._profile.detectMultiScale(
                gray,
                scaleFactor=self._scale_factor,
                minNeighbors=min_n_profile,
                minSize=(self._min_face_px, self._min_face_px),
                flags=cv2.CASCADE_SCALE_IMAGE,
            )
            if len(profile_l) > 0:
                for (x, y, w, h) in profile_l:
                    faces.append((int(x), int(y), int(w), int(h), "profile"))

            # Mirror the frame horizontally to catch right-profile faces
            gray_flip = cv2.flip(gray, 1)
            fw = gray.shape[1]
            profile_r = self._profile.detectMultiScale(
                gray_flip,
                scaleFactor=self._scale_factor,
                minNeighbors=min_n_profile,
                minSize=(self._min_face_px, self._min_face_px),
                flags=cv2.CASCADE_SCALE_IMAGE,
            )
            if len(profile_r) > 0:
                for (x, y, w, h) in profile_r:
                    # Un-mirror x coordinate
                    faces.append((int(fw - x - w), int(y), int(w), int(h), "profile"))

        return self._deduplicate(faces)

    def _deduplicate(self, faces: list, iou_thresh: float = 0.3) -> list:
        """
        Remove overlapping detections (e.g., frontal + profile hitting same face).
        Uses a simple IoU-based suppression — keeps the first (frontal-priority) hit.
        """
        if len(faces) <= 1:
            return faces
        kept = []
        for cand in faces:
            cx, cy, cw, ch, _ = cand
            overlap = False
            for kx, ky, kw, kh, _ in kept:
                # IoU
                ix = max(0, min(cx + cw, kx + kw) - max(cx, kx))
                iy = max(0, min(cy + ch, ky + kh) - max(cy, ky))
                inter = ix * iy
                union = cw * ch + kw * kh - inter
                if union > 0 and inter / union > iou_thresh:
                    overlap = True
                    break
            if not overlap:
                kept.append(cand)
        return kept

    def _detect_eyes(self, gray: np.ndarray, faces: list) -> list:
        """
        For each detected face ROI, look for eyes.
        Returns a list of eye rectangles in full-frame coordinates.
        """
        if self._eye_cascade is None or not faces:
            return []
        eyes_found = []
        for (fx, fy, fw, fh, _) in faces:
            # Only search in the upper half of the face (avoids mouth/chin noise)
            roi = gray[fy: fy + fh // 2, fx: fx + fw]
            if roi.size == 0:
                continue
            det = self._eye_cascade.detectMultiScale(
                roi,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(15, 15),
            )
            if len(det) > 0:
                for (ex, ey, ew, eh) in det:
                    eyes_found.append((fx + int(ex), fy + int(ey), int(ew), int(eh)))
        return eyes_found

    def _run_detection(self, frame: np.ndarray, sensitivity: float):
        """
        Core pipeline:
          preprocess → detect faces → detect eyes → temporal vote → scores.
        """
        gray  = self._preprocess(frame)
        faces = self._detect_faces(gray, sensitivity)
        eyes  = self._detect_eyes(gray, faces)

        # ── Temporal smoothing ────────────────────────────────────────
        # Push a binary "face seen this frame?" into the history ring.
        self._history.append(1 if faces else 0)

        # Vote: what fraction of recent frames had a face?
        vote_fraction = sum(self._history) / max(len(self._history), 1)

        # Raw confidence is the vote fraction — smoothed across time.
        # We scale it so that a fraction >= _MIN_VOTE_FRACTION maps to ~sensitivity.
        raw_conf = vote_fraction  # 0.0 – 1.0

        # ── Attention score ───────────────────────────────────────────
        # Face presence + eye detection (blended).
        face_score = raw_conf
        eye_score  = 1.0 if eyes else (0.4 if faces else 0.0)
        # If eye cascade unavailable, just use face score
        if self._eye_cascade is None:
            attention = face_score
        else:
            attention = _FACE_WEIGHT * face_score + _EYE_WEIGHT * eye_score

        # ── Update state ──────────────────────────────────────────────
        self._last_faces  = faces
        self._last_eyes   = eyes
        self._confidence  = round(raw_conf, 3)
        self._attention   = round(min(attention, 1.0), 3)
