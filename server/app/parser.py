"""Grammar loader and strict matcher for the command service.

Loads a YAML rule set (Vocola3-subset) and compiles it into an exact-match
lookup table. Rules may contain alternation groups — ``focus (chrome |
terminal)`` — which are expanded at load time into concrete phrases, so
matching at runtime is a single dictionary lookup.

This module is intentionally dependency-light (stdlib + PyYAML) so it can be
unit-tested without faster-whisper or CUDA installed.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_GROUP_RE = re.compile(r"\(([^()]+)\)")

_VALID_ACTIONS = ("keypress", "wait", "type_text")
_VALID_MODIFIER_HINTS = frozenset({"ctrl", "alt", "shift", "super", "cmd"})


def normalize(text: str) -> str:
    """Normalize spoken text for strict matching.

    Lowercases, replaces anything that is not a letter or digit with a
    space, collapses whitespace and strips the ends. ``"Copy That!"`` and
    ``"copy  that."`` both become ``"copy that"``.
    """
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def expand_say(say: str) -> list[tuple[str, tuple[str, ...]]]:
    """Expand a rule phrase into concrete normalized phrases.

    Returns ``(phrase, chosen_words)`` pairs where ``chosen_words`` holds the
    word picked from each alternation group, in order. A rule without groups
    yields exactly one pair with an empty ``chosen_words`` tuple.
    """
    segments = _GROUP_RE.split(say)
    results: list[tuple[str, tuple[str, ...]]] = [("", ())]
    for index, segment in enumerate(segments):
        if index % 2 == 0:  # literal text between groups
            literal = normalize(segment)
            results = [
                (f"{phrase} {literal}".strip(), chosen)
                for phrase, chosen in results
            ]
        else:  # alternation group content
            alternatives = [normalize(alt) for alt in segment.split("|")]
            alternatives = [alt for alt in alternatives if alt]
            if not alternatives:
                raise ValueError(f"Empty alternation group in rule phrase: {say!r}")
            results = [
                (f"{phrase} {alt}".strip(), (*chosen, alt))
                for phrase, chosen in results
                for alt in alternatives
            ]
    if not results or all(not phrase for phrase, _ in results):
        raise ValueError(f"Rule phrase normalizes to nothing: {say!r}")
    return [(phrase, chosen) for phrase, chosen in results if phrase]


@dataclass(frozen=True)
class RuleMatch:
    """A successful grammar match."""

    name: str
    actions: tuple[dict[str, Any], ...]


class Grammar:
    """Compiled lookup table from normalized phrase to action sequence."""

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self._table: dict[str, RuleMatch] = {}
        for rule in rules:
            self._add_rule(rule)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Grammar:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data.get("rules") or [])

    def _add_rule(self, rule: dict[str, Any]) -> None:
        name = str(rule.get("name") or "").strip()
        say = str(rule.get("say") or "").strip()
        default_actions = rule.get("actions")
        branches = rule.get("branches") or {}

        if not name:
            raise ValueError("Rule is missing 'name'")
        if not say:
            raise ValueError(f"Rule {name!r} is missing 'say'")

        for phrase, chosen in expand_say(say):
            actions = _resolve_actions(name, default_actions, branches, chosen)
            _validate_actions(name, phrase, actions)
            if phrase in self._table:
                raise ValueError(
                    f"Duplicate phrase {phrase!r} in grammar "
                    f"(rules {self._table[phrase].name!r} and {name!r})"
                )
            self._table[phrase] = RuleMatch(
                name=name,
                actions=tuple(copy.deepcopy(action) for action in actions),
            )

    def match(self, heard_text: str) -> RuleMatch | None:
        """Strictly match already-normalizable ASR text against the grammar."""
        return self._table.get(normalize(heard_text))

    def phrases(self) -> list[str]:
        """All concrete phrases in the grammar, sorted alphabetically."""
        return sorted(self._table)

    def __len__(self) -> int:
        return len(self._table)


def _resolve_actions(
    name: str,
    default_actions: object,
    branches: dict[str, Any],
    chosen: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Pick the action sequence for one concrete expansion of a rule."""
    for word in chosen:
        if word in branches:
            return list(branches[word])
    if default_actions is not None:
        return list(default_actions)
    raise ValueError(
        f"Rule {name!r}: no actions for phrase choice {chosen!r} "
        "(no matching branch and no default 'actions')"
    )


def _validate_actions(rule_name: str, phrase: str, actions: list[dict[str, Any]]) -> None:
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"Rule {rule_name!r} ({phrase!r}): 'actions' must be a non-empty list")
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(
                f"Rule {rule_name!r} ({phrase!r}): action #{i} is not an object"
            )
        kind = action.get("action")
        if kind not in _VALID_ACTIONS:
            raise ValueError(
                f"Rule {rule_name!r} ({phrase!r}): action #{i} has unknown type {kind!r}"
            )
        if kind == "keypress":
            keys = action.get("keys")
            if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
                raise ValueError(
                    f"Rule {rule_name!r} ({phrase!r}): keypress needs non-empty string list 'keys'"
                )
        elif kind == "wait":
            ms = action.get("ms")
            if not isinstance(ms, int) or ms <= 0:
                raise ValueError(f"Rule {rule_name!r} ({phrase!r}): wait needs positive integer 'ms'")
        elif kind == "type_text":
            if not isinstance(action.get("text"), str) or not action["text"]:
                raise ValueError(f"Rule {rule_name!r} ({phrase!r}): type_text needs non-empty 'text'")
