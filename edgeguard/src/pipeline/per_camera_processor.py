from src.vision.person_detector import PersonDetector
from src.vision.person_tracker import PersonTracker
from src.vision.pose import PoseEstimator
from src.vision.item_detector import ItemDetector
from src.vision.item_tracker import ItemTracker


class PerCameraProcessor:
    def __init__(self, camera_cfg, models=None, thresholds=None) -> None:
        _ = (models, thresholds)
        self.camera_cfg = camera_cfg
        self.person_detector = PersonDetector()
        self.person_tracker = PersonTracker()
        self.pose = PoseEstimator()
        self.item_detector = ItemDetector(enabled=False)
        self.item_tracker = ItemTracker()

    def process_frame(self, frame_event) -> list[dict]:
        frame = frame_event.frame
        ts = frame_event.ts
        camera_id = frame_event.camera_id
        dets = self.person_detector.detect(frame)
        det_tuples = [(d.bbox[0], d.bbox[1], d.bbox[2], d.bbox[3], d.conf) for d in dets]

        # Keep this processor as a scaffold; existing production path remains in src/pipeline/runner.py.
        _ = (det_tuples, ts, camera_id)
        return []
