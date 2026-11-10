"""Face detection via OpenCV's built-in YuNet detector.

Replaces the MediaPipe "solutions" API (removed in MediaPipe 1.x). YuNet ships
as a 230 KB ONNX model (models/face_detection_yunet_2023mar.onnx, MIT licence)
and needs nothing beyond opencv-python.
"""
import os

MODEL_NAME = "face_detection_yunet_2023mar.onnx"


def model_path():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("SHORTS_FACE_MODEL", ""),
        os.path.join(here, "..", "models", MODEL_NAME),
        os.path.join(here, "models", MODEL_NAME),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(
        f"{MODEL_NAME} not found (looked in models/ next to scripts/). "
        "Re-run install.sh or set SHORTS_FACE_MODEL.")


class FaceDetector:
    """Detect faces in BGR frames. Results are normalised to 0..1."""

    def __init__(self, min_confidence=0.6, detect_width=960):
        import cv2

        if not hasattr(cv2, "FaceDetectorYN"):
            raise RuntimeError("This OpenCV build has no FaceDetectorYN (need opencv-python >= 4.8)")
        try:  # YuNet prints a harmless backend warning on OpenCV 5
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except AttributeError:
            pass
        self._cv2 = cv2
        self._detect_width = detect_width
        self._size = None
        self._detector = cv2.FaceDetectorYN.create(model_path(), "", (320, 320),
                                                   min_confidence, 0.3, 5000)

    def detect(self, frame):
        """Return [{cx, cy, w, h, score, mouth, nose}] sorted largest-first."""
        cv2 = self._cv2
        height, width = frame.shape[:2]
        if width > self._detect_width:
            scale = self._detect_width / width
            frame = cv2.resize(frame, (self._detect_width, max(2, int(height * scale))))
            height, width = frame.shape[:2]
        if self._size != (width, height):
            self._detector.setInputSize((width, height))
            self._size = (width, height)

        _, faces = self._detector.detect(frame)
        results = []
        if faces is not None:
            for face in faces:
                x, y, w, h = (float(v) for v in face[:4])
                if w <= 0 or h <= 0:
                    continue
                results.append({
                    "cx": min(1.0, max(0.0, (x + w / 2) / width)),
                    "cy": min(1.0, max(0.0, (y + h / 2) / height)),
                    "w": w / width,
                    "h": h / height,
                    "score": round(float(face[14]), 3),
                    # Landmarks (normalised): mouth centre + width, nose tip.
                    "mouth": ((float(face[10]) + float(face[12])) / 2 / width,
                              (float(face[11]) + float(face[13])) / 2 / height,
                              abs(float(face[12]) - float(face[10])) / width),
                    "nose": (float(face[8]) / width, float(face[9]) / height),
                })
        results.sort(key=lambda f: f["w"] * f["h"], reverse=True)
        return results
