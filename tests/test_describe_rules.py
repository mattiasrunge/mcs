"""The description composition rules, ported from MURRiX: what they produce must not drift."""

from mcs import describe as rules


def box(x, width=0.1):
    return {"x": x, "y": 0.2, "width": width, "height": 0.2}


def test_face_grounding_counts_and_positions():
    assert rules.face_grounding([]) == ""
    assert rules.face_grounding([box(0.5)]) == " There is one person in this photograph."
    assert rules.face_grounding([box(0.7), box(0.1)]) == (
        " There are two people in this photograph, positioned from left to right as: left, right. Refer to each by position rather than by name."
    )
    assert "left, middle, right" in rules.face_grounding([box(0.1), box(0.5), box(0.8)])
    four = rules.face_grounding([box(0.0), box(0.3), box(0.5), box(0.9)])
    assert "four people" in four and "far left, left of centre, centre, far right" in four
    assert "eleven" not in rules.face_grounding([box(i / 12) for i in range(11)])
    assert "There are 11 people" in rules.face_grounding([box(i / 12) for i in range(11)])


def test_grounded_on_rounds_like_javascript():
    assert rules.grounded_on([]) == "faces:0"
    # Centres sorted, two decimals, ties away from zero: 0.125 is exactly representable and
    # JavaScript's toFixed(2) prints 0.13 where Python's format would print 0.12.
    assert rules.grounded_on([box(0.9, 0.1), box(0.075, 0.1)]) == "faces:2@0.13,0.95"
    assert rules.grounded_on([{"x": 0.432, "y": 0, "width": 0.094, "height": 0.1}]) == "faces:1@0.48"


def test_joins_and_prompts():
    assert rules.join_video_captions(["a"]) == "a"
    assert rules.join_video_captions(["a", "a", "b"]) == "Video showing: a. b"
    assert rules.compose_description("seen", "") == "seen"
    assert rules.compose_description("seen", "said") == "seen\n\nSpoken: said"
    assert rules.video_summary_prompt("seen", "") == "Seen in the frames: seen\n\nSaid in the clip: (nothing audible)"
    assert rules.model_name("a", None, "b") == "a+b"
    assert rules.PROMPT_VERSION == "p4"
    assert rules.PROMPT_IMAGE.startswith("Describe this photograph for a family photo archive.")
    assert rules.PROMPT_IMAGE.endswith('Do not begin with "The image shows".')
