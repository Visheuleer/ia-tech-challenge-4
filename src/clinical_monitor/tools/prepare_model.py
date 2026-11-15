import shutil
from pathlib import Path

from clinical_monitor.core.config import Settings


def main() -> None:
    from ultralytics import YOLO

    destination = Path(Settings().pose_model_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        print(f"Model available: {destination}")
        return
    model = YOLO("yolov8n-pose.pt")
    shutil.move(str(model.ckpt_path), str(destination))
    print(f"Model ready: {destination}")


if __name__ == "__main__":
    main()
