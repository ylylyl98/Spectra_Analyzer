"""Display-only legend names; retained curve labels remain full provenance."""
from __future__ import annotations

from collections import defaultdict

from .workbook_state import _curve_sheet_context, _source_key, default_curve_label, sheet_columns


def _short_source(name):
    return name if len(name) <= 24 else name[:12] + '…' + name[-11:]


def comparison_legend_labels(workspace, curves):
    """Return (text, automatic) by curve ID for the curves actually being drawn.

    Only exact generated names are recognized. The unsuffixed form also matches
    defaults created before another file with the same basename was imported.
    """
    owners = {sheet_id: book for book in workspace.workbooks.values() for sheet_id in book.sheet_ids}
    sheets = {curve.sheet_id: workspace.sheets[curve.sheet_id] for curve in curves}
    columns = {identity: sheet_columns(workspace, sheet) for identity, sheet in sheets.items()}
    sources, source_names, source_sheets = {}, {}, defaultdict(set)
    contexts = {identity: _curve_sheet_context(workspace, sheet) for identity, sheet in sheets.items()}
    for identity, sheet in sheets.items():
        if sheet.source_path is not None:
            source = ('path', _source_key(sheet.source_path))
            name = sheet.source_path.stem
        else:
            book = owners.get(identity)
            source = ('book', book.workbook_id if book else identity)
            name = book.name if book else 'Workbook'
        sources[identity] = source
        source_names[source] = _short_source(name)
        source_sheets[source].add(identity)

    # Reserve literal names before generating qualifiers, including names that
    # already look qualified. Compare after shortening and without case.
    same_names = defaultdict(list)
    for source, name in source_names.items():
        same_names[name.casefold()].append(source)
    reserved_names = set(same_names)
    for key in sorted(same_names):
        group = same_names[key]
        if len(group) > 1:
            index = 1
            for source in sorted(group):
                name = source_names[source]
                qualified = f'{name} [{index}]'
                while qualified.casefold() in reserved_names:
                    index += 1
                    qualified = f'{name} [{index}]'
                source_names[source] = qualified
                reserved_names.add(qualified.casefold())
                index += 1
    for group in source_sheets.values():
        same_context = defaultdict(list)
        for identity in group:
            same_context[contexts[identity]].append(identity)
        for duplicates in same_context.values():
            if len(duplicates) > 1:
                for index, identity in enumerate(sorted(duplicates), 1):
                    contexts[identity] += f' [Sheet {index}]'

    y_columns, x_columns = defaultdict(set), defaultdict(set)
    for curve in curves:
        names = columns[curve.sheet_id]
        y_columns[curve.sheet_id, names[curve.y_column]].add(curve.y_column)
        x_columns[curve.sheet_id, curve.y_column].add(curve.x_column)

    result = {}
    different_contexts = len(set(contexts.values())) > 1
    for curve in curves:
        sheet = sheets[curve.sheet_id]
        automatic = curve.label in (
            default_curve_label(workspace, sheet, curve.y_column),
            default_curve_label(workspace, sheet, curve.y_column, disambiguate_source=False))
        label = curve.label
        if automatic:
            names = columns[curve.sheet_id]
            label = names[curve.y_column]
            if len(y_columns[curve.sheet_id, label]) > 1:
                label += f' [col {curve.y_column + 1}]'
            xs = x_columns[curve.sheet_id, curve.y_column]
            if len(xs) > 1:
                x_label = names[curve.x_column]
                if sum(names[index] == x_label for index in xs) > 1:
                    x_label += f' [col {curve.x_column + 1}]'
                label += f' vs {x_label}'
            source = sources[curve.sheet_id]
            if different_contexts or len(source_sheets[source]) > 1:
                label = f'{contexts[curve.sheet_id]}: {label}'
            if len(source_names) > 1:
                label = f'{source_names[source]}: {label}'
        result[curve.curve_id] = label, automatic
    return result
