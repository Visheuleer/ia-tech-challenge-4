import math
from pathlib import Path

import cv2
import numpy as np

from clinical_monitor.application.errors import InvalidAnalysis


def joint_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    left, right = a - b, c - b
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    if denominator < 1e-6:
        raise ValueError("Degenerate keypoints")
    return float(np.degrees(np.arccos(np.clip(np.dot(left, right) / denominator, -1, 1))))


def pose_features(points: np.ndarray, min_confidence: float = 0.5) -> dict | None:
    required = [5, 6, 11, 12, 13, 14, 15, 16]
    if (
        points.shape != (17, 3)
        or not np.isfinite(points).all()
        or any(points[index, 2] < min_confidence for index in required)
    ):
        return None
    shoulder = (points[5, :2] + points[6, :2]) / 2
    hip = (points[11, :2] + points[12, :2]) / 2
    length = float(np.linalg.norm(shoulder - hip))
    if length < 1:
        return None
    tilt = math.degrees(math.atan2(abs(shoulder[0] - hip[0]), abs(shoulder[1] - hip[1])))
    try:
        left = joint_angle(points[11, :2], points[13, :2], points[15, :2])
        right = joint_angle(points[12, :2], points[14, :2], points[16, :2])
    except ValueError:
        return None
    return {
        "trunk_tilt_degrees": tilt,
        "knee_asymmetry_degrees": abs(left - right),
        "left_knee_degrees": left,
        "right_knee_degrees": right,
        "center": hip.tolist(),
        "scale": length,
        "keypoint_confidence": float(np.min(points[required, 2])),
    }


def single_complete_pose(poses) -> dict | None:
    candidates = [pose_features(points) for points in poses]
    complete = [candidate for candidate in candidates if candidate is not None]
    return complete[0] if len(complete) == 1 else None


def analyze_video(path: Path, settings, output_dir: Path, model=None) -> dict:
    if model is None:
        from ultralytics import YOLO

        model_path = Path(settings.pose_model_path)
        if not model_path.is_file():
            raise InvalidAnalysis(
                "Pose model missing; run python -m clinical_monitor.tools.prepare_model"
            )
        model = YOLO(str(model_path))
    capture = cv2.VideoCapture(str(path))
    fps, count = capture.get(cv2.CAP_PROP_FPS), capture.get(cv2.CAP_PROP_FRAME_COUNT)
    if not capture.isOpened() or fps <= 0 or count <= 0:
        capture.release()
        raise InvalidAnalysis("Invalid or unreadable video")
    duration = count / fps
    if duration > settings.max_media_seconds:
        capture.release()
        raise InvalidAnalysis("Video exceeds the configured duration limit")
    stride = max(1, round(fps / settings.video_sample_fps))
    frames = []
    previous = None
    last_time = None
    still_since = None
    try:
        for index in range(0, int(count), stride):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            timestamp = index / fps
            prediction = model.predict(frame, verbose=False, device="cpu", imgsz=640)[0]
            features = None
            if prediction.keypoints is not None:
                features = single_complete_pose(
                    [points.cpu().numpy() for points in prediction.keypoints.data]
                )
            if features is None:
                previous, last_time, still_since = None, None, None
                frames.append({"seconds": timestamp, "quality": "unusable"})
                continue
            center = np.array(features["center"])
            speed = None
            if previous is not None and last_time is not None:
                speed = float(
                    np.linalg.norm(center - previous) / features["scale"] / (timestamp - last_time)
                )
                if speed <= settings.video_stillness_speed:
                    still_since = last_time if still_since is None else still_since
                else:
                    still_since = None
            previous, last_time = center, timestamp
            deviation = (
                features["trunk_tilt_degrees"] > settings.video_tilt_degrees
                or features["knee_asymmetry_degrees"] > settings.video_asymmetry_degrees
            )
            still = (
                still_since is not None
                and timestamp - still_since >= settings.video_stillness_seconds
            )
            frames.append(
                {
                    "seconds": timestamp,
                    "quality": "valid",
                    **features,
                    "posture_deviation": deviation,
                    "prolonged_stillness": still,
                    "normalized_speed": speed,
                }
            )
            if deviation and not (output_dir / "video-evidence.jpg").exists():
                cv2.imwrite(str(output_dir / "video-evidence.jpg"), prediction.plot())
    finally:
        capture.release()
    valid = [frame for frame in frames if frame["quality"] == "valid"]
    return {
        "pose_pipeline_version": "2.0.0",
        "duration_seconds": duration,
        "frames": frames,
        "valid_fraction": len(valid) / max(len(frames), 1),
        "model": Path(settings.pose_model_path).name,
        "quality_status": "unusable"
        if not valid
        else "degraded"
        if len(valid) / len(frames) < 0.8
        else "valid",
    }
