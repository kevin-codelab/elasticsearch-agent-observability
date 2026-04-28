#!/usr/bin/env python3
"""Framework-specific OTel instrumentation helpers.

Provides instrumentation patches for supported AI agent frameworks.
Each framework module monkey-patches known call sites to emit OTel spans
with GenAI semantic convention attributes. Coverage depends on framework
version and runtime shape.

Usage:
    # Auto-detect and patch at import time:
    from instrument_frameworks import auto_instrument
    auto_instrument()

    # Or patch a specific framework:
    from instrument_frameworks import instrument_crewai
    instrument_crewai()
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("agent-obsv.instrument")


def _get_tracer():
    """Get the OTel tracer, or None if SDK is not installed."""
    try:
        from opentelemetry import trace
        return trace.get_tracer("agent-observability.frameworks")
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Reasoning trace helpers
# ---------------------------------------------------------------------------

def traced_decision(
    *,
    action: str,
    decision_type: str = "routing",
    rationale: str = "",
    alternatives: str = "",
    confidence: float | None = None,
    input_summary: str = "",
    step_index: int | None = None,
):
    """Decorator / context-helper that emits a reasoning trace span.

    Use this to record WHY an agent chose a particular action at a decision point.

    Args:
        action: What the agent decided to do (tool_call / delegate / respond / wait / escalate)
        decision_type: Category of decision (routing / tool_selection / delegation / termination / retry)
        rationale: Free-text explanation of why this action was chosen
        alternatives: Rejected alternatives (comma-separated)
        confidence: Agent's self-reported confidence 0-1
        input_summary: Condensed input context (NOT the raw prompt)
        step_index: Ordinal within the turn (0-based)

    Can be used as a decorator:
        @traced_decision(action="tool_call", decision_type="tool_selection", rationale="DB has the data")
        def call_database(query): ...

    Or as a context manager via the returned span.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            tracer = _get_tracer()
            if not tracer:
                return func(*args, **kwargs)
            attrs: dict[str, Any] = {
                "gen_ai.agent_ext.reasoning.action": action,
                "gen_ai.agent_ext.reasoning.decision_type": decision_type,
                "gen_ai.operation.name": "decision",
            }
            if rationale:
                attrs["gen_ai.agent_ext.reasoning.rationale"] = rationale
            if alternatives:
                attrs["gen_ai.agent_ext.reasoning.alternatives"] = alternatives
            if confidence is not None:
                attrs["gen_ai.agent_ext.reasoning.confidence"] = confidence
            if input_summary:
                attrs["gen_ai.agent_ext.reasoning.input_summary"] = input_summary
            if step_index is not None:
                attrs["gen_ai.agent_ext.reasoning.step_index"] = step_index
            with tracer.start_as_current_span(
                f"decision.{decision_type}.{action}",
                attributes=attrs,
            ) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("event.outcome", "success")
                    return result
                except Exception as exc:
                    span.set_attribute("event.outcome", "failure")
                    span.set_attribute("error.type", type(exc).__name__)
                    span.record_exception(exc)
                    raise
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def emit_reasoning_span(
    *,
    action: str,
    decision_type: str = "routing",
    rationale: str = "",
    alternatives: str = "",
    confidence: float | None = None,
    input_summary: str = "",
    step_index: int | None = None,
) -> None:
    """Emit a standalone reasoning trace event (non-decorator usage).

    Call this at any decision point to record the agent's reasoning:

        emit_reasoning_span(
            action="tool_call",
            decision_type="tool_selection",
            rationale="User asked about weather, calling weather API",
            alternatives="web_search, cached_response",
            confidence=0.85,
        )
    """
    tracer = _get_tracer()
    if not tracer:
        return
    attrs: dict[str, Any] = {
        "gen_ai.agent_ext.reasoning.action": action,
        "gen_ai.agent_ext.reasoning.decision_type": decision_type,
        "gen_ai.operation.name": "decision",
    }
    if rationale:
        attrs["gen_ai.agent_ext.reasoning.rationale"] = rationale
    if alternatives:
        attrs["gen_ai.agent_ext.reasoning.alternatives"] = alternatives
    if confidence is not None:
        attrs["gen_ai.agent_ext.reasoning.confidence"] = confidence
    if input_summary:
        attrs["gen_ai.agent_ext.reasoning.input_summary"] = input_summary
    if step_index is not None:
        attrs["gen_ai.agent_ext.reasoning.step_index"] = step_index
    with tracer.start_as_current_span(f"decision.{decision_type}.{action}", attributes=attrs) as span:
        span.set_attribute("event.outcome", "success")


