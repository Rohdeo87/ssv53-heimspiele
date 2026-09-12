"""Bounded display/learning patch over the verified automatic-takeover release."""
from scripts import build_irrigation_reliability_release as release

release.BASE_SHA256 = "4a0fd69a343429be1f60f7f82cb484d828f508b721d64ad94c81ee19f1fdd266"
release.PATCHED = frozenset({"function_app.py", "daily_safety_report.py", "platzwart_console.py",
                             "mower/charging_forecast.py", "mower/charging_forecast_store.py"})
release.ADDED = frozenset({"mower/charging_forecast.py", "mower/charging_forecast_store.py"})

if __name__ == "__main__":
    release.main()
