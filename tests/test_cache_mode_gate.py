import unittest
from dataclasses import replace
from unittest.mock import Mock

from mower.runtime import RuntimeSettings, ControlMode
from mower.full_failsafe import run_full_failsafe_cycle
from mower.full_mower import run_full_mower_cycle
from mower.park_only import run_park_only_cycle
from tests.test_full_failsafe import NOW, ENV


class CacheReleaseGateTests(unittest.TestCase):
    def test_cache_only_configures_command_free_modes(self):
        for mode in ('DRY_RUN', 'OFF'):
            settings = RuntimeSettings.from_mapping({'CONTROL_MODE': mode, 'HYDRAWISE_STATUS_CACHE_MODE': 'AZURE_TABLE'})
            self.assertFalse(settings.control_mode.allows_park)
        for mode in ('PARK_ONLY', 'FULL_MOWER', 'FULL_FAILSAFE'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'befehlsfreie'):
                RuntimeSettings.from_mapping({'CONTROL_MODE': mode, 'HYDRAWISE_STATUS_CACHE_MODE': 'AZURE_TABLE'})

    def test_direct_device_runner_also_blocks_preconstructed_settings(self):
        for mode, runner in ((ControlMode.PARK_ONLY, run_park_only_cycle),
                             (ControlMode.FULL_MOWER, run_full_mower_cycle),
                             (ControlMode.FULL_FAILSAFE, run_full_failsafe_cycle)):
            settings = replace(RuntimeSettings.from_mapping(ENV), control_mode=mode, enable_live_reads=True)
            reader = Mock(side_effect=AssertionError('No source access expected'))
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, 'Statuscache'):
                runner(now_utc=NOW, settings=settings, environment={**ENV, 'HYDRAWISE_STATUS_CACHE_MODE': 'AZURE_TABLE'},
                       past_due=False, source='test', read_only_runner=reader)
            reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
