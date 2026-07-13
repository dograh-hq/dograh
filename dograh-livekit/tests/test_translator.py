import pytest
from app.models import WorkflowGraph, RFNode, RFEdge, NodeData, EdgeData, Position
from app.translator.workflow import translate_workflow


@pytest.fixture
def agent_config():
    return {"system_prompt": "You are helpful.", "org_id": "org_1", "deploy_id": "dp_1"}


@pytest.fixture
def linear_graph():
    return WorkflowGraph(
        id="wf_1",
        nodes=[
            RFNode(id="n1", type="startCall", position=Position(x=0, y=0),
                   data=NodeData(name="Start", prompt="Greet the caller")),
            RFNode(id="n2", type="agentNode", position=Position(x=100, y=0),
                   data=NodeData(name="Qualify", prompt="Ask about budget")),
            RFNode(id="n3", type="endCall", position=Position(x=200, y=0),
                   data=NodeData(name="End", prompt="Goodbye")),
        ],
        edges=[
            RFEdge(id="e1", source="n1", target="n2", data=EdgeData(condition="*")),
            RFEdge(id="e2", source="n2", target="n3", data=EdgeData(condition="*")),
        ],
    )


@pytest.fixture
def branching_graph():
    return WorkflowGraph(
        id="wf_2",
        nodes=[
            RFNode(id="intent", type="startCall", position=Position(x=0, y=0),
                   data=NodeData(name="Intent", prompt="Identify intent")),
            RFNode(id="sales", type="agentNode", position=Position(x=100, y=-50),
                   data=NodeData(name="Sales", prompt="Sales pitch")),
            RFNode(id="support", type="agentNode", position=Position(x=100, y=50),
                   data=NodeData(name="Support", prompt="Help user")),
            RFNode(id="end", type="endCall", position=Position(x=200, y=0),
                   data=NodeData(name="End", prompt="Goodbye")),
        ],
        edges=[
            RFEdge(id="e1", source="intent", target="sales", data=EdgeData(condition="sales")),
            RFEdge(id="e2", source="intent", target="support", data=EdgeData(condition="support")),
            RFEdge(id="e3", source="sales", target="end", data=EdgeData(condition="*")),
            RFEdge(id="e4", source="support", target="end", data=EdgeData(condition="*")),
        ],
    )


class TestTranslateWorkflow:
    def test_linear_workflow_creates_steps(self, linear_graph, agent_config):
        workflow = translate_workflow(linear_graph, agent_config)
        assert workflow is not None
        assert len(workflow.steps) >= 3

    def test_branching_workflow_creates_router(self, branching_graph, agent_config):
        workflow = translate_workflow(branching_graph, agent_config)
        assert workflow is not None
        router_steps = [s for s in workflow.steps if hasattr(s, "selector")]
        assert len(router_steps) >= 1

    def test_non_agent_nodes_filtered(self, agent_config):
        graph = WorkflowGraph(
            nodes=[
                RFNode(id="n1", type="startCall", position=Position(x=0, y=0),
                       data=NodeData(name="Start", prompt="Hi")),
                RFNode(id="n_qa", type="qa", position=Position(x=100, y=0),
                       data=NodeData(name="QA", enabled=True)),
                RFNode(id="n2", type="endCall", position=Position(x=200, y=0),
                       data=NodeData(name="End", prompt="Bye")),
            ],
            edges=[RFEdge(id="e1", source="n1", target="n2", data=EdgeData(condition="*"))],
        )
        workflow = translate_workflow(graph, agent_config)
        agent_step_ids = [s.name for s in workflow.steps if not hasattr(s, "selector")]
        assert "n_qa" not in agent_step_ids

    def test_empty_graph(self, agent_config):
        graph = WorkflowGraph(nodes=[], edges=[])
        workflow = translate_workflow(graph, agent_config)
        assert len(workflow.steps) == 0

    def test_global_node_prepended(self, agent_config):
        graph = WorkflowGraph(
            nodes=[
                RFNode(id="global", type="globalNode", position=Position(x=0, y=0),
                       data=NodeData(name="Global", prompt="Be polite.")),
                RFNode(id="n1", type="startCall", position=Position(x=0, y=100),
                       data=NodeData(name="Start", prompt="Greet")),
                RFNode(id="n2", type="endCall", position=Position(x=100, y=100),
                       data=NodeData(name="End", prompt="Bye")),
            ],
            edges=[RFEdge(id="e1", source="n1", target="n2", data=EdgeData(condition="*"))],
        )
        workflow = translate_workflow(graph, agent_config)
        first_step = workflow.steps[0]
        assert "Be polite." in first_step.agent.instructions
