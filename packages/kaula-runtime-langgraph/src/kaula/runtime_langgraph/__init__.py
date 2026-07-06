"""kaula-runtime-langgraph: the LangGraph adapter.

The CrewAI-free sibling of `kaula-runtime` — a second runtime adapter against
the same `kaula.core` contracts and the same framework-free healing loop.

    from kaula.runtime_langgraph import heal_langgraph_tool
"""

from kaula.runtime_langgraph.adapter import heal_langgraph_tool

__all__ = ["heal_langgraph_tool"]
