"""Face detection and recognition on ONNX Runtime alone.

Clipivo uses two models from InsightFace's buffalo_l pack: SCRFD (det_10g.onnx) finds faces
and their five landmarks, ArcFace (w600k_r50.onnx) turns an aligned face into an embedding.
This is the same arithmetic as the insightface package (MIT), kept to what Clipivo uses, so
builds do not carry scipy, scikit-image, scikit-learn, matplotlib and onnx for two models.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# Where ArcFace expects the eyes, nose and mouth corners in a 112x112 crop.
_ARCFACE_POINTS = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)


_GPU_LIBRARIES_LOADED = False


def execution_providers() -> list[str]:
    """The fastest ONNX Runtime providers this build and machine offer, CPU last.

    CUDA comes with onnxruntime-gpu (development, or the optional GPU download on Linux),
    DirectML with the Windows build, CoreML with the macOS build.
    """
    import onnxruntime

    global _GPU_LIBRARIES_LOADED
    if not _GPU_LIBRARIES_LOADED:
        # The optional Linux GPU download carries CUDA as NVIDIA's pip packages; load them once.
        from .downloads import preload_gpu_libraries

        preload_gpu_libraries()
        _GPU_LIBRARIES_LOADED = True
    available = set(onnxruntime.get_available_providers())
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider"]
    return [name for name in preferred if name in available] + ["CPUExecutionProvider"]


def open_session(path, providers: list[str]):
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(str(path), sess_options=options, providers=providers)


@dataclass
class Face:
    bbox: np.ndarray
    kps: np.ndarray | None
    det_score: float
    normed_embedding: np.ndarray | None = field(default=None, repr=False)


class Detector:
    """SCRFD at one square input size, as insightface prepares it for one det_size."""

    def __init__(self, session, size: int, threshold: float = 0.5, nms_threshold: float = 0.4):
        self.session = session
        self.size = int(size)
        self.threshold = threshold
        self.nms_threshold = nms_threshold
        self.input_name = session.get_inputs()[0].name
        self.output_names = [output.name for output in session.get_outputs()]
        count = len(self.output_names)
        self.levels = 3 if count in (6, 9) else 5
        self.strides = [8, 16, 32] if self.levels == 3 else [8, 16, 32, 64, 128]
        self.anchors = 2 if self.levels == 3 else 1
        self.use_kps = count in (9, 15)
        self.batched = len(session.get_outputs()[0].shape) == 3
        self._centres: dict[tuple[int, int, int], np.ndarray] = {}

    def detect(self, image, max_num: int = 0, metric: str = "default"):
        """(boxes with score as N x 5, landmarks as N x 5 x 2), largest-first like insightface."""
        height, width = image.shape[:2]
        if height / width > 1.0:
            new_height, new_width = self.size, int(self.size / (height / width))
        else:
            new_width, new_height = self.size, int(self.size * (height / width))
        scale = new_height / height
        canvas = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        canvas[:new_height, :new_width] = cv2.resize(image, (new_width, new_height))
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128.0, (self.size, self.size), (127.5, 127.5, 127.5), swapRB=True)
        outputs = self.session.run(self.output_names, {self.input_name: blob})

        scores_all, boxes_all, points_all = [], [], []
        for index, stride in enumerate(self.strides):
            pick = (lambda value: value[0]) if self.batched else (lambda value: value)
            scores = pick(outputs[index])
            box_deltas = pick(outputs[index + self.levels]) * stride
            rows, columns = self.size // stride, self.size // stride
            key = (rows, columns, stride)
            centres = self._centres.get(key)
            if centres is None:
                centres = np.stack(np.mgrid[:rows, :columns][::-1], axis=-1).astype(np.float32)
                centres = (centres * stride).reshape((-1, 2))
                if self.anchors > 1:
                    centres = np.stack([centres] * self.anchors, axis=1).reshape((-1, 2))
                self._centres[key] = centres
            keep = np.where(scores >= self.threshold)[0]
            boxes = np.stack([
                centres[:, 0] - box_deltas[:, 0], centres[:, 1] - box_deltas[:, 1],
                centres[:, 0] + box_deltas[:, 2], centres[:, 1] + box_deltas[:, 3],
            ], axis=-1)
            scores_all.append(scores[keep])
            boxes_all.append(boxes[keep])
            if self.use_kps:
                deltas = pick(outputs[index + self.levels * 2]) * stride
                points = np.stack(
                    [centres[:, i % 2] + deltas[:, i] for i in range(deltas.shape[1])], axis=-1
                ).reshape((-1, 5, 2))
                points_all.append(points[keep])

        if not scores_all or sum(item.size for item in scores_all) == 0:
            return np.empty((0, 5), dtype=np.float32), np.empty((0, 5, 2), dtype=np.float32) if self.use_kps else None
        scores = np.vstack(scores_all)
        order = scores.ravel().argsort()[::-1]
        detections = np.hstack((np.vstack(boxes_all) / scale, scores)).astype(np.float32, copy=False)[order]
        points = (np.vstack(points_all) / scale)[order] if self.use_kps else None
        # Same ordering step insightface takes after merging its (here single) sizes.
        resort = detections[:, 4].argsort()[::-1]
        detections = detections[resort]
        if points is not None:
            points = points[resort]
        keep = self._nms(detections)
        detections = detections[keep]
        if points is not None:
            points = points[keep]
        if max_num > 0 and detections.shape[0] > max_num:
            area = (detections[:, 2] - detections[:, 0]) * (detections[:, 3] - detections[:, 1])
            centre_y, centre_x = height // 2, width // 2
            offsets = np.vstack([
                (detections[:, 0] + detections[:, 2]) / 2 - centre_x,
                (detections[:, 1] + detections[:, 3]) / 2 - centre_y,
            ])
            values = area if metric == "max" else area - np.sum(offsets ** 2, 0) * 2.0
            chosen = np.argsort(values)[::-1][:max_num]
            detections = detections[chosen]
            if points is not None:
                points = points[chosen]
        return detections, points

    def _nms(self, detections) -> list[int]:
        x1, y1, x2, y2, scores = (detections[:, index] for index in range(5))
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            first = order[0]
            keep.append(first)
            width = np.maximum(0.0, np.minimum(x2[first], x2[order[1:]]) - np.maximum(x1[first], x1[order[1:]]) + 1)
            height = np.maximum(0.0, np.minimum(y2[first], y2[order[1:]]) - np.maximum(y1[first], y1[order[1:]]) + 1)
            overlap = width * height / (areas[first] + areas[order[1:]] - width * height)
            order = order[np.where(overlap <= self.nms_threshold)[0] + 1]
        return keep


def _similarity(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """2x3 rotation, uniform scale and shift that best maps source points onto target points
    (Umeyama's least squares, as scikit-image's SimilarityTransform estimates it)."""
    source = source.astype(np.float64)
    target = target.astype(np.float64)
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    source_centred, target_centred = source - source_mean, target - target_mean
    covariance = target_centred.T @ source_centred / len(source)
    signs = np.ones(2)
    if np.linalg.det(covariance) < 0:
        signs[-1] = -1
    u, singular, vt = np.linalg.svd(covariance)
    rank = np.linalg.matrix_rank(covariance)
    if rank == 0:
        return np.hstack([np.eye(2), np.zeros((2, 1))])
    if rank == 1:
        if np.linalg.det(u) * np.linalg.det(vt) > 0:
            rotation = u @ vt
        else:
            last = signs[-1]
            signs[-1] = -1
            rotation = u @ np.diag(signs) @ vt
            signs[-1] = last
    else:
        rotation = u @ np.diag(signs) @ vt
    scale = 1.0 / source_centred.var(axis=0).sum() * (singular @ signs)
    shift = target_mean - scale * (rotation @ source_mean)
    return np.hstack([scale * rotation, shift[:, None]])


def norm_crop(image, landmark, image_size: int = 112):
    """The face turned and scaled so its landmarks sit where ArcFace expects them."""
    ratio = image_size / 112.0 if image_size % 112 == 0 else image_size / 128.0
    target = _ARCFACE_POINTS * ratio
    if image_size % 112 != 0:
        target[:, 0] += 8.0 * ratio
    matrix = _similarity(np.asarray(landmark, dtype=np.float32), target)
    return cv2.warpAffine(image, matrix, (image_size, image_size), borderValue=0.0)


class Recognizer:
    """ArcFace: aligned 112x112 faces in, one 512-number embedding each out."""

    def __init__(self, session):
        self.session = session
        inputs = session.get_inputs()[0]
        self.input_name = inputs.name
        self.input_size = tuple(inputs.shape[2:4][::-1])
        self.output_names = [output.name for output in session.get_outputs()]

    def get_feat(self, images):
        if not isinstance(images, list):
            images = [images]
        blob = cv2.dnn.blobFromImages(images, 1.0 / 127.5, self.input_size, (127.5, 127.5, 127.5), swapRB=True)
        return self.session.run(self.output_names, {self.input_name: blob})[0]

    def embed(self, image, face: Face) -> None:
        crop = norm_crop(image, face.kps, image_size=self.input_size[0])
        embedding = self.get_feat(crop).flatten()
        face.normed_embedding = embedding / np.linalg.norm(embedding)


class FaceAnalysis:
    """Detector plus recogniser, answering like insightface's FaceAnalysis for what Clipivo calls."""

    def __init__(self, detector_path, recognizer_path, detector_size: int, providers: list[str]):
        self.det_model = Detector(open_session(detector_path, providers), detector_size)
        recognizer = Recognizer(open_session(recognizer_path, providers))
        self.models = {"detection": self.det_model, "recognition": recognizer}
        self.providers = list(self.det_model.session.get_providers())

    def get(self, image, max_num: int = 0, det_metric: str = "default") -> list[Face]:
        boxes, points = self.det_model.detect(image, max_num=max_num, metric=det_metric)
        faces = []
        for index in range(boxes.shape[0]):
            face = Face(bbox=boxes[index, :4], kps=None if points is None else points[index], det_score=float(boxes[index, 4]))
            self.models["recognition"].embed(image, face)
            faces.append(face)
        return faces
