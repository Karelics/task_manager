#!/usr/bin/env python3

#  ------------------------------------------------------------------
#   Copyright 2024 Karelics Oy
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#  ------------------------------------------------------------------

"""Unit tests for TaskAssignment class."""

import unittest
from datetime import datetime

from task_manager.task_assignment import TaskAssignment


class TestTaskAssignment(unittest.TestCase):
    """Test cases for TaskAssignment class."""

    def test_initialization(self):
        """Test task assignment initialization."""
        now = datetime.now()
        assignment = TaskAssignment(
            task_id="task1",
            task_name="test_task",
            robot_id="uav1",
            assigned_at=now,
            status="PENDING",
            task_data='{"test": "data"}',
            source="Test",
        )

        self.assertEqual(assignment.task_id, "task1")
        self.assertEqual(assignment.task_name, "test_task")
        self.assertEqual(assignment.robot_id, "uav1")
        self.assertEqual(assignment.assigned_at, now)
        self.assertEqual(assignment.status, "PENDING")
        self.assertEqual(assignment.task_data, '{"test": "data"}')
        self.assertEqual(assignment.source, "Test")

    def test_datetime_conversion(self):
        """Test that ISO format strings are converted to datetime objects."""
        iso_string = "2024-03-17T12:00:00"
        assignment = TaskAssignment(
            task_id="task1",
            task_name="test_task",
            robot_id="uav1",
            assigned_at=iso_string,
            status="PENDING",
            task_data='{}',
            source="Test",
        )

        self.assertIsInstance(assignment.assigned_at, datetime)
        self.assertEqual(assignment.assigned_at, datetime.fromisoformat(iso_string))


if __name__ == "__main__":
    unittest.main()
