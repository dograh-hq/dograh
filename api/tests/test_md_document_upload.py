"""Tests for Markdown (.md) document upload and MIME type detection."""

from api.db.knowledge_base_client import KnowledgeBaseClient


def test_markdown_file_mime_type_resolution():
    assert KnowledgeBaseClient.get_mime_type("document.md") == "text/markdown"
    assert KnowledgeBaseClient.get_mime_type("README.MD") == "text/markdown"
    assert KnowledgeBaseClient.get_mime_type("/path/to/notes.md") == "text/markdown"
