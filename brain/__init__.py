"""The LangGraph editorial workflow.

Each station of the pipeline is a node, and the arrows between them — including
the ones that loop backwards — are declared in brain/graph.py rather than buried
in if/else code. The stations themselves are unchanged: the graph calls the same
modules in nodes/ with the same arguments.
"""
