"""Conservative signal labels from explicit metadata or basename tokens only."""
from pathlib import Path
import re

RAW_LABEL = "ΔR/R (a. u.)"
DERIVATIVE_LABEL = "d²(ΔR/R)/dE² (a. u.)"


def _kind(text: str) -> str:
    text = text.casefold().replace('²', '2')
    if re.search(r'(?<![a-z0-9])(?:d2[_ ]?e|second[ _-]+derivative)(?![a-z0-9])', text) or re.search(
            r'd2\s*\(\s*(?:δr|deltar|dr)\s*/\s*r\s*\)\s*/\s*de2', text):
        return 'energy_second_derivative'
    if re.search(r'(?<![a-z0-9])(?:dr[_/]r|δr/r|deltar/r)(?![a-z0-9])', text):
        return 'reflection'
    return 'unknown'


def infer_signal_kind(path: str, metadata=()) -> str:
    # Explicit signal declarations override a filename; unrelated comments do not.
    for line in metadata:
        match = re.search(r'^\s*[#;!]*\s*(?:signal(?:[ _-]?(?:label|title))?|z(?:[ _-]?(?:label|title))?)\s*[:=]\s*(.+)', str(line), re.I)
        if match:
            kind = _kind(match.group(1))
            if kind != 'unknown':
                return kind
    return _kind(Path(path).name)
