"""Local image preparation for photographed book pages."""

from __future__ import annotations

from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:  # The DirectML alignment environment intentionally has no OpenCV.
    cv2 = None
    np = None


def _read_image(path: Path) -> np.ndarray:
    """Read paths with Cyrillic characters too (cv2.imread cannot reliably do that on Windows)."""
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image: {path}")
    return image


def _ordered_quad(points: np.ndarray) -> np.ndarray:
    points = points.reshape(4, 2).astype(np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    return np.array(
        [points[np.argmin(sums)], points[np.argmin(differences)], points[np.argmax(sums)], points[np.argmax(differences)]],
        dtype=np.float32,
    )


def _find_page_quad(image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    preview_scale = min(1.0, 1400 / max(height, width))
    preview = cv2.resize(image, None, fx=preview_scale, fy=preview_scale) if preview_scale < 1 else image
    gray = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 35, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    candidates: list[tuple[float, np.ndarray]] = []
    for contour in cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]:
        area = cv2.contourArea(contour)
        if area < preview.shape[0] * preview.shape[1] * 0.15:
            continue
        approx = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, w, h = cv2.boundingRect(approx)
        ratio = w / max(h, 1)
        if not 0.42 <= ratio <= 0.88:
            continue
        # Prefer a large vertical quadrilateral away from tiny high-contrast objects.
        score = area * (1.0 - min(abs(ratio - 0.62), 0.3))
        candidates.append((score, approx))
    if not candidates:
        return None
    quad = _ordered_quad(max(candidates, key=lambda item: item[0])[1])
    return quad / preview_scale


def _warp_page(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    top_left, top_right, bottom_right, bottom_left = quad
    page_width = int(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
    page_height = int(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
    target = np.array([[0, 0], [page_width - 1, 0], [page_width - 1, page_height - 1], [0, page_height - 1]], dtype=np.float32)
    return cv2.warpPerspective(image, cv2.getPerspectiveTransform(quad, target), (page_width, page_height), borderValue=(255, 255, 255))


def _crop_to_text_region(gray: np.ndarray) -> np.ndarray:
    """Remove ornamental headers/footers while retaining every normal text line."""
    height, width = gray.shape
    inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    # Join letters within a printed line.  Decorations tend to become tall or
    # irregular components; ordinary lines stay short and wide.
    joined = cv2.dilate(inverted, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, width // 42), 3)))
    boxes: list[tuple[int, int, int, int]] = []
    for contour in cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        x, y, w, h = cv2.boundingRect(contour)
        # A line glued to the physical edge is a border/ornament, not prose.
        if y < height * 0.05 or y + h > height * 0.96:
            continue
        if w >= width * 0.18 and 8 <= h <= height * 0.045:
            boxes.append((x, y, w, h))
    if len(boxes) < 3:
        return gray
    top = max(0, min(y for _, y, _, _ in boxes) - round(height * 0.02))
    bottom = min(height, max(y + h for _, y, _, h in boxes) + round(height * 0.025))
    if bottom - top < height * 0.35:
        return gray
    return gray[top:bottom, :]


def prepare_book_page(source: Path, destination: Path) -> Path:
    """Deskew/crop a photographed page and make its text contrast OCR-friendly."""
    if cv2 is None or np is None:
        raise ImportError("OpenCV is required only for image OCR preprocessing. Install opencv-python in this environment to use it.")
    image = _read_image(source)
    # Phone photos with a light page on a dark table are already sharp enough
    # for Tesseract.  A perspective warp can enlarge tiny lens artefacts, so
    # keep the native pixels and limit preparation to safe crop/contrast work.

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Remove a small physical page margin, where shadows and the neighbouring page occur.
    height, width = gray.shape
    x_margin, y_margin = round(width * 0.035), round(height * 0.035)
    gray = gray[y_margin : height - y_margin, x_margin : width - x_margin]
    gray = _crop_to_text_region(gray)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    gray = cv2.fastNlMeansDenoising(gray, None, 5, 7, 21)
    # Keep antialiasing in the printed letters. Hard thresholding made this
    # particular small serif typeface less readable for Tesseract.
    prepared = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", prepared)
    if not ok:
        raise ValueError(f"Cannot encode prepared image: {destination}")
    encoded.tofile(destination)
    return destination
