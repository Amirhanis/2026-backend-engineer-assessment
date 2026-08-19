import unittest
import time
from fake_erp import FakeErp, Record
from sync_adapter import LocalStore, LocalRecord, pull, push, idempotency_key

class TestSyncAdapter(unittest.TestCase):

    def test_defect1_pagination_skips_tied_records_maia_812(self):
        # Setup ERP with records that share the same timestamp
        erp = FakeErp(seed=1, timeout_rate=0.0)
        # Manually create records with the exact same timestamp
        ts = "2026-08-01 12:00:00"
        erp.records["EXT-1"] = Record("EXT-1", {"v": 1}, 1, ts)
        erp.records["EXT-2"] = Record("EXT-2", {"v": 1}, 1, ts)
        erp.records["EXT-3"] = Record("EXT-3", {"v": 1}, 1, ts)
        
        # We need to make sure we don't skip tied records across pages.
        # So we pull with page_size=1
        store = LocalStore()
        
        # Pull first page (1 record)
        pulled = pull(erp, store, page_size=1)
        self.assertEqual(len(store.records), 3, "Should have pulled all records eventually")
        self.assertIn("EXT-1", store.records)
        self.assertIn("EXT-2", store.records)
        self.assertIn("EXT-3", store.records)

    def test_defect2_non_deterministic_idempotency_key_maia_830(self):
        # Generate two keys at different times/attempts for the same payload
        payload = {"name": "item", "price": 100}
        key1 = idempotency_key("EXT-001", payload)
        
        time.sleep(0.01) # Ensure time passes if time.time() was used
        key2 = idempotency_key("EXT-001", payload)
        
        self.assertEqual(key1, key2, "Idempotency keys should be deterministic")

    def test_defect3_blind_overwrite_on_conflict_maia_844(self):
        erp = FakeErp(seed=2, timeout_rate=0.0)
        erp.records["EXT-001"] = Record("EXT-001", {"price": 10}, 1, "2026-08-01 12:00:00")
        
        store = LocalStore()
        # Local record based on version 1, but we made an edit
        store.upsert(LocalRecord("EXT-001", {"price": 99}, 1, "2026-08-01 12:00:00", dirty=True))
        
        # Meanwhile, remote was updated to version 2
        erp.write("EXT-001", {"price": 20}, base_version=1)
        
        # Push should hit conflict and accept remote version
        push(erp, store)
        
        # Local should now have remote's payload
        self.assertEqual(store.records["EXT-001"].payload["price"], 20)
        self.assertFalse(store.records["EXT-001"].dirty)

    def test_defect4_timezone_mismatch_in_pull_comparison_maia_844(self):
        erp = FakeErp(seed=3, timeout_rate=0.0)
        # Remote record has an artificially newer timestamp due to +08:00
        erp.records["EXT-001"] = Record("EXT-001", {"price": 10}, 1, "2026-08-01 20:00:00")
        
        store = LocalStore()
        # Local edit made based on version 1. UTC timestamp is artificially "older"
        store.upsert(LocalRecord("EXT-001", {"price": 99}, 1, "2026-08-01 12:00:00", dirty=True))
        
        # Pull should keep local edit because versions match (remote hasn't moved forward)
        pull(erp, store)
        
        self.assertTrue(store.records["EXT-001"].dirty)
        self.assertEqual(store.records["EXT-001"].payload["price"], 99)

    def test_defect5_cursor_advanced_before_page_processing(self):
        erp = FakeErp(seed=4, timeout_rate=0.0)
        erp.records["EXT-001"] = Record("EXT-001", {"price": 10}, 1, "2026-08-01 12:00:00")
        
        store = LocalStore()
        
        # To simulate a crash, we can monkeypatch store.upsert to raise an exception
        original_upsert = store.upsert
        def failing_upsert(rec):
            raise RuntimeError("Crash during processing")
        store.upsert = failing_upsert
        
        with self.assertRaises(RuntimeError):
            pull(erp, store)
            
        # The cursor should NOT have been advanced
        self.assertIsNone(store.cursor)

    def test_defect6_erptimeout_on_push_not_handled_as_potential_success(self):
        # We guarantee a timeout on the first write
        erp = FakeErp(seed=5, timeout_rate=1.0)
        erp.records["EXT-001"] = Record("EXT-001", {"price": 10}, 1, "2026-08-01 12:00:00")
        
        store = LocalStore()
        store.upsert(LocalRecord("EXT-001", {"price": 99}, 1, "2026-08-01 12:00:00", dirty=True))
        
        # Push should succeed despite the timeout because it checks if write landed
        pushed = push(erp, store)
        self.assertEqual(pushed, 1)
        self.assertFalse(store.records["EXT-001"].dirty)
        self.assertEqual(store.records["EXT-001"].remote_version, 2)

if __name__ == '__main__':
    unittest.main()
