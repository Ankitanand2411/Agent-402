from langgraph.graph import END, START, StateGraph

from agent import nodes
from agent.state import AgentRunState


def build_graph(checkpointer):
    g = StateGraph(AgentRunState)
    g.add_node("plan", nodes.plan)
    g.add_node("await_payment", nodes.await_payment)
    g.add_node("execute", nodes.execute)
    g.add_node("finish", nodes.finish)

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", nodes.route_after_plan, {"await_payment": "await_payment", "execute": "execute", "finish": "finish"})
    g.add_edge("await_payment", "execute")
    g.add_conditional_edges("execute", nodes.route_after_execute, {"plan": "plan", "finish": "finish"})
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)
