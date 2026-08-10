# --- The "brain": the LangGraph editorial workflow ---
#
# WHAT THIS PACKAGE IS
#   The publish loop used to be one long hand-written function
#   (nodes/publish_loop.py process_item). This package rewrites it as an
#   explicit graph: each station of the pipeline is a node, and the arrows
#   between them — including the ones that loop backwards — are declared in
#   one place (brain/graph.py) instead of being buried in if/else code.
#
# WHAT IT DOES NOT CHANGE
#   The stations themselves. The graph calls the same nodes/sorter.py,
#   nodes/writer.py, nodes/editor.py and friends, with the same arguments,
#   in the same order. All the tuning in those files carries over untouched.
