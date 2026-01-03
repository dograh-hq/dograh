from unittest.mock import Mock

from api.services.workflow.pipecat_engine_callbacks import (
    create_aggregation_correction_callback,
)


def test_aggregation_fixer():
    """Validate the aggregation correction algorithm using a helper that
    creates a fresh callback for every (reference, corrupted) pair.

    The production callback now needs a PipecatEngine instance with the
    `_current_llm_generation_reference_text` set.  For test-friendliness we mock a bare
    object providing just that attribute for each assertion so the original
    two-argument test cases remain unchanged.
    """

    def fixer(reference: str, corrupted: str) -> str:  # noqa: D401
        mock_engine = Mock()
        mock_engine._current_llm_generation_reference_text = reference
        return create_aggregation_correction_callback(mock_engine)(corrupted)

    ##### Trailing extra Chars #####

    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
        )
        == "My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?"
    ), "leading_whole_sentence"

    # Whole sentences
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "Good Morning Mr NAR GES  , My name is Alex and I am calling you from Cons umer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
        )
        == "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?"
    ), "whole_sentences"

    # With a period in the end
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "Good Morning Mr NAR GES  , My name is Alex and I am calling you from Cons umer Services.",
        )
        == "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services."
    ), "period_end"

    # without a period in the end
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "Good Morning Mr NAR GES  , My name is Alex and I am calling you from Cons umer Services",
        )
        == "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services"
    ), "without_period_end"

    # Extra space in the end
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "Good Morning Mr NAR GES  , My name is Alex and I am calling you from Cons umer Services ",
        )
        == "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services"
    ), "extra_space"

    # Multiple spaces in corruption
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "Good Morning Mr NAR GES  , My name is Alex and I am calling you from Cons umer Servi  ces ",
        )
        == "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services"
    ), "multiple_space"

    # Multiple spaces in corruption ending in a whitespace
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "Good Morning Mr NAR GES  , My name is Alex and I am calling you from Cons umer Servi  ces. ",
        )
        == "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. "
    ), "multiple_space_end_ws"

    ##### Leading extra Chars #####

    # Whole sentences
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
        )
        == "My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?"
    ), "leading_whole_sentence"

    # With a period in the end
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Services.",
        )
        == "My name is Alex and I am calling you from Consumer Services."
    ), "leading_period_end"

    # without a period in the end
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Services",
        )
        == "My name is Alex and I am calling you from Consumer Services"
    ), "leading_without_period_end"

    # Extra space in the end
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Services ",
        )
        == "My name is Alex and I am calling you from Consumer Services"
    ), "leading_extra_space"

    # Multiple spaces in corruption
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Servi  ces ",
        )
        == "My name is Alex and I am calling you from Consumer Services"
    ), "leading_multiple_space"

    # Multiple spaces in corruption ending in a whitespace
    assert (
        fixer(
            "Good Morning Mr NARGES  , My name is Alex and I am calling you from Consumer Services. The reason of my call today, as I can see in our records that you are making your monthly credit card payments on time, but you STILL carry a balance of over 7 thousand dollars, right?",
            "My name is Alex and I am calling you from Cons umer Servi  ces. ",
        )
        == "My name is Alex and I am calling you from Consumer Services. "
    ), "leading_multiple_space_end_ws"

    # Whitespace
    assert fixer("", "") == ""

    # Missing reference
    assert (
        fixer("", "My name is Alex and I am calling you from Cons umer Servi  ces.")
        == "My name is Alex and I am calling you from Cons umer Servi  ces."
    ), "missing_reference"

    # Smaller reference
    assert (
        fixer(
            "My name is Alex",
            "My name is Alex and I am calling you from Cons umer Servi  ces.",
        )
        == "My name is Alex and I am calling you from Cons umer Servi  ces."
    ), "smaller_reference"

    # Unrelated reference
    assert (
        fixer(
            "Hello Hello",
            "My name is Alex and I am calling you from Cons umer Servi  ces.",
        )
        == "My name is Alex and I am calling you from Cons umer Servi  ces."
    ), "unrelated_reference"


def test_create_aggregation_correction_callback():
    """Test the new aggregation correction callback creator."""
    # Mock engine with reference text
    mock_engine = Mock()
    mock_engine._current_llm_generation_reference_text = "Good Morning Mr NARGES, My name is Alex and I am calling you from Consumer Services."

    # Create callback
    callback = create_aggregation_correction_callback(mock_engine)

    # Test correction
    corrected = callback(
        "Good Morning Mr NAR GES, My name is Alex and I am calling you from Cons umer Services."
    )
    assert (
        corrected
        == "Good Morning Mr NARGES, My name is Alex and I am calling you from Consumer Services."
    )

    # Test with no reference text
    mock_engine._current_llm_generation_reference_text = ""
    corrected = callback("Some corrupted text")
    assert corrected == "Some corrupted text"  # Should return as-is when no reference
