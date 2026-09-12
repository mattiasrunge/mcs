"""Composition rules for a media description — what a *description* reads like.

Ported from MURRiX's `modules/media/lib/describe.ts`, prompt text byte for byte: the prompt
version rides in every caption's provenance (`<model>/<version>`), and a caption written
under the same model and version must be comparable across the move.

The captioner returns raw captions and whisper a transcript; these functions decide the prompt
the captioner is asked, how a video's keyframe captions are joined, how what was seen and what
was said become one paragraph, and how face geometry grounds the prompt without naming anyone.
"""

from __future__ import annotations

# Bumped whenever the prompt text below changes. It rides in the description's provenance as
# `<model>/<version>`, so a caption's provenance names the prompt that produced it and not just
# the weights; without it a prompt change is invisible.
PROMPT_VERSION = "p4"

# What the captioner is asked for. The abstention clause is the load-bearing sentence: this
# text is embedded and becomes what semantic search matches on, so a confidently wrong
# "smiling happily" poisons the index while an omission merely fails to help it. MAN/WOMAN/BOY/
# GIRL rather than "person": every caption saying person costs search the most obvious query.
# "exact ages" rather than "ages": a number is a guess, the band a boy or girl implies is what
# the archive wants back.
PROMPT_IMAGE = (
    "Describe this photograph for a family photo archive. Say what is visible: the setting, "
    "the objects, and what the people are doing. Call each person a man, woman, boy or girl. "
    "Describe a facial expression or emotion only "
    "when it is unmistakable; when it is not, describe the face plainly and say nothing about "
    "the emotion. Do not guess names, exact ages, relationships or nationalities. Do not "
    "speculate about the occasion, the date or the place unless it is written in the image. "
    'Write two to four plain sentences. Do not begin with "The image shows".'
)

# The video variant. Terser, because the frames are handed over together and described as one
# clip rather than four stills.
PROMPT_VIDEO = (
    "Describe this video clip for a family photo archive. Say what is visible across the frames: "
    "the setting, the objects, and what the people are doing. Call each person a man, woman, "
    "boy or girl. Describe a facial expression or emotion only when it is unmistakable. Do not "
    "guess names, exact ages or relationships. Write one or two plain sentences. Do not begin "
    'with "The video shows".'
)

# One description of a clip from what was seen and what was said, rather than the two stapled
# together: frames and speech are halves of one event.
PROMPT_VIDEO_SUMMARY_SYSTEM = (
    "You describe a home video for a family archive, from what is visible in its frames and what "
    "is said in it. Reply with one short paragraph saying what the clip is about: the setting, "
    "what happens, and what is talked about. Name people, places and activities that come up. "
    'Do not mention frames, transcripts, subtitles or "the video". Reply with the description only.'
)

# One paragraph saying what was said in a recording, for the description of an audio file (and
# the spoken half of a video's). The words themselves stay in the transcript.
PROMPT_TRANSCRIPT_SUMMARY_SYSTEM = (
    "You summarize what is said in a home recording. Given a transcript, reply with a short "
    "paragraph saying what is talked about. Name the people, places, activities and events that "
    'come up. Do not mention the recording, the transcript or the speakers as "speakers". Reply '
    "with the summary only."
)

TOKENS_IMAGE = 256
# The keyframes are captioned in one call that describes the whole clip, so this is the budget
# for the entire video description rather than for one frame of four.
TOKENS_VIDEO = 256
TOKENS_SUMMARY = 160
VIDEO_KEYFRAMES = 4

# Description used when an audio file carries no recognisable speech.
NO_SPEECH = "Audio with no detected speech"

# How much raw transcript stands in for a summary when no model could produce one — some
# description beats none.
SUMMARY_MIN_CHARS = 400
SUMMARY_INPUT_CHARS = 12000


def join_video_captions(captions: list[str]) -> str:
    """Keyframes of the same scene caption identically, so duplicates are dropped."""
    if len(captions) == 1:
        return captions[0]
    unique = list(dict.fromkeys(captions))
    return "Video showing: " + ". ".join(unique)


def compose_description(visual: str, transcript: str) -> str:
    """The fallback when no model can merge the two halves: what was seen, then what was said."""
    return f"{visual}\n\nSpoken: {transcript}" if transcript else visual


def video_summary_prompt(visual: str, transcript: str) -> str:
    """The user half of the merge request. Empty speech is stated, so the model invents none."""
    return f"Seen in the frames: {visual}\n\nSaid in the clip: {transcript or '(nothing audible)'}"


def _position_of(box: dict, index: int, total: int) -> str:
    if total == 2:
        return "left" if index == 0 else "right"
    if total == 3:
        return ["left", "middle", "right"][index]
    centre = box["x"] + box["width"] / 2
    if centre < 0.2:
        return "far left"
    if centre < 0.4:
        return "left of centre"
    if centre < 0.6:
        return "centre"
    if centre < 0.8:
        return "right of centre"
    return "far right"


_NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _count(n: int) -> str:
    return _NUMBER_WORDS[n] if n < len(_NUMBER_WORDS) else str(n)


def face_grounding(boxes: list[dict]) -> str:
    """Tell the captioner how many people are in frame and where — never who.

    Geometry rather than names: a name baked into caption text goes stale the moment a face is
    reassigned or a person renamed, and buys search nothing it does not get from the person
    graph. Geometry stops the model miscounting and lets it attribute actions to the right body,
    and is stable under every identity change. Empty when there is nothing to say, so the
    caller concatenates unconditionally. Boxes are fractions of the display frame.
    """
    if not boxes:
        return ""
    if len(boxes) == 1:
        return " There is one person in this photograph."
    ordered = sorted(boxes, key=lambda b: b["x"] + b["width"] / 2)
    positions = ", ".join(_position_of(b, i, len(ordered)) for i, b in enumerate(ordered))
    return (
        f" There are {_count(len(ordered))} people in this photograph, positioned from left to "
        f"right as: {positions}. Refer to each by position rather than by name."
    )


def grounded_on(boxes: list[dict]) -> str:
    """Provenance for the grounding: what the caption was told, so a detection change is visible.

    Centres rounded to two decimals — enough to notice a face appearing, moving or being
    dropped, coarse enough that a re-detection jittering a box by a pixel does not. The same
    string MURRiX's `composeGroundedOn` writes, which is what its staleness check compares.
    """
    if not boxes:
        return "faces:0"
    centres = sorted(b["x"] + b["width"] / 2 for b in boxes)
    return f"faces:{len(boxes)}@{','.join(_to_fixed_2(c) for c in centres)}"


def _to_fixed_2(value: float) -> str:
    """JavaScript's `toFixed(2)`: round the exact binary value, ties away from zero.

    Python's `f"{x:.2f}"` rounds ties to even, so an exactly representable centre such as 0.125
    would print 0.12 here and 0.13 in MURRiX. The two strings are compared by a staleness check
    that must never disagree, so the rounding is JavaScript's.
    """
    from decimal import ROUND_HALF_UP, Decimal

    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def model_name(*models: str | None) -> str:
    """Provenance naming every model that contributed, joined with `+`."""
    return "+".join(m for m in models if m)
