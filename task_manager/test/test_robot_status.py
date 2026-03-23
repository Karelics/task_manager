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

"""Unit tests for RobotStatus class."""

import time
import unittest
from datetime import datetime, timedelta

from task_manager.robot_status import RobotStatus
from task_manager_msgs.msg import ActiveTask


class TestRobotStatus(unittest.TestCase):
    """Test cases for RobotStatus class."""

    def test_initialization(self):
        """Test robot status initialization."""
        robot = RobotStatus(robot_id="uav1")
        self.assertEqual(robot.robot_id, "uav1")
        self.assertEqual(robot.active_tasks, [])
        self.assertTrue(robot.is_connected)
        self.assertEqual(robot.total_tasks_assigned, 0)

    def test_is_available_when_no_tasks(self):
        """Test that robot is available when connected with no active tasks."""
        robot = RobotStatus(robot_id="uav1")
        self.assertTrue(robot.is_available())

    def test_is_not_available_when_has_tasks(self):
        """Test that robot is not available when it has active tasks."""
        robot = RobotStatus(robot_id="uav1")

        # Add an active task
        task = ActiveTask()
        task.task_id = "task1"
        task.task_name = "test_task"
        task.task_status = "IN_PROGRESS"

        robot.update_active_tasks([task])
        self.assertFalse(robot.is_available())

    def test_is_not_available_when_disconnected(self):
        """Test that robot is not available when disconnected."""
        robot = RobotStatus(robot_id="uav1")
        robot.mark_disconnected()
        self.assertFalse(robot.is_available())

    def test_update_active_tasks(self):
        """Test updating active tasks list."""
        robot = RobotStatus(robot_id="uav1")

        task1 = ActiveTask()
        task1.task_id = "task1"

        task2 = ActiveTask()
        task2.task_id = "task2"

        old_time = robot.last_seen

        # Small delay to ensure timestamp changes
        time.sleep(0.01)

        robot.update_active_tasks([task1, task2])

        self.assertEqual(len(robot.active_tasks), 2)
        self.assertTrue(robot.is_connected)
        self.assertGreater(robot.last_seen, old_time)

    def test_mark_disconnected(self):
        """Test marking robot as disconnected."""
        robot = RobotStatus(robot_id="uav1")

        # Add tasks
        task = ActiveTask()
        task.task_id = "task1"
        robot.update_active_tasks([task])

        # Mark disconnected
        robot.mark_disconnected()

        self.assertFalse(robot.is_connected)
        self.assertEqual(len(robot.active_tasks), 0)
        self.assertFalse(robot.is_available())


if __name__ == "__main__":
    unittest.main()
