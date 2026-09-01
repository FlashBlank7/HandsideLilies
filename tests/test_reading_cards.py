from __future__ import annotations

from pathlib import Path

from lilies.core.database import Database


def test_reading_cards_save_search_filter_and_delete(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    explain_id = database.save_reading_card(
        "Dropout randomly masks activations during training.",
        "Dropout 会在训练时随机屏蔽一部分激活值。",
        kind="explain",
        title="Dropout",
        metadata={"sourceApp": "wpspdf.exe", "ephemeral": True},
    )
    term_id = database.save_reading_card(
        "The attention mechanism assigns a weight to each token.",
        "术语：注意力机制（attention mechanism）\n它为不同 token 分配权重。",
        kind="term",
        title="注意力机制（attention mechanism）",
    )

    cards = database.reading_cards()
    assert {card["card_id"] for card in cards} == {explain_id, term_id}
    assert database.reading_cards(query="随机")[0]["card_id"] == explain_id
    assert database.reading_cards(query="attention")[0]["card_id"] == term_id
    assert database.reading_cards(kind="term")[0]["kind"] == "term"
    assert database.reading_cards(kind="translate") == []
    assert next(card for card in cards if card["card_id"] == explain_id)["metadata"] == {
        "sourceApp": "wpspdf.exe",
        "ephemeral": True,
    }

    assert database.delete_reading_card(explain_id) is True
    assert database.delete_reading_card(explain_id) is False
    assert [card["card_id"] for card in database.reading_cards()] == [term_id]


def test_reading_card_update_keeps_identity_and_creation_time(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    card_id = database.save_reading_card("source", "first", title="old")
    before = database.reading_cards()[0]

    returned = database.save_reading_card(
        "updated source",
        "second",
        kind="translate",
        title="new",
        question="ignored here",
        card_id=card_id,
    )
    after = database.reading_cards()[0]

    assert returned == card_id
    assert after["card_id"] == card_id
    assert after["created_at"] == before["created_at"]
    assert after["title"] == "new"
    assert after["source_text"] == "updated source"
    assert after["answer"] == "second"
    assert after["kind"] == "translate"


def test_reading_search_treats_sql_wildcards_as_literal(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    percent = database.save_reading_card("Accuracy improved by 5%.", "five percent")
    database.save_reading_card("Accuracy improved greatly.", "no numeric value")

    assert [card["card_id"] for card in database.reading_cards(query="5%") ] == [percent]
    assert database.reading_cards(query="_") == []
    assert database.reading_cards(limit=0) == []
