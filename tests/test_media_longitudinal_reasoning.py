from agent_reach.toolbox.video import build_media_knowledge_graph

def test_knowledge_graph_preserves_cross_time_citations():
    reasoning={"claim_graph":{"nodes":[
        {"id":"A","statement":"premise","citations":["media:0-10"]},
        {"id":"B","statement":"conclusion","citations":["media:120-130"]}],
        "edges":[{"source":"A","target":"B","relation":"SUPPORTS",
                  "citations":["media:0-10","media:120-130"]}]}}
    graph=build_media_knowledge_graph(reasoning)
    assert graph["node_count"] == 2
    assert graph["edge_count"] == 1
    assert graph["edges"][0]["citations"] == ["media:0-10","media:120-130"]

def test_graph_does_not_default_to_verified():
    reasoning={"claim_graph":{"nodes":[{"id":"A","statement":"x","citations":["media:0-10"]}],"edges":[]}}
    graph=build_media_knowledge_graph(reasoning)
    assert graph["nodes"][0]["epistemic_status"] == "SOURCE_OR_INFERENCE"
