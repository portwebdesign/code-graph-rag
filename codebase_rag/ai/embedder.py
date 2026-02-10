"""
This module is responsible for generating semantic vector embeddings for code snippets
using the UniXcoder model.

It uses a singleton pattern for the model instance, managed by `functools.lru_cache`,
to ensure the large model is loaded into memory only once. The core functionality
is provided by the `embed_code` function, which takes a string of code and returns
its vector representation.

This module has optional dependencies (`torch`, `transformers`). If these are not
installed, calling `embed_code` will raise a `RuntimeError`, allowing the rest of
the application to function without semantic capabilities.
"""

# ┌────────────────────────────────────────────────────────────────────────┐
# │ UniXcoder Model Singleton via LRU Cache                              │
# ├────────────────────────────────────────────────────────────────────────┤
# │ get_model() provides:                                                 │
# │   - Singleton behavior without global variables                       │
# │   - Thread-safe lazy initialization                                   │
# │   - Easy testability with cache_clear() method                        │
# │   - Memory efficient with maxsize=1                                   │
# └────────────────────────────────────────────────────────────────────────┘
from functools import lru_cache

from codebase_rag.core.config import settings
from codebase_rag.core.constants import UNIXCODER_MODEL
from codebase_rag.utils.dependencies import has_torch, has_transformers

from ..infrastructure import exceptions as ex

if has_torch() and has_transformers():
    import numpy as np
    import torch
    from numpy.typing import NDArray

    from .unixcoder import UniXcoder

    @lru_cache(maxsize=1)
    def get_model() -> UniXcoder:
        """
        Lazily initializes and returns a singleton instance of the UniXcoder model.

        The `lru_cache(maxsize=1)` decorator ensures that the model is loaded only
        once and that the same instance is returned on subsequent calls. This is a
        memory-efficient way to manage a large model.

        Returns:
            UniXcoder: The singleton instance of the UniXcoder model.
        """
        model = UniXcoder(UNIXCODER_MODEL)
        model.eval()
        if torch.cuda.is_available():
            model = model.cuda()
        return model

    def embed_code(code: str, max_length: int | None = None) -> list[float]:
        """
        Generates a vector embedding for a given string of code.

        Args:
            code (str): The source code to embed.
            max_length (int | None): The maximum token length for the input.
                                     Defaults to `settings.EMBEDDING_MAX_LENGTH`.

        Returns:
            list[float]: The generated vector embedding as a list of floats.
        """
        if max_length is None:
            max_length = settings.EMBEDDING_MAX_LENGTH
        model = get_model()
        device = next(model.parameters()).device
        tokens = model.tokenize([code], max_length=max_length)
        tokens_tensor = torch.tensor(tokens).to(device)
        with torch.no_grad():
            _, sentence_embeddings = model(tokens_tensor)
            embedding: NDArray[np.float32] = sentence_embeddings.cpu().numpy()
        result: list[float] = embedding[0].tolist()
        return result

else:

    def embed_code(code: str, max_length: int | None = None) -> list[float]:
        """
        Raises a RuntimeError if the required ML dependencies are not installed.

        This function serves as a placeholder when `torch` or `transformers` are
        not available, ensuring the application can still run without semantic
        features.
        """
        raise RuntimeError(ex.SEMANTIC_EXTRA)
