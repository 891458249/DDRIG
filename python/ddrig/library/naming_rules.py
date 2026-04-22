"""DDRIG naming rule registry.

A *naming rule* is a JSON document that describes how a side token
(``L`` / ``R`` / ``C``) is embedded into a DAG node name built out of
label tokens. Rules are files in
``python/ddrig/resources/naming_rules/``; the active rule is persisted
in ``db.userSettings.activeNamingRule``.

Rule schema
-----------

    {
        "name":        str,                        # globally unique
        "description": str,                        # optional, UI only
        "builtin":     bool,                       # True = shipped, undeletable
        "sides": {
            "L": {"mode": "...", "token": "...", "separator": "_"},
            "R": {"mode": "...", "token": "...", "separator": "_"},
            "C": {"mode": "...", "token": "...", "separator": "_"}
        }
    }

``mode`` is one of ``prefix`` / ``suffix`` / ``mid`` / ``none``.  With
``mode = "none"`` the side is omitted entirely; ``token`` / ``separator``
are ignored.  With any other mode, ``token`` is required and
``separator`` defaults to ``"_"``.

Two different "default" constants exist on purpose — do not merge them:

    NEW_INSTALL_DEFAULT   -- active rule the first time DDRIG is run.
                             Reflects the user-agreed convention
                             (L/R prefix, Center untagged).

    LEGACY_FALLBACK       -- used when loading a session archive that
                             has no ``namingRule`` metadata.  Mirrors
                             the hard-coded behaviour of DDRIG before
                             this registry existed (``{side}_`` prefix
                             for every side).
"""
from __future__ import annotations

import glob
import json
import os


NEW_INSTALL_DEFAULT = "DDRIG Default"
LEGACY_FALLBACK = "All Prefix Upper"

VALID_MODES = ("prefix", "suffix", "mid", "none")
VALID_SIDES = ("L", "R", "C")

# python/ddrig/library/naming_rules.py -> python/ddrig/resources/naming_rules/
_RULES_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.pardir,
        "resources",
        "naming_rules",
    )
)


# ---------------------------------------------------------------------------
# Filesystem layer
# ---------------------------------------------------------------------------

def rules_dir():
    """Absolute path to the directory that holds *.json rule files."""
    return _RULES_DIR


