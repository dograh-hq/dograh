import pytest

from api.tasks.knowledge_base_processing import _embed_texts_in_batches


class FakeEmbeddingService:
    def __init__(self):
        self.calls = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[float(len(text))] for text in texts]


@pytest.mark.asyncio
async def test_embed_texts_in_batches_preserves_order():
    service = FakeEmbeddingService()

    embeddings = await _embed_texts_in_batches(
        service,
        ["a", "bb", "ccc", "dddd", "eeeee"],
        batch_size=2,
    )

    assert service.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert embeddings == [[1.0], [2.0], [3.0], [4.0], [5.0]]