# ---------------------------------------------------------------------------
# AutoGen
# ---------------------------------------------------------------------------

def instrument_autogen() -> bool:
    """Patch AutoGen's ConversableAgent to emit OTel spans per message exchange.

    Traces:
    - Each agent.generate_reply() call → span with gen_ai.agent.name, gen_ai.operation.name
    - Tool executions → span with gen_ai.tool.name
    - Model calls → inherits from OpenAI/Anthropic auto-patch
    """
    tracer = _get_tracer()
    if not tracer:
        return False
    try:
        import importlib
        autogen = importlib.import_module("autogen")
        ConversableAgent = getattr(autogen, "ConversableAgent", None)
        if ConversableAgent is None:
            # Try autogen-agentchat package
            agentchat = importlib.import_module("autogen.agentchat")
            ConversableAgent = getattr(agentchat, "ConversableAgent", None)
        if ConversableAgent is None:
            logger.debug("AutoGen ConversableAgent not found")
            return False

        _orig_generate = ConversableAgent.generate_reply

        def _patched_generate(self, messages=None, sender=None, **kwargs):
            agent_name = getattr(self, "name", "unknown")
            with tracer.start_as_current_span(
                f"autogen.agent.{agent_name}",
                attributes={
                    "gen_ai.agent.name": agent_name,
                    "gen_ai.operation.name": "generate_reply",
                    "gen_ai.agent_ext.component_type": "runtime",
                },
            ) as span:
                import time as _time
                t0 = _time.monotonic()
                try:
                    result = _orig_generate(self, messages=messages, sender=sender, **kwargs)
                    span.set_attribute("event.outcome", "success")
                    return result
                except Exception as exc:
                    span.set_attribute("event.outcome", "failure")
                    span.set_attribute("error.type", type(exc).__name__)
                    span.record_exception(exc)
                    raise
                finally:
                    span.set_attribute("gen_ai.agent_ext.latency_ms", (_time.monotonic() - t0) * 1000)

        ConversableAgent.generate_reply = _patched_generate
        logger.info("AutoGen instrumented")
        return True
    except (ImportError, AttributeError) as exc:
        logger.debug("AutoGen instrumentation skipped: %s", exc)
        return False


# ---------------------------------------------------------------------------
# CrewAI
# ---------------------------------------------------------------------------