def list_rules():
    """Return every rule dict, builtins first then user-created, each
    alphabetically within its group. Malformed JSON files are silently
    skipped so a bad user rule cannot take the whole registry down."""
    if not os.path.isdir(_RULES_DIR):
        return []
    builtins, user = [], []
    for path in glob.glob(os.path.join(_RULES_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "name" not in data:
            continue
        if data.get("builtin", False):
            builtins.append(data)
        else:
            user.append(data)
    builtins.sort(key=lambda r: r["name"])
    user.sort(key=lambda r: r["name"])
    return builtins + user


def get_rule(name):
    """Return the rule dict with the given name, or None."""
    if not name:
        return None
    for r in list_rules():
        if r["name"] == name:
            return r
    return None


def validate_rule(rule):
    """Raise ValueError on malformed rule."""
    if not isinstance(rule, dict):
        raise ValueError("Rule must be a dict.")
    name = rule.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("Rule must have a non-empty string 'name'.")
    sides = rule.get("sides")
    if not isinstance(sides, dict):
        raise ValueError("Rule must have a 'sides' dict.")
    for side in VALID_SIDES:
        cfg = sides.get(side)
        if not isinstance(cfg, dict):
            raise ValueError("Rule 'sides' missing entry for %r." % side)
        mode = cfg.get("mode")
        if mode not in VALID_MODES:
            raise ValueError(
                "Side %r has invalid mode %r (allowed: %s)."
                % (side, mode, ", ".join(VALID_MODES))
            )
        if mode != "none":
            token = cfg.get("token")
            if not isinstance(token, str) or not token:
                raise ValueError(
                    "Side %r mode=%r requires a non-empty 'token'."
                    % (side, mode)
                )


def _slugify(name):
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " _-":
            out.append("_")
    slug = "".join(out).strip("_") or "rule"
    return slug


def save_rule(rule):
    """Persist a user-created rule.  Forces ``builtin=False`` and writes
    the file with a ``user_`` prefix so .gitignore can exclude them.
    Raises ValueError on duplicate name or malformed rule."""
    rule = dict(rule)  # shallow copy, do not mutate caller input
    rule.pop("builtin", None)
    rule["builtin"] = False
    validate_rule(rule)
    if get_rule(rule["name"]):
        raise ValueError("Rule name %r already exists." % rule["name"])
    os.makedirs(_RULES_DIR, exist_ok=True)
    path = os.path.join(_RULES_DIR, "user_" + _slugify(rule["name"]) + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rule, f, indent=2, ensure_ascii=False)
    return path


def delete_rule(name):
    """Delete a user-created rule.  Builtins are refused."""
    rule = get_rule(name)
    if rule is None:
        raise ValueError("Rule %r not found." % name)
    if rule.get("builtin", False):
        raise ValueError("Rule %r is builtin and cannot be deleted." % name)
    for path in glob.glob(os.path.join(_RULES_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("name") == name:
            os.remove(path)
            return path
    # name matched get_rule but no file on disk -> stale cache? treat as gone
    raise ValueError("Rule %r has no backing file on disk." % name)


# ---------------------------------------------------------------------------
# Active-rule persistence (wraps db.userSettings.activeNamingRule)
# ---------------------------------------------------------------------------

def _user_settings():
    """Return the shared UserSettings instance (or None if unavailable)."""
    try:
        from ddrig.core import database
    except ImportError:
        return None
    try:
        return database.Database().userSettings
    except Exception:
        return None


def get_active_rule_name():
    """Return the name of the currently active rule.  Falls back to
    ``NEW_INSTALL_DEFAULT`` if userSettings is unreadable."""
    us = _user_settings()
    if us is not None:
        name = getattr(us, "activeNamingRule", None)
        if name:
            return name
    return NEW_INSTALL_DEFAULT


def set_active_rule_name(name):
    """Persist ``name`` as the active rule.  Validates that the rule
    exists on disk before writing."""
    if not get_rule(name):
        raise ValueError("Rule %r not found." % name)
    us = _user_settings()
    if us is None:
        raise RuntimeError("userSettings is unavailable; cannot persist.")
    us.activeNamingRule = name
    us.apply()


def get_active_rule():
    """Return the full dict of the active rule, with safety fallbacks."""
    rule = get_rule(get_active_rule_name())
    if rule is None:
        rule = get_rule(NEW_INSTALL_DEFAULT)
    if rule is None:
        rule = get_rule(LEGACY_FALLBACK)
    return rule


# ---------------------------------------------------------------------------
# Side embedding / extraction
# ---------------------------------------------------------------------------

def _separator(cfg):
    sep = cfg.get("separator", "_")
    return sep if sep else "_"


def apply_side(side, labels, prefix="", suffix="", rule=None):
    """Build the final name by inserting the side token per ``rule``.

    Args:
        side: one of ``L`` / ``R`` / ``C`` or empty string.  An unknown
            or empty side makes this a pure concatenation
            (``prefix + labels + suffix`` joined by underscore).
        labels: a list of intermediate tokens.  A string is accepted and
            wrapped in a 1-element list.
        prefix: optional extra prefix token placed after the side.
        suffix: optional trailing token placed before the side in the
            ``suffix`` side mode.
        rule: explicit rule dict (else the active rule is used).

    Returns:
        The dash-free underscore-joined name.
    """
    if rule is None:
        rule = get_active_rule()
    if not isinstance(labels, (list, tuple)):
        labels = [labels]
    labels = [str(x) for x in labels]

    if not side or side not in VALID_SIDES or rule is None:
        elements = [prefix] + labels + [suffix]
    else:
        cfg = rule["sides"][side]
        mode = cfg["mode"]
        token = cfg.get("token", "")
        if mode == "prefix":
            elements = [token, prefix] + labels + [suffix]
        elif mode == "suffix":
            elements = [prefix] + labels + [suffix, token]
        elif mode == "mid":
            if labels:
                elements = [prefix, labels[0], token] + labels[1:] + [suffix]
            else:
                elements = [prefix, token, suffix]
        else:  # none
            elements = [prefix] + labels + [suffix]
    elements = [str(e) for e in elements if e != "" and e is not None]
    return "_".join(elements)


def strip_side(name, side, rule):
    """Reverse of ``apply_side`` restricted to the side component.

    Given a ``name`` that was produced with ``apply_side(side, ..., rule)``,
    return the core part with the side token removed.  Returns ``None``
    when ``name`` does not match the pattern that ``rule`` produces for
    that side — callers can use that signal to reject rename attempts
    that would cross rules.

    ``side == ""`` or ``side`` not in ``VALID_SIDES`` returns ``name``
    unchanged (no-op, matches ``apply_side`` behaviour).
    """
    if not side or side not in VALID_SIDES or rule is None:
        return name
    cfg = rule["sides"][side]
    mode = cfg["mode"]
    if mode == "none":
        return name
    token = cfg.get("token", "")
    sep = _separator(cfg)
    if mode == "prefix":
        needle = token + sep
        if name.startswith(needle):
            return name[len(needle):]
        return None
    if mode == "suffix":
        needle = sep + token
        if name.endswith(needle):
            return name[:-len(needle)]
        return None
    if mode == "mid":
        needle = sep + token + sep
        idx = name.find(needle)
        if idx == -1:
            return None
        # Collapse the infix back to a single separator so the two halves
        # re-join naturally.
        return name[:idx] + sep + name[idx + len(needle):]
    return None
