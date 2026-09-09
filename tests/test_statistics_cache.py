from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import unittest
import time

from mower.statistics_cache import _STATISTICS_CACHE, get_dashboard_statistics

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)


class StatisticsCacheTests(unittest.TestCase):
    def setUp(self): _STATISTICS_CACHE.clear()
    def test_parallel_first_readers_query_once(self):
        calls = []
        def loader(now, env):
            calls.append(env["SSV53_APP_INSIGHTS_APP_ID"]); time.sleep(.02)
            return {"available": True, "_chargingEvidence": {"x": 1}}
        env={"SSV53_APP_INSIGHTS_APP_ID":"a"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: get_dashboard_statistics(env,NOW,loader=loader), range(8)))
        self.assertEqual(len(calls), 1); self.assertEqual(results[0], results[-1])
    def test_accounts_are_isolated(self):
        calls=[]
        def loader(now, env): calls.append(env["SSV53_APP_INSIGHTS_APP_ID"]); return {"available":True}
        get_dashboard_statistics({"SSV53_APP_INSIGHTS_APP_ID":"a"},NOW,loader=loader)
        get_dashboard_statistics({"SSV53_APP_INSIGHTS_APP_ID":"b"},NOW,loader=loader)
        self.assertEqual(calls,["a","b"])
    def test_result_is_deep_copied(self):
        env={"SSV53_APP_INSIGHTS_APP_ID":"a"}
        def loader(now, env): return {"available":True,"_chargingEvidence":{"rows":[1]}}
        value=get_dashboard_statistics(env,NOW,loader=loader); value["_chargingEvidence"]["rows"].append(2)
        self.assertEqual(get_dashboard_statistics(env,NOW,loader=loader)["_chargingEvidence"]["rows"],[1])
    def test_expiry_and_time_regression_do_not_use_fresh_cache(self):
        calls=[]
        def loader(now, env): calls.append(now); return {"available":True,"n":len(calls)}
        env={"SSV53_APP_INSIGHTS_APP_ID":"a"}
        get_dashboard_statistics(env,NOW,loader=loader)
        get_dashboard_statistics(env,NOW-timedelta(minutes=1),loader=loader)
        get_dashboard_statistics(env,NOW+timedelta(minutes=6),loader=loader)
        self.assertEqual(len(calls),3)
    def test_failure_is_short_cached_and_not_old_data(self):
        calls=[]
        def loader(now, env): calls.append(1); raise RuntimeError("offline")
        env={"SSV53_APP_INSIGHTS_APP_ID":"a"}
        first=get_dashboard_statistics(env,NOW,loader=loader); second=get_dashboard_statistics(env,NOW+timedelta(seconds=5),loader=loader)
        self.assertFalse(first["available"]); self.assertFalse(second["available"]); self.assertEqual(len(calls),1)

if __name__ == "__main__": unittest.main()
