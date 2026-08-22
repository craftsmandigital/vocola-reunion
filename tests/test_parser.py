"""Unit tests for the command-service grammar parser."""

from __future__ import annotations

import pytest


RULES = [
    {"name": "copy_that", "say": "copy that",
     "actions": [{"action": "keypress", "keys": ["ctrl", "c"]}]},
    {"name": "paste_that", "say": "paste that",
     "actions": [{"action": "keypress", "keys": ["ctrl", "v"]}]},
    {"name": "save", "say": "save",
     "actions": [{"action": "keypress", "keys": ["ctrl", "s"]}]},
    # Valggruppe med felles handlinger.
    {"name": "show_desktop", "say": "(show | hide) desktop",
     "actions": [{"action": "keypress", "keys": ["super", "d"]}]},
    # Valggruppe med egne handlinger per valg (branches).
    {"name": "focus_app", "say": "focus (chrome | terminal)",
     "actions": [{"action": "keypress", "keys": ["alt", "tab"]}],
     "branches": {
         "chrome": [{"action": "keypress", "keys": ["super", "1"]}],
         "terminal": [{"action": "keypress", "keys": ["super", "2"]}],
     }},
    # Kompleks makro med wait og type_text (TC-02-aktig).
    {"name": "reformat_block", "say": "reformat block",
     "actions": [
         {"action": "keypress", "keys": ["shift", "up"]},
         {"action": "wait", "ms": 150},
         {"action": "type_text", "text": "hello"},
     ]},
]


# --- normalize -------------------------------------------------------------


def test_normalize_strips_punctuation_and_case(parser):
    assert parser.normalize("Copy That!") == "copy that"


def test_normalize_collapses_whitespace(parser):
    assert parser.normalize("  COPY    that. ") == "copy that"


# --- expand_say ------------------------------------------------------------


def test_expand_say_without_groups(parser):
    assert parser.expand_say("copy that") == [("copy that", ())]


def test_expand_say_with_group(parser):
    expanded = dict(parser.expand_say("focus (chrome | terminal)"))
    assert set(expanded) == {"focus chrome", "focus terminal"}
    assert expanded["focus chrome"] == ("chrome",)
    assert expanded["focus terminal"] == ("terminal",)


def test_expand_say_leading_group(parser):
    assert {p for p, _ in parser.expand_say("(show | hide) desktop")} == {
        "show desktop", "hide desktop",
    }


# --- Grammar matching --------------------------------------------------------


@pytest.fixture
def grammar(parser):
    return parser.Grammar(RULES)


def test_exact_match(grammar):
    match = grammar.match("Copy that!")
    assert match is not None
    assert match.name == "copy_that"
    assert match.actions == ({"action": "keypress", "keys": ["ctrl", "c"]},)


def test_match_normalizes_heard_text(grammar):
    assert grammar.match("  SAVE.") .name == "save"
    assert grammar.match("PASTE   THAT").name == "paste_that"


def test_alternation_group_matches_both_variants(grammar):
    assert grammar.match("show desktop").name == "show_desktop"
    assert grammar.match("hide desktop").name == "show_desktop"


def test_branches_pick_actions_per_choice(parser):
    grammar = parser.Grammar([RULES[4]])
    assert grammar.match("focus chrome").actions == (
        {"action": "keypress", "keys": ["super", "1"]},)
    assert grammar.match("focus terminal").actions == (
        {"action": "keypress", "keys": ["super", "2"]},)


def test_branch_falls_back_to_default_actions(parser):
    rules = [{
        "name": "open_app", "say": "(open | launch) (chrome | terminal)",
        "actions": [{"action": "keypress", "keys": ["alt", "tab"]}],
        "branches": {
            "chrome": [{"action": "keypress", "keys": ["super", "1"]}],
        },
    }]
    grammar = parser.Grammar(rules)
    # 'chrome' treffer branch; alt annet faller tilbake til default actions.
    assert grammar.match("open chrome").actions == (
        {"action": "keypress", "keys": ["super", "1"]},)
    assert grammar.match("launch terminal").actions == (
        {"action": "keypress", "keys": ["alt", "tab"]},)


def test_macro_preserves_order_and_types(grammar):
    match = grammar.match("reformat block")
    kinds = [op["action"] for op in match.actions]
    assert kinds == ["keypress", "wait", "type_text"]
    assert match.actions[1] == {"action": "wait", "ms": 150}


def test_unknown_command_returns_none(grammar):
    assert grammar.match("elephant") is None
    assert grammar.match("") is None
    assert grammar.match("copy the thing") is None


def test_phrases_lists_all_expansions(grammar):
    phrases = grammar.phrases()
    assert "copy that" in phrases
    assert "focus chrome" in phrases
    assert "focus terminal" in phrases
    assert "hide desktop" in phrases


# --- Validation --------------------------------------------------------------


def test_duplicate_phrase_raises(parser):
    rules = [
        {"name": "a", "say": "copy that",
         "actions": [{"action": "keypress", "keys": ["c"]}]},
        {"name": "b", "say": "Copy That!",
         "actions": [{"action": "keypress", "keys": ["v"]}]},
    ]
    with pytest.raises(ValueError, match="Duplicate phrase"):
        parser.Grammar(rules)


def test_missing_actions_raises(parser):
    with pytest.raises(ValueError):
        parser.Grammar([{"name": "x", "say": "do stuff"}])


def test_unknown_action_type_raises(parser):
    with pytest.raises(ValueError):
        parser.Grammar([{"name": "x", "say": "boom",
                         "actions": [{"action": "explode"}]}])


def test_wait_requires_positive_int(parser):
    with pytest.raises(ValueError):
        parser.Grammar([{"name": "x", "say": "w",
                         "actions": [{"action": "wait", "ms": -1}]}])
