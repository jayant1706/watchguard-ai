"""
WatchGuard AI — WatchDetector  (v4 — MediaPipe Tasks Edition)
=============================================================
Rewritten for mediapipe >= 0.10.30 which replaced mp.solutions
with the new Tasks API (mp.tasks.python.vision.FaceLandmarker).

Changes from v3:
  ✓ Uses mediapipe.tasks.python.vision.FaceLandmarker instead of
    the removed mp.solutions.face_mesh
  ✓ Requires a model file: face_landmarker.task
    Download once with:
        python download_model.py
    or manually from:
        https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
  ✓ Landmark access updated — new API returns NormalizedLandmark
    objects inside result.face_landmarks[face_idx][landmark_idx]
  ✓ Head pose now uses facial_transformation_matrixes when available
    (output_facial_transformation_matrixes=True) for better accuracy
  ✓ All public methods remain 100% backwards compatible

Graceful fallback: if mediapipe is not installed, or the model file
is missing, falls back to the v2 Haar-cascade implementation.

Public API (unchanged):
  is_watching(frame, sensitivity)  → bool
  annotate_frame(frame)            → annotated BGR frame
  get_confidence()                 → float  (0.0 – 1.0)
  get_attention_score()            → float  (0.0 – 1.0)
  get_gaze_direction()             → str    ("centre"|"left"|"right"|"up"|"down"|"away")
  get_head_angles()                → (pitch, yaw, roll) in degrees, or (0,0,0)
"""

import collections
import math
import os
import cv2
import numpy as np

# ── Model file location ───────────────────────────────────────────────────────
# Place face_landmarker.task next to detector.py, or set this env var.
_DEFAULT_MODEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "face_landmarker.task"
)
MODEL_PATH = os.environ.get("WATCHGUARD_MP_MODEL", _DEFAULT_MODEL)

# ── Try importing MediaPipe Tasks ─────────────────────────────────────────────
_MP_AVAILABLE = False
try:
    import mediapipe as mp
    from mediapipe.tasks.python import vision as _mp_vision
    from mediapipe.tasks.python.core import base_options as _mp_base
    _MP_AVAILABLE = True
except ImportError:
    print(
        "[Detector] MediaPipe not found — falling back to Haar cascades.\n"
        "           For best accuracy: pip install mediapipe"
    )

# ── Tunables ──────────────────────────────────────────────────────────────────
_TEMPORAL_WINDOW   = 8      # frames to vote over
_MIN_VOTE_FRACTION = 0.35   # fraction of window that must be "face seen"
_BLINK_EAR_THRESH  = 0.20   # EAR below this = blink / eyes closed
_BLINK_CONSEC      = 2      # consecutive frames below thresh before counting as blink
_GAZE_YAW_THRESH   = 25.0   # degrees — head yaw beyond this = looking away laterally
_GAZE_PITCH_THRESH = 20.0   # degrees — pitch beyond this = looking up/down away
_HEAD_AWAY_THRESH  = 40.0   # degrees — extreme rotation = definitely away

# MediaPipe landmark indices (same as v3 — 478-landmark FaceMesh with irises)
_LEFT_EYE  = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE = [33,  160, 158, 133, 153, 144]
_LEFT_IRIS_CENTER  = 473
_RIGHT_IRIS_CENTER = 468
_NOSE_TIP      = 1
_CHIN          = 152
_LEFT_EYE_L    = 263
_RIGHT_EYE_R   = 33
_LEFT_MOUTH    = 287
_RIGHT_MOUTH   = 57


def _ear(landmarks, eye_indices, w, h):
    """Eye Aspect Ratio for a given eye (6 landmark indices)."""
    pts = [(int(landmarks[i].x * w), int(landmarks[i].y * h))
           for i in eye_indices]
    A = math.dist(pts[1], pts[5])
    B = math.dist(pts[2], pts[4])
    C = math.dist(pts[0], pts[3])
    return (A + B) / (2.0 * C + 1e-6)


