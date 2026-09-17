import pytest

from runtime.llm.mock import MockLLMClient
from runtime.llm.router import ModelRouter, RoutingRequest


def make_router():
    default = MockLLMClient()
    fast = MockLLMClient()
    reasoning = MockLLMClient()
    tool = MockLLMClient()

    return ModelRouter(
        clients={
            "default": default,
            "fast": fast,
            "reasoning": reasoning,
            "tool": tool,
        },
        default_model="default",
    ), {
        "default": default,
        "fast": fast,
        "reasoning": reasoning,
        "tool": tool,
    }


def test_router_uses_default_model():
    router, clients = make_router()

    selected = router.route(
        RoutingRequest(
            purpose="GENERAL",
        )
    )

    assert selected is clients["default"]


def test_router_prefers_fast_model_for_low_latency():
    router, clients = make_router()

    selected = router.route(
        RoutingRequest(
            purpose="DECISION",
            prefer_low_latency=True,
        )
    )

    assert selected is clients["fast"]


def test_router_selects_tool_model_when_tools_are_required():
    router, clients = make_router()

    selected = router.route(
        RoutingRequest(
            purpose="DECISION",
            requires_tools=True,
        )
    )

    assert selected is clients["tool"]


def test_router_selects_reasoning_model_for_high_complexity():
    router, clients = make_router()

    selected = router.route(
        RoutingRequest(
            purpose="DECISION",
            complexity="HIGH",
        )
    )

    assert selected is clients["reasoning"]


def test_router_selects_reasoning_model_for_planning():
    router, clients = make_router()

    selected = router.route(
        RoutingRequest(
            purpose="PLANNING",
            complexity="MEDIUM",
        )
    )

    assert selected is clients["reasoning"]


def test_router_selects_reasoning_model_for_replanning():
    router, clients = make_router()

    selected = router.route(
        RoutingRequest(
            purpose="REPLANNING",
        )
    )

    assert selected is clients["reasoning"]


def test_router_falls_back_to_default_when_specialized_model_is_missing():
    default = MockLLMClient()

    router = ModelRouter(
        clients={"default": default},
        default_model="default",
    )

    selected = router.route(
        RoutingRequest(
            purpose="PLANNING",
            complexity="HIGH",
        )
    )

    assert selected is default


def test_router_rejects_empty_client_registry():
    with pytest.raises(
        ValueError,
        match="At least one LLM client",
    ):
        ModelRouter(
            clients={},
            default_model="default",
        )


def test_router_rejects_unknown_default_model():
    with pytest.raises(
        ValueError,
        match="Default model 'missing' is not configured",
    ):
        ModelRouter(
            clients={"default": MockLLMClient()},
            default_model="missing",
        )
