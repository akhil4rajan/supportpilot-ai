from pathlib import Path

from app.rag.index import KnowledgeBase

KB_DIR = Path(__file__).resolve().parent.parent / "tenants" / "acme" / "knowledge"


def kb() -> KnowledgeBase:
    return KnowledgeBase.from_directory(KB_DIR)


def test_finds_relevant_policy():
    hits = kb().search("how long do refunds take?")
    assert hits and hits[0].chunk.source == "returns.md"
    assert "5-7 business days" in hits[0].chunk.text
    assert hits[0].coverage >= 0.35


def test_off_topic_has_low_coverage():
    hits = kb().search("who won the football world cup in 2018")
    assert not hits or hits[0].coverage < 0.35


def test_heading_boost_and_cancel():
    hits = kb().search("can I cancel my order")
    assert hits[0].chunk.source == "account.md"


def test_empty_and_stopword_queries():
    assert kb().search("the a is") == []
    assert KnowledgeBase().search("anything") == []
