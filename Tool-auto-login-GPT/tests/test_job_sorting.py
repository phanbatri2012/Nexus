import unittest
from auto_yt.main import _sort_job_center_items


class JobSortingTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {
                "id": "job-1",
                "status": "done",
                "created_at": "2026-10-01T10:00:00Z",
                "started_at": "2026-10-01T10:05:00Z",
                "updated_at": "2026-10-01T10:30:00Z",
            },
            {
                "id": "job-2",
                "status": "running",
                "created_at": "2026-10-02T12:00:00Z",
                "started_at": "2026-10-02T12:01:00Z",
                "updated_at": "2026-10-02T12:10:00Z",
            },
            {
                "id": "job-3",
                "status": "queued",
                "created_at": "2026-10-03T08:00:00Z",
                "started_at": "",
                "updated_at": "2026-10-03T08:00:00Z",
            },
            {
                "id": "job-4",
                "status": "error",
                "created_at": "2026-10-04T09:00:00Z",
                "started_at": "2026-10-04T09:02:00Z",
                "updated_at": "2026-10-04T09:05:00Z",
            },
        ]

    def test_sort_by_start_time_desc(self):
        sorted_items = _sort_job_center_items(self.items, sort_by="start_desc")
        ids = [item["id"] for item in sorted_items]
        # job-4: 2026-10-04T09:02:00Z
        # job-3: 2026-10-03T08:00:00Z (started_at empty -> fallback created_at)
        # job-2: 2026-10-02T12:01:00Z
        # job-1: 2026-10-01T10:05:00Z
        self.assertEqual(ids, ["job-4", "job-3", "job-2", "job-1"])

    def test_sort_by_start_time_asc(self):
        sorted_items = _sort_job_center_items(self.items, sort_by="start_asc")
        ids = [item["id"] for item in sorted_items]
        self.assertEqual(ids, ["job-1", "job-2", "job-3", "job-4"])

    def test_sort_by_created_desc(self):
        sorted_items = _sort_job_center_items(self.items, sort_by="created_desc")
        ids = [item["id"] for item in sorted_items]
        self.assertEqual(ids, ["job-4", "job-3", "job-2", "job-1"])

    def test_sort_by_created_asc(self):
        sorted_items = _sort_job_center_items(self.items, sort_by="created_asc")
        ids = [item["id"] for item in sorted_items]
        self.assertEqual(ids, ["job-1", "job-2", "job-3", "job-4"])

    def test_sort_by_updated_desc(self):
        sorted_items = _sort_job_center_items(self.items, sort_by="updated_desc")
        ids = [item["id"] for item in sorted_items]
        self.assertEqual(ids, ["job-4", "job-3", "job-2", "job-1"])

    def test_sort_by_active_first(self):
        sorted_items = _sort_job_center_items(self.items, sort_by="active_first")
        ids = [item["id"] for item in sorted_items]
        # Active: job-3 (Oct 3), job-2 (Oct 2) -> job-3, job-2
        # Inactive: job-4 (Oct 4), job-1 (Oct 1) -> job-4, job-1
        self.assertEqual(ids, ["job-3", "job-2", "job-4", "job-1"])


if __name__ == "__main__":
    unittest.main()
