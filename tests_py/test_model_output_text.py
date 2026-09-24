"""Normalización lineal de la salida de modelo: bloques <think>, fences y JSON parseable."""

from __future__ import annotations

import json

from local_control_center.agents.model_output_text import (
    json_candidate_text,
    strip_code_fences,
    strip_reasoning_blocks,
)


def test_think_block_is_removed_and_reasoning_is_counted() -> None:
    cleaned, reasoning = strip_reasoning_blocks('<think>plan the patch</think>\n{"ok": true}')

    assert cleaned == '{"ok": true}'
    assert reasoning == len("plan the patch")


def test_orphan_closing_tag_drops_the_prefilled_reasoning() -> None:
    cleaned, reasoning = strip_reasoning_blocks('reasoning opened by the template</think>\n\n{"ok": 1}')

    assert cleaned == '{"ok": 1}'
    assert reasoning > 0


def test_unclosed_block_drops_everything_after_it() -> None:
    cleaned, reasoning = strip_reasoning_blocks("prefix <think>still thinking when max_tokens hit")

    assert cleaned == "prefix"
    assert reasoning == len("still thinking when max_tokens hit")


def test_multiple_blocks_are_removed_and_text_without_blocks_is_untouched() -> None:
    cleaned, reasoning = strip_reasoning_blocks("<think>a</think>A<think></think>B<think>c</think>C")
    untouched, none = strip_reasoning_blocks("  plain answer  ")

    assert cleaned == "ABC"
    assert reasoning == 2
    assert untouched == "  plain answer  "
    assert none == 0


def test_adversarial_repetition_is_processed_in_one_linear_pass() -> None:
    text = "<think>x</think>y" * 50_000

    cleaned, reasoning = strip_reasoning_blocks(text)

    assert cleaned == "y" * 50_000
    assert reasoning == 50_000


def test_code_fence_is_removed_only_when_it_wraps_the_payload() -> None:
    assert strip_code_fences('```json\n{"ok": true}\n```') == '{"ok": true}'
    assert strip_code_fences('{"text": "keep ``` inside"}') == '{"text": "keep ``` inside"}'


def test_json_candidate_text_parses_reasoning_plus_fenced_json() -> None:
    raw = '<think>check refs</think>\n```json\n{"verdict": "approve", "note": "prompt: keep"}\n```'

    assert json.loads(json_candidate_text(raw)) == {"verdict": "approve", "note": "prompt: keep"}
