"""Optional process-owner metrics provider; never part of state identity."""
_provider=None
def configure(provider):
    global _provider
    _provider=provider
def snapshot():return _provider()if _provider else {'job_metrics_available':False}
