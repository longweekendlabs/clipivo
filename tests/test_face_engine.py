from __future__ import annotations

import unittest

import numpy as np

from core.face_engine import InsightFaceEngine


class FakeFace:
    def __init__(self, embedding, area: int = 100):
        self.normed_embedding = np.asarray(embedding, dtype=np.float32)
        self.bbox = np.asarray([0, 0, area, 1], dtype=np.float32)


class FakeApplication:
    def __init__(self, faces_by_image):
        self.faces_by_image = faces_by_image

    def get(self, image):
        return self.faces_by_image.get(image, [])


class TestInsightFaceEngine(InsightFaceEngine):
    def __init__(self, faces_by_image):
        super().__init__(".")
        self.application = FakeApplication(faces_by_image)

    def _ensure_application(self):
        return self.application

    @staticmethod
    def _decode(data: bytes):
        return None if data == b"unreadable" else data


class FaceEngineTests(unittest.TestCase):
    def test_reference_matrix_contains_one_normalized_row_per_valid_image(self) -> None:
        engine = TestInsightFaceEngine({
            b"one": [FakeFace([1.0, 0.0])],
            b"two": [FakeFace([0.8, 0.6])],
        })
        matrix = engine.reference_matrix([("one.jpg", b"one"), ("two.jpg", b"two")])
        self.assertEqual(matrix.shape, (2, 2))
        np.testing.assert_allclose(matrix[1], [0.8, 0.6])

    def test_invalid_reference_and_multiple_face_warning(self) -> None:
        engine = TestInsightFaceEngine({
            b"none": [],
            b"group": [FakeFace([1, 0], 50), FakeFace([1, 0], 200)],
        })
        results = engine.assess_references([
            ("broken.jpg", b"unreadable"),
            ("empty.jpg", b"none"),
            ("group.jpg", b"group"),
        ])
        self.assertEqual(results[0].reason, "Unreadable image")
        self.assertEqual(results[1].reason, "No usable face found")
        self.assertIn("Multiple faces", results[2].warning)

    def test_face_cropped_tight_is_found_with_a_border(self) -> None:
        class TightCropApplication:
            # Like the real detector, it misses a face that fills the whole picture.
            def get(self, image):
                return [FakeFace([1.0, 0.0])] if image.shape[0] > 300 else []

        engine = TestInsightFaceEngine({})
        engine.application = TightCropApplication()
        engine._decode = staticmethod(lambda data: np.zeros((200, 140, 3), dtype=np.uint8))
        results = engine.assess_references([("tight.png", b"tight")])
        self.assertTrue(results[0].valid)

    def test_reference_outlier_is_warned_without_rejecting_it(self) -> None:
        faces = {f"same-{index}".encode(): [FakeFace([1, 0])] for index in range(4)}
        faces[b"outlier"] = [FakeFace([-1, 0])]
        engine = TestInsightFaceEngine(faces)
        inputs = [(name.decode(), name) for name in faces]
        results = engine.assess_references(inputs)
        self.assertTrue(all(item.valid for item in results))
        self.assertIn("outlier", results[-1].warning.lower())
        self.assertFalse(any(item.warning for item in results[:-1]))

    def test_still_photo_matching_obeys_threshold(self) -> None:
        engine = TestInsightFaceEngine({
            b"reference": [FakeFace([1, 0])],
            b"match": [FakeFace([0.9, 0.1])],
            b"miss": [FakeFace([0.1, 0.9])],
        })
        references = [("reference.jpg", b"reference")]
        self.assertTrue(engine.match_image(b"match", references, 0.80).matched)
        self.assertFalse(engine.match_image(b"miss", references, 0.80).matched)

    def test_multi_character_matching_detects_faces_once(self) -> None:
        engine = TestInsightFaceEngine({b"frame": [FakeFace([0.9, 0.1])]})
        targets = {
            "alex": (np.asarray([[1.0, 0.0]], dtype=np.float32), 0.80),
            "sam": (np.asarray([[0.0, 1.0]], dtype=np.float32), 0.80),
        }
        results = engine.match_frame_many(b"frame", targets)
        self.assertTrue(results["alex"].matched)
        self.assertFalse(results["sam"].matched)


if __name__ == "__main__":
    unittest.main()