def instrument_crewai() -> bool:
    """Patch CrewAI's Crew.kickoff() and Agent.execute_task() for OTel spans.

    Traces:
    - Crew.kickoff() → top-level session span with gen_ai.conversation.id
    - Agent.execute_task() → per-agent span with gen_ai.agent.name
    - Tool._run() → per-tool span with gen_ai.tool.name
    """
    tracer = _get_tracer()
    if not tracer:
        return False
    try:
        import importlib
        crewai = importlib.import_module("crewai")
        Crew = getattr(crewai, "Crew", None)
        Agent = getattr(crewai, "Agent", None)
        if Crew is None:
            return False

        # Patch Crew.kickoff
        _orig_kickoff = Crew.kickoff

        def _patched_kickoff(self, *args, **kwargs):
            import uuid
            session_id = str(uuid.uuid4())[:8]
            crew_name = getattr(self, "name", None) or "crew"
            with tracer.start_as_current_span(
                f"crewai.crew.{crew_name}",
                attributes={
                    "gen_ai.conversation.id": session_id,
                    "gen_ai.operation.name": "crew_kickoff",
                    "gen_ai.agent_ext.component_type": "runtime",
                },
            ) as span:
                try:
                    result = _orig_kickoff(self, *args, **kwargs)
                    span.set_attribute("event.outcome", "success")
                    return result
                except Exception as exc:
                    span.set_attribute("event.outcome", "failure")
                    span.set_attribute("error.type", type(exc).__name__)
                    span.record_exception(exc)
                    raise

        Crew.kickoff = _patched_kickoff

        # Patch Agent.execute_task if available
        if Agent and hasattr(Agent, "execute_task"):
            _orig_execute = Agent.execute_task

            def _patched_execute(self, *args, **kwargs):
                agent_name = getattr(self, "role", None) or getattr(self, "name", "agent")
                with tracer.start_as_current_span(
                    f"crewai.agent.{agent_name}",
                    attributes={
                        "gen_ai.agent.name": str(agent_name),
                        "gen_ai.operation.name": "execute_task",
                        "gen_ai.agent_ext.component_type": "runtime",
                    },
                ) as span:
                    import time as _time
                    t0 = _time.monotonic()
                    try:
                        result = _orig_execute(self, *args, **kwargs)
                        span.set_attribute("event.outcome", "success")
                        return result
                    except Exception as exc:
                        span.set_attribute("event.outcome", "failure")
                        span.set_attribute("error.type", type(exc).__name__)
                        span.record_exception(exc)
                        raise
                    finally:
                        span.set_attribute("gen_ai.agent_ext.latency_ms", (_time.monotonic() - t0) * 1000)

            Agent.execute_task = _patched_execute

        logger.info("CrewAI instrumented")
        return True
    except (ImportError, AttributeError) as exc:
        logger.debug("CrewAI instrumentation skipped: %s", exc)
        return False


# ---------------------------------------------------------------------------
# LangGraph / LangChain
# ---------------------------------------------------------------------------

def instrument_langgraph() -> bool:
    """Patch LangGraph's CompiledGraph.invoke() and LangChain's BaseTool.run().

    Traces:
    - graph.invoke() → session-level span
    - tool.run() → per-tool span with gen_ai.tool.name
    - LLM calls → already covered by traceloop-sdk or the auto-patch in bootstrap snippet
    """
    tracer = _get_tracer()
    if not tracer:
        return False
    patched_any = False
    try:
        import importlib

        # Patch LangGraph CompiledGraph.invoke
        try:
            lg_graph = importlib.import_module("langgraph.graph")
            CompiledGraph = getattr(lg_graph, "CompiledStateGraph", None) or getattr(lg_graph, "CompiledGraph", None)
            if CompiledGraph and hasattr(CompiledGraph, "invoke"):
                _orig_invoke = CompiledGraph.invoke

                def _patched_invoke(self, *args, **kwargs):
                    config = kwargs.get("config") or (args[1] if len(args) > 1 else {})
                    thread_id = ""
                    if isinstance(config, dict):
                        thread_id = config.get("configurable", {}).get("thread_id", "")
                    with tracer.start_as_current_span(
                        "langgraph.invoke",
                        attributes={
                            "gen_ai.operation.name": "graph_invoke",
                            "gen_ai.agent_ext.component_type": "runtime",
                            **({"gen_ai.conversation.id": thread_id} if thread_id else {}),
                        },
                    ) as span:
                        import time as _time
                        t0 = _time.monotonic()
                        try:
                            result = _orig_invoke(self, *args, **kwargs)
                            span.set_attribute("event.outcome", "success")
                            return result
                        except Exception as exc:
                            span.set_attribute("event.outcome", "failure")
                            span.set_attribute("error.type", type(exc).__name__)
                            span.record_exception(exc)
                            raise
                        finally:
                            span.set_attribute("gen_ai.agent_ext.latency_ms", (_time.monotonic() - t0) * 1000)

                CompiledGraph.invoke = _patched_invoke
                patched_any = True
        except (ImportError, AttributeError):
            pass

        # Patch LangChain BaseTool.run
        try:
            lc_tools = importlib.import_module("langchain_core.tools")
            BaseTool = getattr(lc_tools, "BaseTool", None)
            if BaseTool and hasattr(BaseTool, "run"):
                _orig_run = BaseTool.run

                def _patched_run(self, *args, **kwargs):
                    tool_name = getattr(self, "name", "unknown_tool")
                    with tracer.start_as_current_span(
                        f"langchain.tool.{tool_name}",
                        attributes={
                            "gen_ai.tool.name": tool_name,
                            "gen_ai.operation.name": "tool_call",
                            "gen_ai.agent_ext.component_type": "tool",
                        },
                    ) as span:
                        try:
                            result = _orig_run(self, *args, **kwargs)
                            span.set_attribute("event.outcome", "success")
                            return result
                        except Exception as exc:
                            span.set_attribute("event.outcome", "failure")
                            span.set_attribute("error.type", type(exc).__name__)
                            span.record_exception(exc)
                            raise

                BaseTool.run = _patched_run
                patched_any = True
        except (ImportError, AttributeError):
            pass

        if patched_any:
            logger.info("LangGraph/LangChain instrumented")
        return patched_any
    except (ImportError, AttributeError) as exc:
        logger.debug("LangGraph instrumentation skipped: %s", exc)
        return False


