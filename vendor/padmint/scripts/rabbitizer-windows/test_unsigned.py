"""Hand-authored API checks; no game input or generated game code is needed."""
import unittest
import rabbitizer


class UnsignedBindingTests(unittest.TestCase):
    def test_tracked_addresses_keep_all_32_bits(self):
        for address in (0, 1, 0x7fffffff, 0x80000000, 0x80347578, 0xffffffff):
            with self.subTest(address=hex(address)):
                tracker = rabbitizer.RegistersTracker()
                # addiu t0, t0, 0; establish the already-decoded full address.
                tracker.processLo(rabbitizer.Instruction(0x25080000), address, 4)
                # lbu v0, 0(t0); ask for the address whose type this access infers.
                actual = tracker.getAddressIfCanSetType(rabbitizer.Instruction(0x91020000), 8)
                self.assertEqual(actual, address)

    def test_half_float_bit_patterns_are_unsigned(self):
        for half, full in ((0, 0), (0x8000, 0x80000000),
                           (0xbc00, 0xbf800000), (0xfc00, 0xff800000)):
            with self.subTest(half=hex(half)):
                self.assertEqual(rabbitizer.Utils.floatRepr_32From16(half), full)

    def test_unset_register_still_has_no_address(self):
        tracker = rabbitizer.RegistersTracker()
        self.assertIsNone(tracker.getAddressIfCanSetType(rabbitizer.Instruction(0x91020000), 8))

    def test_signed_immediate_and_jump_metadata_are_unchanged(self):
        self.assertEqual(rabbitizer.Instruction(0x2508ffff).getProcessedImmediate(), -1)
        tracker = rabbitizer.RegistersTracker()
        tracker.processLo(rabbitizer.Instruction(0x8d080000), 0xffffffff, 4)
        self.assertEqual(tracker.getJrInfo(rabbitizer.Instruction(0x01000008)), (4, 0xffffffff))


if __name__ == '__main__':
    unittest.main()
