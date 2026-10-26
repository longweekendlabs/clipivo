from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Protocol
import warnings


@dataclass(frozen=True)
class EngineStatus:
    available: bool
    device: str
    detail: str


@dataclass
class ReferenceAssessment:
    name: str
    valid: bool
    reason: str = ""
    warning: str = ""
    embedding: object | None = field(default=None, repr=False)


@dataclass(frozen=True)
class MatchAssessment:
    faces_detected: int
    score: float
    matched: bool
    # Best-matching face as (x1, y1, x2, y2) in frame pixels, when known.
    box: tuple[float, float, float, float] | None = None
    # Every detected face as (x1, y1, x2, y2), so framing can keep other people out.
    faces: tuple = ()


class FaceEngine(Protocol):
    def status(self) -> EngineStatus: ...
    def assess_references(
        self,
        named_bytes: list[tuple[str, bytes]],
        cancelled: Callable[[], bool] | None = None,
    ) -> list[ReferenceAssessment]: ...
    def reference_matrix(self, references: list[tuple[str, bytes]]): ...
    def match_image(self, image_bytes: bytes, references: list[tuple[str, bytes]], threshold: float) -> MatchAssessment: ...
    def match_frame(self, image, reference_matrix, threshold: float) -> MatchAssessment: ...
    def match_frame_many(self, image, targets: dict[str, tuple[object, float]]) -> dict[str, MatchAssessment]: ...


