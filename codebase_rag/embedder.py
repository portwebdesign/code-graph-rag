from __future__ import annotations

import sys

from codebase_rag.ai import embedder as _embedder

sys.modules[__name__] = _embedder
