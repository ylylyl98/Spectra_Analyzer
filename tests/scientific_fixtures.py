"""Scientific data factories without application creation or test imports."""
import numpy as np

from spectra_app.dat_io import DataCube
from spectra_app.fitting import FitResult


def workflow_cube(z: np.ndarray, gates: np.ndarray | None = None) -> DataCube:
    """The workflow fixture's original 0–20 energy grid and metadata."""
    z = np.asarray(z, dtype=float)
    energy = np.linspace(0.0, 20.0, z.shape[1])
    gate = np.asarray(gates if gates is not None else np.arange(z.shape[0]), dtype=float)
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title="workflow",
        source_path="",
        x_original=energy.copy(),
        x_original_type="pixel",
        x_original_label="X",
        x_detection_source="test",
        x_detection_note="",
        x_is_ambiguous=False,
        x_override=None,
        photon_energy=None,
        photon_energy_note="",
        energy_label="X",
    )


def workflow_fit_result(center: float) -> FitResult:
    """The workflow fixture's original single Lorentzian result."""
    x = np.linspace(0.0, 20.0, 21)
    y = np.zeros_like(x)
    return FitResult(
        model="Lorentzian",
        x=x,
        y_fit=y,
        components=np.zeros((1, x.size)),
        centers=np.asarray([center]),
        amplitudes=np.asarray([1.0]),
        widths=np.asarray([0.5]),
        baseline=0.0,
        rmse=0.0,
        r_squared=1.0,
    )
