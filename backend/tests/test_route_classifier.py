"""Quick unit tests for route_classifier.py"""
from graph.route_classifier import classify_query, RouteTier, L1_READONLY_TOOLS


def test_l0():
    assert classify_query("hello") == RouteTier.L0
    assert classify_query("thanks") == RouteTier.L0
    assert classify_query("goodbye") == RouteTier.L0
    assert classify_query("") == RouteTier.L0
    assert classify_query("a") == RouteTier.L0


def test_l2_code_execution():
    assert classify_query("run the code now") == RouteTier.L2
    assert classify_query("write code in python") == RouteTier.L2
    assert classify_query("use bash terminal") == RouteTier.L2
    assert classify_query("execute this script") == RouteTier.L2


def test_l2_file_ops():
    assert classify_query("write file to disk") == RouteTier.L2
    assert classify_query("modify the config") == RouteTier.L2
    assert classify_query("delete old files") == RouteTier.L2
    assert classify_query("edit the source") == RouteTier.L2
    assert classify_query("save this document") == RouteTier.L2


def test_l2_wiki_analysis():
    assert classify_query("register source in wiki") == RouteTier.L2
    assert classify_query("parse pdf paper") == RouteTier.L2
    assert classify_query("analyze all data") == RouteTier.L2
    assert classify_query("compare both approaches") == RouteTier.L2
    assert classify_query("implement transformer from scratch") == RouteTier.L2
    assert classify_query("generate a report") == RouteTier.L2


def test_l1_knowledge():
    assert classify_query("what is attention mechanism") == RouteTier.L1
    assert classify_query("explain how BERT works?") == RouteTier.L1
    assert classify_query("what is the difference?") == RouteTier.L1


def test_long_message_goes_l2():
    assert classify_query("x" * 201) == RouteTier.L2


def test_tool_whitelist():
    assert "ReadFile" in L1_READONLY_TOOLS
    assert "FetchURL" in L1_READONLY_TOOLS
    assert "search_memory_v3" in L1_READONLY_TOOLS
    assert "Terminal" not in L1_READONLY_TOOLS
    assert "PythonRepl" not in L1_READONLY_TOOLS


if __name__ == "__main__":
    test_l0()
    test_l2_code_execution()
    test_l2_file_ops()
    test_l2_wiki_analysis()
    test_l1_knowledge()
    test_long_message_goes_l2()
    test_tool_whitelist()
    print("All route classifier tests passed!")
