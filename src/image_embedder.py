"""Embed images into the same 1024-d vector space as text using Visualized-BGE-M3.

bge-visualized-m3 extends BGE-M3 with a vision encoder that maps images
into the same embedding space as text. This enables cross-modal retrieval:
text queries can find relevant image attachments and vice versa.

Requirements:
- FlagEmbedding >= 1.3.0 with visual support
- Weight file: Visualized_m3.pth (~2.3 GB, downloaded separately)

When Visualized-BGE is not available, falls back gracefully to None.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp",
})

# Default search paths for the weight file
_WEIGHT_SEARCH_PATHS = [
    Path("models/Visualized_m3.pth"),
    Path.home() / ".cache" / "bge-visualized" / "Visualized_m3.pth",
]


def is_image_file(filename: str) -> bool:
    """Check if a filename has a supported image extension."""
    dot_pos = filename.rfind(".")
    if dot_pos == -1:
        return False
    return filename[dot_pos:].lower() in _IMAGE_EXTENSIONS


class ImageEmbedder:
    """Embed images into the BGE-M3 1024-d vector space.

    Uses Visualized-BGE-M3 when available, otherwise reports unavailability.
    """

    def __init__(
        self,
        weight_path: str | None = None,
        model_name: str = "BAAI/bge-m3",
    ) -> None:
        self._model = None
        self._available = False
        self._weight_path = self._resolve_weight_path(weight_path)

        if self._weight_path:
            self._try_load(model_name)

    @property
    def is_available(self) -> bool:
        """Whether the image embedder is ready to encode images."""
        return self._available

    def encode_image(self, image_bytes: bytes) -> list[float] | None:
        """Encode an image into a 1024-d embedding vector.

        Args:
            image_bytes: Raw image file bytes (PNG, JPG, etc.).

        Returns:
            1024-d embedding list, or None if unavailable or encoding fails.
        """
        if not self._available or not image_bytes:
            return None

        try:
            import tempfile

            # Visualized-BGE requires a file path, not bytes
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                tmp_path = f.name

            try:
                embedding = self._model.encode(image=tmp_path)  # type: ignore[union-attr]
                # Normalize to list of floats
                if hasattr(embedding, "tolist"):
                    return embedding.tolist()
                return list(embedding)
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        except Exception:  # noqa: BLE001
            logger.debug("Failed to encode image", exc_info=True)
            return None

    def encode_image_batch(
        self, images: list[bytes],
    ) -> list[list[float] | None]:
        """Encode multiple images.

        Args:
            images: List of raw image bytes.

        Returns:
            List of 1024-d embeddings (None for failures).
        """
        return [self.encode_image(img) for img in images]

    def _resolve_weight_path(self, explicit_path: str | None) -> str | None:
        """Find the Visualized-BGE weight file."""
        if explicit_path:
            p = Path(explicit_path)
            if p.exists():
                return str(p)
            logger.warning("Weight file not found: %s", explicit_path)
            return None

        for candidate in _WEIGHT_SEARCH_PATHS:
            if candidate.exists():
                logger.info("Found Visualized-BGE weights: %s", candidate)
                return str(candidate)

        logger.info(
            "Visualized-BGE weight file not found. Image embedding disabled. "
            "Download from HuggingFace and place at: %s",
            _WEIGHT_SEARCH_PATHS[0],
        )
        return None

    def _try_load(self, model_name: str) -> None:
        """Attempt to load the Visualized-BGE model."""
        try:
            from FlagEmbedding.visual.modeling import Visualized_BGE  # type: ignore[import-untyped]

            self._model = Visualized_BGE(
                model_name_bge=model_name,
                model_weight=self._weight_path,
            )
            self._available = True
            logger.info("Visualized-BGE loaded from %s", self._weight_path)
        except ImportError:
            logger.info(
                "FlagEmbedding visual module not available. "
                "Install FlagEmbedding >= 1.3.0 for image embedding."
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to load Visualized-BGE model", exc_info=True)
