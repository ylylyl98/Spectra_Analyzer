"""Transient paste ownership; scientific arrays remain borrowed by detached candidates."""
from copy import copy, deepcopy
from dataclasses import dataclass, field, replace

PASTE_OPTIONS_KEY = 'plot_formats/paste_options'


def graph_candidate(session, graph_id):
    # activate_graph synchronizes analysis compatibility fields. Detach first,
    # otherwise preparing a candidate can overwrite the live target's view.
    owner = copy(session)
    owner.graphs = dict(session.graphs)
    graph = copy(session.graphs[graph_id])
    graph.view = deepcopy(graph.view)
    graph.presentation = deepcopy(graph.presentation)
    graph.gate_indices = deepcopy(graph.gate_indices)
    owner.graphs[graph_id] = graph
    owner.activate_graph(graph_id)
    return owner


def independently_formatted(graph):
    return graph.analysis is None or graph.analysis.source_graph_id is None


@dataclass
class PasteOperation:
    source: object
    baselines: dict
    options: object
    pairs: dict
    histories: dict
    states: dict = field(default_factory=dict)
    expected_histories: dict = field(default_factory=dict)


def effective_paste_options(template, options):
    """Unavailable source fields mask application, never the remembered intent."""
    updates = {name: False for name in ('x_limits', 'y_limits', 'title_position')
               if getattr(template, name) is None}
    if not template.annotation_layout:
        updates['annotation_positions'] = False
    return replace(options, **updates)
