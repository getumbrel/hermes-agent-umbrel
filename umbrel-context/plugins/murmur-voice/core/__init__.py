"""Local voices for Murmur: models picked for this box, downloaded on demand, run inside Hermes.

Loaded once per process under the name ``murmur_voice_core`` (see load() in ../__init__.py and
../dashboard/plugin_api.py), so Hermes's speech providers and the dashboard routes share one copy:
one download, one set of loaded models.
"""