# ---------------------------------------------------------------------------
# OpenAI Agents SDK
# ---------------------------------------------------------------------------

def instrument_openai_agents() -> bool:
    """Patch OpenAI Agents SDK's Runner.run() for OTel spans."""
    tracer = _get_tracer()
    if not tracer:
        return False
    try:
        import importlib
        agents = importlib.import_module("agents")
        Runner = getattr(agents, "Runner", None)
        if Runner is None:
            return False

        _orig_run = Runner.run

        async def _patched_run(cls, *args, **kwargs):
            agent = args[0] if args else kwargs.get("agent")
            agent_name = getattr(agent, "name", "agent") if agent else "agent"
            with tracer.start_as_current_span(
                f"openai.agents.{agent_name}",
                attributes={
                    "gen_ai.agent.name": agent_name,
                    "gen_ai.operation.name": "agent_run",
                    "gen_ai.agent_ext.component_type": "runtime",
                },
            ) as span:
                import time as _time
                t0 = _time.monotonic()
                try:
                    result = await _orig_run(*args, **kwargs)
                    span.set_attribute("event.outcome", "success")
                    return result
                except Exception as exc:
                    span.set_attribute("event.outcome", "failure")
                    span.set_attribute("error.type", type(exc).__name__)
                    span.record_exception(exc)
                    raise
                finally:
                    span.set_attribute("gen_ai.agent_ext.latency_ms", (_time.monotonic() - t0) * 1000)

        Runner.run = classmethod(_patched_run) if isinstance(_orig_run, classmethod) else _patched_run
        logger.info("OpenAI Agents SDK instrumented")
        return True
    except (ImportError, AttributeError) as exc:
        logger.debug("OpenAI Agents SDK instrumentation skipped: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Auto-detect and instrument
# ---------------------------------------------------------------------------

_INSTRUMENTORS = {
    "autogen": instrument_autogen,
    "crewai": instrument_crewai,
    "langgraph": instrument_langgraph,
    "openai-agents": instrument_openai_agents,
}


def auto_instrument(*, frameworks: list[str] | None = None) -> dict[str, bool]:
    """Auto-detect installed frameworks and instrument them.

    Args:
        frameworks: If provided, only instrument these frameworks.
                    If None, tries all known frameworks.

    Returns:
        Dict of framework_name -> whether it was successfully patched.
    """
    if os.environ.get("AGENT_OBSV_NO_AUTO_INSTRUMENT", "").lower() in ("1", "true", "yes"):
        return {}

    targets = frameworks or list(_INSTRUMENTORS.keys())
    results: dict[str, bool] = {}
    for name in targets:
        if name in _INSTRUMENTORS:
            results[name] = _INSTRUMENTORS[name]()
    patched = [k for k, v in results.items() if v]
    if patched:
        logger.info("Auto-instrumented: %s", ", ".join(patched))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = auto_instrument()
    for name, ok in results.items():
        status = "✓" if ok else "✗ (not installed or not patchable)"
        print(f"  {name}: {status}")
