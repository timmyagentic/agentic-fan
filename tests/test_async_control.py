import unittest
from unittest.mock import patch

from macfan.demo import MemorySMC
from macfan.errors import MacFanError
from macfan.hardware import Hardware


class DelayedSMC(MemorySMC):
    """Writes are accepted now but become visible on a later firmware tick."""
    def __init__(self):
        super().__init__()
        self.tick = 0.0
        self.pending = []
        self.targets_before_manual = []

    def write(self, key, raw):
        if key.endswith('Tg') and self.read(key[:2] + 'Md') != b'\1':
            self.targets_before_manual.append(key)
        self.pending.append((self.tick + .25, key, raw))

    def advance(self, seconds):
        self.tick += seconds
        ready = [x for x in self.pending if x[0] <= self.tick + 1e-9]
        self.pending = [x for x in self.pending if x not in ready]
        for _, key, raw in ready:
            MemorySMC.write(self, key, raw)


class AsyncControlTests(unittest.TestCase):
    def test_waits_for_modes_before_targets_and_for_restore(self):
        smc = DelayedSMC()
        hw = Hardware(smc, simulated=True)
        with patch('time.monotonic', side_effect=lambda: smc.tick), \
                patch('time.sleep', side_effect=smc.advance):
            hw.validate_control([0, 1], 3000)
            hw.apply([0, 1], 3000)
            self.assertEqual(smc.targets_before_manual, [])
            self.assertEqual(hw.mode(0), 'manual')
            self.assertEqual(hw.number('F0Tg'), 3000)
            hw.restore([0, 1])
            self.assertEqual([hw.mode(i) for i in (0, 1)], ['auto', 'auto'])
            self.assertGreaterEqual(smc.tick, .75)

    def test_unapplied_write_times_out_with_observed_value(self):
        smc = DelayedSMC()
        hw = Hardware(smc, simulated=True)
        def only_clock(seconds): smc.tick += seconds
        with patch('time.monotonic', side_effect=lambda: smc.tick), \
                patch('time.sleep', side_effect=only_clock):
            with self.assertRaises(MacFanError) as error:
                hw.apply([0], 3000)
            self.assertEqual(error.exception.code, 'CONTROL_REJECTED')
            self.assertEqual(error.exception.details['observed'], 'auto')
            self.assertLessEqual(smc.tick, 3.1)
            self.assertEqual(smc.targets_before_manual, [])

    def test_bitmap_updates_do_not_erase_other_pending_fan(self):
        smc = DelayedSMC()
        from macfan.codecs import KeyInfo
        del smc.values['F0Md']; del smc.values['F1Md']
        smc.values['FS! '] = (KeyInfo('ui16', 2), b'\0\0')
        hw = Hardware(smc, simulated=True)
        # This model has a global bitmap; mode writes must be sequenced.
        smc.targets_before_manual = []
        def write(key, raw): smc.pending.append((smc.tick + .25, key, raw))
        smc.write = write
        with patch('time.monotonic', side_effect=lambda: smc.tick), \
                patch('time.sleep', side_effect=smc.advance):
            hw.apply([0, 1], 3000)
            self.assertEqual(hw.mode(0), 'manual')
            self.assertEqual(hw.mode(1), 'manual')
            hw.restore([0, 1])
            self.assertEqual(smc.read('FS! '), b'\0\0')