class InsightFaceEngine:
    """Replaceable InsightFace adapter, loaded only when recognition is requested."""

    def __init__(self, model_root: Path, *, use_gpu: bool = True, detector_size: int = 640):
        self.model_root = Path(model_root)
        self.use_gpu = use_gpu
        self.detector_size = detector_size
        self._application = None
        self._used_gpu = False

    def status(self) -> EngineStatus:
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
        except ImportError:
            return EngineStatus(False, "Unavailable", "Install InsightFace and ONNX Runtime to validate references.")
        gpu = "CUDAExecutionProvider" in providers and self._nvidia_device_available()
        if self._application is not None:
            device = "GPU" if self._used_gpu else "CPU"
            return EngineStatus(True, device, f"{device} inference active")
        device = "GPU" if self.use_gpu and gpu else "CPU"
        detail = "CUDA available" if gpu else "CPU fallback; no usable CUDA device found"
        models = self._model_files()
        if not all(path.is_file() for path in models):
            return EngineStatus(False, device, "Clipivo's buffalo_l detection and recognition model files are missing.")
        try:
            import insightface  # noqa: F401
        except ImportError:
            return EngineStatus(False, device, "The InsightFace package is not installed.")
        return EngineStatus(True, device, detail)

    def search_engine(self) -> "InsightFaceEngine":
        """Create a reusable fast detector for interactive first-match searches."""
        return InsightFaceEngine(
            self.model_root,
            use_gpu=self.use_gpu,
            detector_size=min(320, self.detector_size),
        )

    def standard_engine(self) -> "InsightFaceEngine":
        """The 640 px detector, shared: enough for faces that are not tiny, about 3x faster
        than the wide-shot detector."""
        if self.detector_size == 640:
            return self
        if getattr(self, "_standard", None) is None:
            self._standard = InsightFaceEngine(self.model_root, use_gpu=self.use_gpu, detector_size=640)
        return self._standard

    def wide_shot_engine(self) -> "InsightFaceEngine":
        """Create a high-resolution detector that still sees small faces in wide studio shots."""
        return InsightFaceEngine(
            self.model_root,
            use_gpu=self.use_gpu,
            detector_size=max(1280, self.detector_size),
        )

    def assess_references(
        self,
        named_bytes: list[tuple[str, bytes]],
        cancelled: Callable[[], bool] | None = None,
    ) -> list[ReferenceAssessment]:
        import numpy as np

        application = self._ensure_application()
        results: list[ReferenceAssessment] = []
        for name, data in named_bytes:
            if cancelled and cancelled():
                break
            image = self._decode(data)
            if image is None:
                results.append(ReferenceAssessment(name, False, "Unreadable image"))
                continue
            faces = self._reference_faces(application, image)
            if not faces:
                results.append(ReferenceAssessment(name, False, "No usable face found"))
                continue
            face = max(faces, key=self._face_area)
            warning = "Multiple faces found; largest face used" if len(faces) > 1 else ""
            results.append(ReferenceAssessment(name, True, warning=warning, embedding=face.normed_embedding))

        valid = [item for item in results if item.valid]
        if len(valid) > 1:
            matrix = np.vstack([item.embedding for item in valid]).astype(np.float32)
            similarities = matrix @ matrix.T
            for index, item in enumerate(valid):
                mean = float(np.delete(similarities[index], index).mean())
                if mean < 0.30:
                    item.warning = f"Possible outlier reference (mean similarity {mean:.2f})"
        return results

    def match_image(
        self,
        image_bytes: bytes,
        references: list[tuple[str, bytes]],
        threshold: float = 0.40,
    ) -> MatchAssessment:
        matrix = self.reference_matrix(references)
        image = self._decode(image_bytes)
        if image is None:
            raise RuntimeError("The test image could not be decoded.")
        return self.match_frame(image, matrix, threshold)

    def match_frame(self, image, reference_matrix, threshold: float = 0.40) -> MatchAssessment:
        """Match an already-decoded BGR frame against a prepared matrix."""
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"insightface\..*")
            faces = self._ensure_application().get(image)
        if not faces:
            return MatchAssessment(0, 0.0, False)
        score = max(float((reference_matrix @ face.normed_embedding).max()) for face in faces)
        return MatchAssessment(len(faces), score, score >= threshold)

    def match_frame_many(
        self,
        image,
        targets: dict[str, tuple[object, float]],
    ) -> dict[str, MatchAssessment]:
        """Detect faces once and compare them with every selected character."""
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"insightface\..*")
            faces = self._ensure_application().get(image)
        if not faces:
            return {target_id: MatchAssessment(0, 0.0, False) for target_id in targets}
        results: dict[str, MatchAssessment] = {}
        for target_id, (reference_matrix, threshold) in targets.items():
            scores = [float((reference_matrix @ face.normed_embedding).max()) for face in faces]
            best = max(range(len(faces)), key=scores.__getitem__)
            box = tuple(float(value) for value in faces[best].bbox[:4])
            boxes = tuple(tuple(float(value) for value in face.bbox[:4]) for face in faces)
            results[target_id] = MatchAssessment(len(faces), scores[best], scores[best] >= threshold, box, boxes)
        return results

    def detect_faces(self, image) -> list[tuple[tuple[float, float, float, float], object]]:
        """Face boxes (x1, y1, x2, y2) with their landmarks, without recognising anyone."""
        application = self._ensure_application()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"insightface\..*")
            boxes, landmarks = application.det_model.detect(image, max_num=0, metric="default")
        return [
            (tuple(float(value) for value in boxes[index, :4]), landmarks[index] if landmarks is not None else None)
            for index in range(boxes.shape[0])
        ]

    def face_scores(self, image, faces: list, reference_matrix) -> list[float]:
        """Similarity of each detected face to the references, recognising only these faces."""
        import numpy as np
        from insightface.utils import face_align

        if not faces:
            return []
        recognition = self._ensure_application().models["recognition"]
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            crops = [face_align.norm_crop(image, landmark=landmarks, image_size=recognition.input_size[0]) for _box, landmarks in faces]
            features = np.asarray(recognition.get_feat(crops), dtype=np.float32).reshape(len(crops), -1)
        features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-6)
        return [float(value) for value in (features @ reference_matrix.T).max(axis=1)]

    def reference_matrix(self, references: list[tuple[str, bytes]]):
        import numpy as np

        assessed = self.assess_references(references)
        embeddings = [item.embedding for item in assessed if item.valid]
        if not embeddings:
            raise RuntimeError("None of this person's photos show a face Clipivo can use. Add a clearer, front-facing photo.")
        return np.vstack(embeddings).astype(np.float32)

    def _ensure_application(self):
        if self._application is not None:
            return self._application
        status = self.status()
        if not status.available:
            raise RuntimeError(status.detail)
        from insightface.app import FaceAnalysis

        providers = ["CPUExecutionProvider"]
        if self.use_gpu and status.device == "GPU":
            providers.insert(0, "CUDAExecutionProvider")
            self._used_gpu = True
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            application = FaceAnalysis(
                name="buffalo_l",
                root=str(self.model_root),
                allowed_modules=["detection", "recognition"],
                providers=providers,
            )
        actual_providers = {
            provider
            for model in application.models.values()
            for provider in model.session.get_providers()
        }
        self._used_gpu = "CUDAExecutionProvider" in actual_providers
        with redirect_stdout(output), redirect_stderr(output):
            application.prepare(
                ctx_id=0 if self._used_gpu else -1,
                det_size=(self.detector_size, self.detector_size),
            )
        self._application = application
        return application

    @staticmethod
    def _nvidia_device_available() -> bool:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return False
        try:
            completed = subprocess.run(
                [executable, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return completed.returncode == 0 and bool(completed.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _decode(data: bytes):
        import cv2
        import numpy as np

        array = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(array, cv2.IMREAD_COLOR) if array.size else None

    @staticmethod
    def _reference_faces(application, image) -> list:
        """Faces in a reference photo. A photo cropped tight around a face fills the detector's
        whole input and is missed, so retry with a border that makes the face smaller."""
        import cv2

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"insightface\..*")
            faces = application.get(image)
            for ratio in (1.0, 2.0):
                if faces or not hasattr(image, "shape"):
                    break
                border = int(max(image.shape[:2]) * ratio)
                padded = cv2.copyMakeBorder(image, border, border, border, border, cv2.BORDER_CONSTANT, value=(0, 0, 0))
                faces = application.get(padded)
        return faces

    @staticmethod
    def _face_area(face) -> float:
        return float((face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1]))

    def _model_files(self) -> tuple[Path, Path]:
        directory = self.model_root / "models" / "buffalo_l"
        return directory / "det_10g.onnx", directory / "w600k_r50.onnx"
