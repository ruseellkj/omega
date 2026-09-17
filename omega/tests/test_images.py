"""Image reading — the last Tier 3 item, and the one that tests the union.

`types.py` has said since Tier 1 that content blocks are a **discriminated
union**, and `TIER-2.md` used that to argue images would be "an addition, not a
change to every caller". This is where that gets checked.

## The part that is not mechanical

`ToolResultMessage.content` is `list[TextContent]`. A tool that returns an image
has nowhere to put it, so the union has to grow — and the moment it does, **the
two providers disagree about where an image may appear**:

| | an image in a tool result |
|---|---|
| Anthropic | allowed, inside the `tool_result` block |
| OpenAI Chat Completions | **rejected** — a `tool` message takes text only |

That is exactly the kind of divergence the provider layer exists to absorb, and
the same shape as the rule the two adapters already disagree about (Anthropic
merges results into one user message; OpenAI requires one each). Neither rule may
escape its adapter.

## Detection is by content, not by name

Tau reads magic bytes (`image_processing.py:39`) rather than trusting a file
extension. A `.png` that is really a text file is an ordinary mistake; sending it
as an image is a rejected request, and sending it as text is a wasted turn.
"""

from __future__ import annotations

import base64
from pathlib import Path

from omega_agent.types import ImageContent, ToolResultMessage, UserMessage
from omega_ai.anthropic import to_anthropic_messages
from omega_ai.openai import to_openai_messages
from omega_coding.builtin_tools import build_tools

#: A real 1x1 PNG, so the fixture is honest about being a file.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF+AV0AAAAASUVORK5CYII="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def _read_image(root: Path, path: str) -> ToolResultMessage:
    tool = {t.name: t for t in build_tools(root)}["read_image"]
    result = await tool.execute({"path": path}, None)
    return ToolResultMessage(tool_call_id="1", tool_name="read_image", content=result.content)


# ------------------------------------------------------------------- the tool


async def test_reading_a_png_returns_an_image_block(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(PNG)

    message = await _read_image(tmp_path, "shot.png")

    images = [b for b in message.content if isinstance(b, ImageContent)]
    assert len(images) == 1
    assert images[0].media_type == "image/png"
    assert base64.b64decode(images[0].data) == PNG


async def test_the_type_comes_from_the_bytes_not_the_name(tmp_path: Path) -> None:
    """**Tau's rule, and the right one.** An extension is a claim; magic bytes
    are evidence. A JPEG saved as `.png` must be sent as a JPEG, or the provider
    rejects a request that looked fine locally."""
    (tmp_path / "mislabelled.png").write_bytes(JPEG)

    message = await _read_image(tmp_path, "mislabelled.png")

    images = [b for b in message.content if isinstance(b, ImageContent)]
    assert images[0].media_type == "image/jpeg"


async def test_a_gif_is_recognised(tmp_path: Path) -> None:
    (tmp_path / "anim.gif").write_bytes(GIF)

    message = await _read_image(tmp_path, "anim.gif")

    assert any(
        isinstance(b, ImageContent) and b.media_type == "image/gif" for b in message.content
    )


async def test_a_file_that_is_not_an_image_says_so(tmp_path: Path) -> None:
    """Named rather than guessed at. Sending a text file as an image is a
    rejected request; sending it as text is a wasted turn."""
    (tmp_path / "notes.png").write_text("this is not a png\n")

    message = await _read_image(tmp_path, "notes.png")

    assert not any(isinstance(b, ImageContent) for b in message.content)
    assert "not a" in message.text.lower() or "unrecognised" in message.text.lower()


# --------------------------------------------------- the two providers disagree


def test_anthropic_puts_the_image_inside_the_tool_result() -> None:
    """Anthropic accepts image blocks in a `tool_result`, so that is where it
    goes — closest to where the model asked for it."""
    message = ToolResultMessage(
        tool_call_id="1",
        tool_name="read_image",
        content=[ImageContent(media_type="image/png", data=_encoded(PNG))],
    )

    sent = to_anthropic_messages([UserMessage(content="look"), message])

    result_block = sent[-1]["content"][0]
    assert result_block["type"] == "tool_result"
    inner = result_block["content"]
    assert isinstance(inner, list)
    assert inner[0]["type"] == "image"
    assert inner[0]["source"]["media_type"] == "image/png"


def test_openai_moves_the_image_into_a_following_user_message() -> None:
    """**The divergence, absorbed by the adapter.**

    A `tool` message in Chat Completions takes text only — an image there is
    rejected outright. So the tool result carries a text note and the image
    follows as a user message, which is the one place OpenAI accepts one.

    The neutral `ToolResultMessage` above did not have to pick a side, which is
    the whole argument for the layer restated on a new content type.
    """
    message = ToolResultMessage(
        tool_call_id="1",
        tool_name="read_image",
        content=[ImageContent(media_type="image/png", data=_encoded(PNG))],
    )

    sent = to_openai_messages("", [UserMessage(content="look"), message])

    tool_message = next(m for m in sent if m.get("role") == "tool")
    assert isinstance(tool_message["content"], str), "no image in a tool message"

    carrier = sent[-1]
    assert carrier["role"] == "user"
    parts = carrier["content"]
    assert isinstance(parts, list)
    assert any(p.get("type") == "image_url" for p in parts)
    assert any("data:image/png;base64," in str(p) for p in parts)


def test_a_text_only_tool_result_is_unchanged_on_both_providers() -> None:
    """The regression guard. Every existing session is text-only, and growing the
    union must not alter how any of them is sent."""
    message = ToolResultMessage(tool_call_id="1", tool_name="read_file", content="hello")

    anthropic = to_anthropic_messages([message])
    openai = to_openai_messages("", [message])

    assert anthropic[0]["content"][0]["content"] == "hello"
    assert openai[0]["content"] == "hello"
    assert len(openai) == 1, "no stray user message was invented"


def test_the_text_property_ignores_image_blocks() -> None:
    """`.text` is read everywhere — the cost meter, the log, redaction. It must
    return prose, not a megabyte of base64."""
    message = ToolResultMessage(
        tool_call_id="1",
        tool_name="read_image",
        content=[ImageContent(media_type="image/png", data=_encoded(PNG))],
    )

    assert message.text == "", "an image contributes no text"
