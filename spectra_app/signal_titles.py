"""Conservative signal labels from explicit metadata or basename tokens only."""
from pathlib import Path
import re

RAW_LABEL = "ΔR/R₀ (arb. units)"
DERIVATIVE_LABEL = "d²(ΔR/R₀)/dE² (arb. units)"
PL_LABEL = "PL (arb. units)"
NEUTRAL_LABEL = "Signal (arb. units)"


def _source_kind(text: str) -> str:
    text = text.casefold().replace('₀', '0')
    text = re.sub(r'(?:d|∂)(?:²|2)\s*(?=pl\s*/\s*(?:d|∂)e(?:²|2))', '', text)
    if re.search(r'(?<![a-z0-9])(?:pl(?:\d+(?:\.\d+)?nm)?|photoluminescence)(?![a-z0-9])', text):
        return 'pl'
    if re.search(r'(?<![a-z0-9])(?:dr[_/]r|δr/r|deltar/r)(?:[_ ]?0)(?![a-z0-9])', text):
        return 'reflection_reference'
    if re.search(r'(?<![a-z0-9])(?:dr[_/]r|δr/r|deltar/r)(?![a-z0-9])', text):
        return 'reflection'
    return 'unknown'


def _declarations(metadata):
    for line in metadata:
        match = re.search(r'^\s*[#;!]*\s*(?:signal(?:[ _-]?(?:label|title))?|z(?:[ _-]?(?:label|title))?)\s*(?:[:=]\s*|\s+)(.+)', str(line), re.I)
        if match:
            yield match.group(1)


def infer_source_signal_kind(path: str, metadata=()) -> str:
    for declaration in _declarations(metadata):
        kind = _source_kind(declaration)
        if kind != 'unknown':
            return kind
    return _source_kind(Path(path).name)


def automatic_signal_label(source_kind: str, derivative: bool = False, source_derivative: bool = False) -> str:
    raw = {'pl': 'PL', 'reflection': 'ΔR/R₀', 'reflection_reference': 'ΔR/R₀'}.get(source_kind, 'Signal')
    if derivative or source_derivative:
        order = '⁴' if derivative and source_derivative else '²'
        raw = f'd{order}({raw})/dE{order}' if source_kind.startswith('reflection') else f'd{order}{raw}/dE{order}'
    return f'{raw} (arb. units)'


def display_signal_label(text: str, show_unit: bool = True) -> str:
    """Format only a recognized trailing unit, leaving stored literal text intact."""
    if show_unit:
        return re.sub(r'\s*\(a\.\s*u\.?\)\s*$', ' (arb. units)', text, flags=re.I)
    return re.sub(r'\s*\((?:a\.\s*u\.?|arb\.\s*units?|counts?|eV|meV)\)\s*$', '', text, flags=re.I)


def _kind(text: str) -> str:
    text = text.casefold().replace('²', '2').replace('∂', 'd')
    if re.search(r'(?<![a-z0-9])(?:d2[_ ]?e|second[ _-]+derivative)(?![a-z0-9])', text) or re.search(
            r'd2\s*(?:\(\s*(?:δr|deltar|dr)\s*/\s*r(?:0|₀)?\s*\)|pl|signal)\s*/\s*de2', text):
        return 'energy_second_derivative'
    return _source_kind(text)


def infer_signal_kind(path: str, metadata=()) -> str:
    # Explicit signal declarations override a filename; unrelated comments do not.
    for declaration in _declarations(metadata):
        kind = _kind(declaration)
        if kind != 'unknown':
            return kind
    return _kind(Path(path).name)
