from __future__ import annotations

import sys

from codebase_rag.data_models import vector_store as _vector_store

sys.modules[__name__] = _vector_store
