"""Selección determinista del modelo local: cargado, afinidad, por defecto, orden y bloqueo sin fallback.

@author Rodrigo Mason
"""

from __future__ import annotations

from local_control_center.agents.local_model_selection import (
    normalize_model_capabilities,
    resolve_local_model,
)
from local_control_center.agents.local_model_settings import LocalModelSetting

MODELS = ("alpha", "beta", "gamma")
ALL = frozenset(MODELS)
CHAT = frozenset({"chat"})


def _setting(model, *, default=False, order=0, code_edit=False, code_review=False) -> LocalModelSetting:
    return LocalModelSetting(
        provider_id="llama_cpp",
        model=model,
        is_default=default,
        code_edit=code_edit,
        code_review=code_review,
        operator_order=order,
        provenance={},
    )


def _resolve(**overrides):
    arguments = {
        "required_capabilities": CHAT,
        "enabled_models": MODELS,
        "settings": (),
        "validated_models": ALL,
        "load_states": {},
    }
    arguments.update(overrides)
    return resolve_local_model(**arguments)


def test_a_loaded_model_wins_without_a_switch():
    result = _resolve(
        load_states={"beta": "loaded", "alpha": "unloaded"}, settings=[_setting("alpha", default=True)]
    )
    assert (result.model, result.requires_switch, result.reason, result.from_model) == (
        "beta",
        False,
        "loaded",
        None,
    )


def test_several_loaded_models_break_ties_by_default_then_order_then_id():
    loaded = {"alpha": "loaded", "beta": "loaded", "gamma": "loaded"}
    assert _resolve(load_states=loaded, settings=[_setting("gamma", default=True)]).model == "gamma"
    ordered = [_setting("alpha", order=2), _setting("beta", order=1), _setting("gamma", order=3)]
    assert _resolve(load_states=loaded, settings=ordered).model == "beta"
    assert _resolve(load_states=loaded).model == "alpha"


def test_without_a_loaded_model_the_default_is_chosen_and_the_switch_is_reported():
    result = _resolve(
        load_states={"alpha": "unloaded", "beta": "unloaded", "gamma": "loaded"},
        validated_models=frozenset({"alpha", "beta"}),
        settings=[_setting("beta", default=True)],
    )
    assert (result.model, result.reason, result.requires_switch, result.from_model) == (
        "beta",
        "default",
        True,
        "gamma",
    )


def test_affinity_from_another_role_beats_the_default():
    result = _resolve(
        load_states={"alpha": "unloaded", "beta": "unloaded"},
        settings=[_setting("alpha", default=True)],
        affinity_model="beta",
    )
    assert (result.model, result.reason, result.requires_switch) == ("beta", "affinity", True)


def test_operator_order_decides_without_a_default_and_unknown_state_never_switches():
    result = _resolve(
        settings=[_setting("alpha", order=3), _setting("beta", order=2), _setting("gamma", order=1)]
    )
    assert (result.model, result.reason, result.requires_switch, result.from_model) == (
        "gamma",
        "operator_order",
        False,
        None,
    )


def test_role_capabilities_restrict_the_eligible_set_in_both_vocabularies():
    coder = _resolve(
        required_capabilities=frozenset({"chat", "code"}),
        settings=[_setting("gamma", code_edit=True)],
        load_states={"alpha": "loaded"},
    )
    assert (coder.model, coder.requires_switch) == ("gamma", False)
    reviewer = _resolve(
        required_capabilities=frozenset({"code_review"}), settings=[_setting("beta", code_review=True)]
    )
    assert reviewer.model == "beta"


def test_capability_vocabularies_normalize_to_the_model_names():
    assert normalize_model_capabilities(
        ["Chat", "code", "issue_to_patch", "review", "vision", " "]
    ) == frozenset({"chat", "code_edit", "code_review", "vision"})
    assert normalize_model_capabilities(["code_edit", "code_review"]) == frozenset(
        {"code_edit", "code_review"}
    )


def test_a_developer_that_requires_code_accepts_a_model_with_the_code_edit_opt_in():
    developer = _resolve(
        required_capabilities=frozenset({"chat", "code"}),
        settings=[_setting("beta", code_edit=True)],
    )
    assert (developer.model, developer.blocked_cause) == ("beta", None)


def test_explicit_model_capabilities_override_the_settings():
    result = _resolve(
        required_capabilities=frozenset({"code"}),
        model_capabilities={"alpha": frozenset({"chat", "code"})},
    )
    assert result.model == "alpha"
    canonical = _resolve(
        required_capabilities=frozenset({"code_edit"}),
        model_capabilities={"gamma": frozenset({"chat", "code"})},
    )
    assert canonical.model == "gamma"


def test_a_default_saved_under_a_server_alias_resolves_to_its_canonical_model():
    result = _resolve(
        enabled_models=("alpha", "beta"),
        validated_models=frozenset({"alpha", "beta"}),
        settings=[_setting("local", default=True)],
        load_states={"alpha": "unloaded", "beta": "unloaded", "local": "unloaded"},
        model_aliases={"local": "beta"},
    )
    assert (result.model, result.reason, result.requires_switch) == ("beta", "default", True)
    without_aliases = _resolve(
        enabled_models=("alpha", "beta"),
        validated_models=frozenset({"alpha", "beta"}),
        settings=[_setting("local", default=True)],
    )
    assert (without_aliases.model, without_aliases.reason) == ("alpha", "operator_order")


def test_disabled_or_unvalidated_models_never_win_and_an_empty_set_blocks():
    assert _resolve(enabled_models=("alpha",), load_states={"beta": "loaded"}).model == "alpha"
    blocked = _resolve(validated_models=frozenset())
    assert (blocked.model, blocked.blocked_cause, blocked.requires_switch) == (
        None,
        "local_model_not_validated",
        False,
    )
