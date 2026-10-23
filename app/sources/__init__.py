"""Source Graph: the decision map of who may fill the 92 canonical columns.

``reference/source_graph.json`` is the document (nodes, edges, 92 fields and the
three screen-only fields). :mod:`app.sources.graph` loads, validates and
resolves it; :mod:`app.sources.schema` publishes its JSON Schema;
:mod:`app.sources.api` serves all three read-only.

The graph does not replace the provider ladder and it stores no market value.
"""
