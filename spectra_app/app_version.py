"""Version and public release feed shared by the app and Windows packaging."""

APP_VERSION = '0.1.5'

# Public release channel. No account credentials are stored by the updater.
UPDATE_REPOSITORY = 'ylylyl98/Spectra_Analyzer'


def installer_asset_name(version: str) -> str:
    return f'SpectraAnalyzer-{version}-Setup.exe'
