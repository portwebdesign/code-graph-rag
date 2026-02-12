from __future__ import annotations

from codebase_rag.services.embeddings_service import EmbeddingsService


def test_embed_text_caches(monkeypatch) -> None:
    calls: list[str] = []

    def fake_embed(text: str) -> list[float]:
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(
        "codebase_rag.services.embeddings_service.embed_code", fake_embed
    )

    service = EmbeddingsService(max_cache_size=2)

    first = service.embed_text("hello")
    second = service.embed_text("hello")

    assert first == second
    assert service.embedded_count == 1
    assert service.cache_hit_rate > 0
    assert calls == ["hello"]


def test_embed_function_uses_signature_lite(monkeypatch) -> None:
    monkeypatch.setattr(
        "codebase_rag.services.embeddings_service.embed_code",
        lambda _: [0.0, 0.0, 0.0],
    )

    service = EmbeddingsService(max_cache_size=4)
    result = service.embed_function(
        {
            "name": "do_work",
            "signature_lite": "do_work(x)",
            "docstring": "doc",
            "qualified_name": "pkg.do_work",
        }
    )

    assert "name_embedding" in result
    assert "signature_embedding" in result
    assert "docstring_embedding" in result
    assert "semantic_embedding" in result