class WatchDetector:
    """
    Robust face-presence + gaze + blink + head-pose detector.
    Uses MediaPipe FaceLandmarker (Tasks API) when available;
    falls back to Haar cascades if mediapipe or the model file is missing.
    """

    def __init__(
        self,
        model_path: str   = MODEL_PATH,
        temporal_window: int  = _TEMPORAL_WINDOW,
        # Haar fallback (ignored when MP available)
        scale_factor: float   = 1.15,
        min_neighbors: int    = 4,
        min_face_px: int      = 55,
        use_profile: bool     = True,
        use_eye_check: bool   = True,
    ):
        self._temporal_window = temporal_window
        self._history         = collections.deque(maxlen=temporal_window)
        self._confidence      = 0.0
        self._attention       = 0.0
        self._gaze            = "unknown"
        self._head_angles     = (0.0, 0.0, 0.0)  # pitch, yaw, roll

        # Blink tracking
        self._blink_counter   = 0
        self._blink_total     = 0
        self._eyes_closed     = False

        # Annotation cache
        self._last_landmarks  = None
        self._last_faces_haar = []
        self._last_eyes_haar  = []
        self._frame_shape     = (480, 640)

        mp_ok = _MP_AVAILABLE and os.path.isfile(model_path)
        if _MP_AVAILABLE and not os.path.isfile(model_path):
            print(
                f"[Detector] Model file not found: {model_path}\n"
                "           Falling back to Haar cascades.\n"
                "           Download the model with:  python download_model.py"
            )

        if mp_ok:
            self._init_mediapipe(model_path)
        else:
            self._init_haar(scale_factor, min_neighbors, min_face_px,
                            use_profile, use_eye_check)

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_mediapipe(self, model_path: str):
        """Initialise using the new mediapipe.tasks.python.vision API."""
        self._use_mp = True

        base_opts = _mp_base.BaseOptions(model_asset_path=model_path)
        options   = _mp_vision.FaceLandmarkerOptions(
            base_options=base_opts,
            running_mode=_mp_vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_facial_transformation_matrixes=True,
        )
        self._face_landmarker = _mp_vision.FaceLandmarker.create_from_options(options)
        print("[Detector] MediaPipe FaceLandmarker (Tasks API) loaded ✓")

    def _init_haar(self, scale_factor, min_neighbors, min_face_px,
                   use_profile, use_eye_check):
        self._use_mp      = False
        self._scale_factor   = scale_factor
        self._min_neighbors  = min_neighbors
        self._min_face_px    = min_face_px
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        base = cv2.data.haarcascades
        self._frontal = cv2.CascadeClassifier(
            base + "haarcascade_frontalface_default.xml")
        self._profile = None
        if use_profile:
            p = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
            if not p.empty():
                self._profile = p
        self._eye_cascade = None
        if use_eye_check:
            e = cv2.CascadeClassifier(base + "haarcascade_eye.xml")
            if not e.empty():
                self._eye_cascade = e

    # ── Public API ────────────────────────────────────────────────────────────

    def is_watching(self, frame: np.ndarray, sensitivity: float = 0.6) -> bool:
        if self._use_mp:
            self._run_mp(frame, sensitivity)
        else:
            self._run_haar(frame, sensitivity)
        return self._confidence >= sensitivity

    def annotate_frame(self, frame: np.ndarray) -> np.ndarray:
        if self._use_mp:
            return self._annotate_mp(frame)
        return self._annotate_haar(frame)

    def get_confidence(self) -> float:
        return self._confidence

    def get_attention_score(self) -> float:
        return self._attention

    def get_gaze_direction(self) -> str:
        """Returns: 'centre', 'left', 'right', 'up', 'down', 'away', 'unknown'"""
        return self._gaze

    def get_head_angles(self):
        """(pitch_deg, yaw_deg, roll_deg)  — positive yaw = turned right."""
        return self._head_angles

    # ── MediaPipe pipeline ────────────────────────────────────────────────────

    def _run_mp(self, frame: np.ndarray, sensitivity: float):
        h, w = frame.shape[:2]
        self._frame_shape = (h, w)

        # New API: wrap numpy array in mp.Image (SRGB)
        rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._face_landmarker.detect(mp_img)

        # result.face_landmarks is a list-of-lists:
        #   result.face_landmarks[face_idx][landmark_idx]  → NormalizedLandmark
        if not result.face_landmarks:
            self._history.append(0)
            self._last_landmarks = None
            self._update_scores_mp(False, None, None)
            return

        # Flat list of landmarks for face 0 — same shape as v3's lm
        lm = result.face_landmarks[0]
        self._last_landmarks = lm

        # ── Head pose ─────────────────────────────────────────────────
        # Prefer transformation matrix when available (more accurate)
        if (result.facial_transformation_matrixes and
                len(result.facial_transformation_matrixes) > 0):
            pitch, yaw, roll = self._pose_from_matrix(
                result.facial_transformation_matrixes[0])
        else:
            pitch, yaw, roll = self._head_pose(lm, w, h)
        self._head_angles = (pitch, yaw, roll)

        head_away = (abs(yaw) > _HEAD_AWAY_THRESH or
                     abs(pitch) > _HEAD_AWAY_THRESH)

        # ── Blink / eye closure ───────────────────────────────────────
        left_ear  = _ear(lm, _LEFT_EYE,  w, h)
        right_ear = _ear(lm, _RIGHT_EYE, w, h)
        avg_ear   = (left_ear + right_ear) / 2.0

        if avg_ear < _BLINK_EAR_THRESH:
            self._blink_counter += 1
        else:
            if self._blink_counter >= _BLINK_CONSEC:
                self._blink_total += 1
            self._blink_counter = 0

        eyes_closed = self._blink_counter > _BLINK_CONSEC * 3
        self._eyes_closed = eyes_closed

        # ── Gaze direction ────────────────────────────────────────────
        gaze = self._estimate_gaze(lm, w, h, pitch, yaw)
        self._gaze = gaze
        gaze_away = gaze in ("left", "right", "up", "down", "away")

        face_seen = not head_away
        self._history.append(1 if face_seen else 0)
        self._update_scores_mp(face_seen, gaze_away, eyes_closed)

    def _update_scores_mp(self, face_seen, gaze_away, eyes_closed):
        vote_fraction    = sum(self._history) / max(len(self._history), 1)
        self._confidence = round(vote_fraction, 3)

        if not face_seen:
            self._attention = 0.0
            return

        base        = vote_fraction
        gaze_factor = 0.0 if (gaze_away is True) else (
            0.5 if gaze_away is None else 1.0)
        eye_factor  = 0.0 if eyes_closed else 1.0

        self._attention = round(
            min(1.0, base * (0.6 + 0.25 * gaze_factor + 0.15 * eye_factor)), 3)

    # ── Head pose helpers ─────────────────────────────────────────────────────

    def _pose_from_matrix(self, mat):
        """
        Extract Euler angles (degrees) from a 4×4 facial transformation matrix
        returned by FaceLandmarker when output_facial_transformation_matrixes=True.
        mat is a mediapipe MatrixData — convert to numpy first.
        """
        try:
            # mat.data is a flat list; mat.rows / mat.cols give dimensions
            m = np.array(mat.data, dtype=np.float64).reshape(mat.rows, mat.cols)
            r = m[:3, :3]
            sy = math.sqrt(r[0, 0] ** 2 + r[1, 0] ** 2)
            if sy > 1e-6:
                pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
                yaw   = math.degrees(math.atan2(-r[2, 0], sy))
                roll  = math.degrees(math.atan2(r[1, 0], r[0, 0]))
            else:
                pitch = math.degrees(math.atan2(-r[1, 2], r[1, 1]))
                yaw   = math.degrees(math.atan2(-r[2, 0], sy))
                roll  = 0.0
            return pitch, yaw, roll
        except Exception:
            return 0.0, 0.0, 0.0

    def _head_pose(self, lm, w, h):
        """
        Fallback PnP-based head pose when transformation matrix is unavailable.
        Identical to v3.
        """
        model_pts = np.array([
            [0.0,    0.0,    0.0],
            [0.0,   -63.6,  -12.5],
            [-43.3,  32.7,  -26.0],
            [43.3,   32.7,  -26.0],
            [-28.9, -28.9,  -24.1],
            [28.9,  -28.9,  -24.1],
        ], dtype=np.float64)

        idx     = [_NOSE_TIP, _CHIN, _LEFT_EYE_L, _RIGHT_EYE_R,
                   _LEFT_MOUTH, _RIGHT_MOUTH]
        img_pts = np.array(
            [(lm[i].x * w, lm[i].y * h) for i in idx], dtype=np.float64)

        focal   = w
        cam_mat = np.array([[focal, 0, w / 2],
                            [0, focal, h / 2],
                            [0, 0, 1]], dtype=np.float64)
        dist    = np.zeros((4, 1))

        ok, rvec, _ = cv2.solvePnP(model_pts, img_pts, cam_mat, dist,
                                    flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return 0.0, 0.0, 0.0

        rot_mat, _ = cv2.Rodrigues(rvec)
        sy = math.sqrt(rot_mat[0, 0] ** 2 + rot_mat[1, 0] ** 2)
        if sy > 1e-6:
            pitch = math.degrees(math.atan2(rot_mat[2, 1], rot_mat[2, 2]))
            yaw   = math.degrees(math.atan2(-rot_mat[2, 0], sy))
            roll  = math.degrees(math.atan2(rot_mat[1, 0], rot_mat[0, 0]))
        else:
            pitch = math.degrees(math.atan2(-rot_mat[1, 2], rot_mat[1, 1]))
            yaw   = math.degrees(math.atan2(-rot_mat[2, 0], sy))
            roll  = 0.0
        return pitch, yaw, roll

    def _estimate_gaze(self, lm, w, h, pitch, yaw):
        """
        Estimate where the user is looking (unchanged from v3).
        lm is a flat list of NormalizedLandmark — same .x/.y/.z interface.
        """
        if abs(yaw) > _GAZE_YAW_THRESH:
            return "right" if yaw > 0 else "left"
        if pitch > _GAZE_PITCH_THRESH:
            return "down"
        if pitch < -_GAZE_PITCH_THRESH:
            return "up"

        try:
            l_iris = lm[_LEFT_IRIS_CENTER]
            r_iris = lm[_RIGHT_IRIS_CENTER]
            l_left  = lm[362]; l_right = lm[263]
            r_left  = lm[33];  r_right = lm[133]

            def iris_ratio(iris, corner_l, corner_r):
                total = corner_r.x - corner_l.x
                if abs(total) < 1e-4:
                    return 0.5
                return (iris.x - corner_l.x) / total

            l_ratio = iris_ratio(l_iris, l_left, l_right)
            r_ratio = iris_ratio(r_iris, r_left, r_right)
            avg = (l_ratio + r_ratio) / 2.0

            if avg < 0.35:
                return "left"
            elif avg > 0.65:
                return "right"
            return "centre"
        except Exception:
            return "centre"

    # ── Annotation ────────────────────────────────────────────────────────────

    def _annotate_mp(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]

        if self._last_landmarks is None:
            cx, cy = w // 2, h // 2
            cv2.circle(out, (cx, cy), 30, (80, 80, 200), 2)
            cv2.putText(out, "no face", (cx - 28, cy + 48),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (100, 100, 200), 1)
            self._draw_hud(out, w, h)
            return out

        lm = self._last_landmarks

        for eye_idx in (_LEFT_EYE, _RIGHT_EYE):
            pts = np.array(
                [(int(lm[i].x * w), int(lm[i].y * h)) for i in eye_idx],
                dtype=np.int32)
            col = (99, 230, 102) if not self._eyes_closed else (100, 100, 255)
            cv2.polylines(out, [pts], True, col, 1, cv2.LINE_AA)

        for iris_idx in (_LEFT_IRIS_CENTER, _RIGHT_IRIS_CENTER):
            ix = int(lm[iris_idx].x * w)
            iy = int(lm[iris_idx].y * h)
            cv2.circle(out, (ix, iy), 3, (79, 217, 255), -1)

        nx = int(lm[_NOSE_TIP].x * w)
        ny = int(lm[_NOSE_TIP].y * h)
        cv2.circle(out, (nx, ny), 2, (255, 200, 80), -1)

        self._draw_hud(out, w, h)
        return out

    def _draw_hud(self, out, w, h):
        pitch, yaw, roll = self._head_angles

        bar_w   = int(self._attention * 80)
        bar_col = (99, 230, 102) if self._attention > 0.6 else \
                  (255, 217, 122) if self._attention > 0.3 else (255, 92, 122)
        cv2.rectangle(out, (6, 6), (86, 14), (20, 28, 40), -1)
        if bar_w > 0:
            cv2.rectangle(out, (6, 6), (6 + bar_w, 14), bar_col, -1)
        cv2.putText(out, f"attn {int(self._attention*100)}%", (6, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, bar_col, 1, cv2.LINE_AA)

        gaze_col = (99, 230, 102) if self._gaze == "centre" else (255, 92, 122)
        cv2.putText(out, f"gaze:{self._gaze}", (6, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, gaze_col, 1, cv2.LINE_AA)
        cv2.putText(out, f"Y{yaw:+.0f} P{pitch:+.0f}", (6, 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, (130, 160, 220), 1, cv2.LINE_AA)
        cv2.putText(out, f"blinks:{self._blink_total}", (6, 54),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, (180, 150, 80), 1, cv2.LINE_AA)

    # ── Haar fallback ─────────────────────────────────────────────────────────

    def _run_haar(self, frame: np.ndarray, sensitivity: float):
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray  = self._clahe.apply(gray)
        faces = self._detect_faces_haar(gray, sensitivity)
        eyes  = self._detect_eyes_haar(gray, faces)

        self._last_faces_haar = faces
        self._last_eyes_haar  = eyes
        self._history.append(1 if faces else 0)

        vote_fraction    = sum(self._history) / max(len(self._history), 1)
        self._confidence = round(vote_fraction, 3)
        face_score       = vote_fraction
        eye_score        = 1.0 if eyes else (0.4 if faces else 0.0)
        if self._eye_cascade is None:
            self._attention = round(face_score, 3)
        else:
            self._attention = round(
                min(1.0, 0.65 * face_score + 0.35 * eye_score), 3)
        self._gaze = "unknown"

    def _detect_faces_haar(self, gray, sensitivity):
        min_n   = max(1, int(self._min_neighbors * sensitivity / 0.6))
        faces   = []
        frontal = self._frontal.detectMultiScale(
            gray, scaleFactor=self._scale_factor,
            minNeighbors=min_n, minSize=(self._min_face_px, self._min_face_px))
        if len(frontal):
            for (x, y, w, h) in frontal:
                faces.append((int(x), int(y), int(w), int(h), "face"))
        if self._profile is not None:
            for flip in (False, True):
                g   = cv2.flip(gray, 1) if flip else gray
                det = self._profile.detectMultiScale(
                    g, scaleFactor=self._scale_factor,
                    minNeighbors=max(1, min_n - 1),
                    minSize=(self._min_face_px, self._min_face_px))
                if len(det):
                    fw = gray.shape[1]
                    for (x, y, w, h) in det:
                        faces.append((
                            int(fw - x - w) if flip else int(x),
                            int(y), int(w), int(h), "profile"))
        return self._dedup_haar(faces)

    def _dedup_haar(self, faces, iou=0.3):
        if len(faces) <= 1:
            return faces
        kept = []
        for cand in faces:
            cx, cy, cw, ch, _ = cand
            overlap = False
            for kx, ky, kw, kh, _ in kept:
                ix    = max(0, min(cx + cw, kx + kw) - max(cx, kx))
                iy    = max(0, min(cy + ch, ky + kh) - max(cy, ky))
                inter = ix * iy
                union = cw * ch + kw * kh - inter
                if union > 0 and inter / union > iou:
                    overlap = True; break
            if not overlap:
                kept.append(cand)
        return kept

    def _detect_eyes_haar(self, gray, faces):
        if self._eye_cascade is None or not faces:
            return []
        eyes = []
        for (fx, fy, fw, fh, _) in faces:
            roi = gray[fy: fy + fh // 2, fx: fx + fw]
            if roi.size == 0:
                continue
            det = self._eye_cascade.detectMultiScale(
                roi, scaleFactor=1.1, minNeighbors=3, minSize=(15, 15))
            if len(det):
                for (ex, ey, ew, eh) in det:
                    eyes.append((fx + int(ex), fy + int(ey), int(ew), int(eh)))
        return eyes

    def _annotate_haar(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        for (x, y, w, h, label) in self._last_faces_haar:
            cv2.rectangle(out, (x, y), (x + w, y + h), (99, 230, 102), 2)
            badge = f"{label}  {int(self._confidence * 100)}%"
            cv2.putText(out, badge, (x, max(y - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (99, 230, 102), 1, cv2.LINE_AA)
        for (ex, ey, ew, eh) in self._last_eyes_haar:
            cv2.circle(out, (ex + ew // 2, ey + eh // 2), 4, (79, 217, 255), -1)
        bar_w   = int(self._attention * 80)
        bar_col = (99, 230, 102) if self._attention > 0.6 else \
                  (255, 217, 122) if self._attention > 0.3 else (255, 92, 122)
        cv2.rectangle(out, (6, 6), (86, 14), (30, 40, 50), -1)
        if bar_w > 0:
            cv2.rectangle(out, (6, 6), (6 + bar_w, 14), bar_col, -1)
        cv2.putText(out, "attn", (6, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, (100, 120, 160), 1, cv2.LINE_AA)
        return out
