from dataclasses import dataclass

import numpy as np

try:
    import torch
    from ultralytics import YOLO
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    YOLO = None

# COCO classes that commonly appear in retail environments.
# One YOLO call returns all; we split persons vs items for free.
RETAIL_COCO_CLASSES: dict[int, str] = {
    24: "backpack",
    25: "umbrella",
    26: "handbag",
    28: "suitcase",
    39: "bottle",
    40: "wine_glass",
    41: "cup",
    42: "fork",
    43: "knife",
    44: "spoon",
    45: "bowl",
    46: "banana",
    47: "apple",
    48: "sandwich",
    49: "orange",
    50: "broccoli",
    51: "carrot",
    63: "laptop",
    64: "mouse",
    65: "remote",
    66: "keyboard",
    67: "cell_phone",
    73: "book",
    76: "scissors",
    77: "teddy_bear",
    84: "book",
}


@dataclass
class Detection:
    box: tuple[float, float, float, float]
    cls: str
    conf: float
    track_id: int | None = None


class YOLODetector:
    def __init__(self, model_name: str = "yolo26n.pt", conf_thres: float = 0.35) -> None:
        if YOLO is None:
            raise RuntimeError("ultralytics is not available. Install dependencies first.")
        if torch is not None:
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        else:
            device = "cpu"
        self.model = YOLO(model_name)
        self.model.to(device)
        self._device = device
        self.conf_thres = conf_thres

    def detect_all(
        self, frame: np.ndarray
    ) -> tuple[list[Detection], list[Detection]]:
        """Single inference call → (person_detections, item_detections).

        Splitting class filtering here means the rest of the pipeline pays
        zero extra inference cost for item detection.
        """
        result = self.model.predict(frame, conf=self.conf_thres, verbose=False)[0]
        persons: list[Detection] = []
        items: list[Detection] = []

        for box in result.boxes:
            cls_id = int(box.cls.item())
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf.item())
            if cls_id == 0:
                persons.append(Detection(box=(x1, y1, x2, y2), cls="person", conf=conf))
            elif cls_id in RETAIL_COCO_CLASSES:
                items.append(
                    Detection(
                        box=(x1, y1, x2, y2),
                        cls=RETAIL_COCO_CLASSES[cls_id],
                        conf=conf,
                    )
                )
        return persons, items

    def detect_persons(self, frame: np.ndarray) -> list[Detection]:
        persons, _ = self.detect_all(frame)
        return persons

    def detect_items(self, frame: np.ndarray) -> list[Detection]:
        _, items = self.detect_all(frame)
        return items

    def track_persons(self, frame: np.ndarray) -> list[Detection]:
        result = self.model.track(
            frame,
            conf=self.conf_thres,
            classes=[0],
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )[0]

        detections: list[Detection] = []
        ids = result.boxes.id
        id_values = ids.int().tolist() if ids is not None else []

        for idx, box in enumerate(result.boxes):
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf.item())
            track_id = int(id_values[idx]) if idx < len(id_values) else None
            detections.append(Detection(box=(x1, y1, x2, y2), cls="person", conf=conf, track_id=track_id))
        return detections
