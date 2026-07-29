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

import unittest
from unittest.mock import Mock

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import ActionTaskClient, TaskClient
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskSpecs
from task_manager.tasks.mission import Mission
from task_manager.tasks.system_tasks import _mirror_owning_mission_status, _resolve_leaf_task_id

# pylint: disable=protected-access


def make_task_client(task_id: str, task_name: str, status: str = TaskStatus.IN_PROGRESS, spec=TaskClient):
    """Builds a Mock TaskClient with real TaskSpecs/TaskDetails, following the pattern used in test_active_tasks.py."""
    client = Mock(spec=spec)
    client.task_specs = TaskSpecs(
        task_name=task_name,
        topic=Mock(),
        msg_interface=Mock(),
        task_server_type=Mock(),
        blocking=False,
        cancel_on_stop=False,
    )
    client.task_details = TaskDetails(task_id=task_id, source="TEST", status=status)
    return client


def make_mission_client(task_id: str, goal_id_byte: int):
    """Builds a Mock ActionTaskClient standing in for a Mission's own task_client, with a fake goal_id."""
    client = make_task_client(task_id, task_name="system/mission", spec=ActionTaskClient)
    client.goal_id = Mock(uuid=[goal_id_byte] * 16)
    return client


class ResolveLeafTaskIdTests(unittest.TestCase):
    """Unit tests for system_tasks._resolve_leaf_task_id."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)

    def test_non_mission_task_resolves_to_itself(self):
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.assertEqual(_resolve_leaf_task_id("leaf1", self.active_tasks, self.mission), "leaf1")

    def test_mission_redirects_to_its_current_subtask(self):
        self.active_tasks.add(make_mission_client("m1", goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_current_subtask_id.side_effect = lambda goal_id: {bytes([1] * 16): "leaf1"}.get(goal_id)

        self.assertEqual(_resolve_leaf_task_id("m1", self.active_tasks, self.mission), "leaf1")

    def test_recurses_through_nested_missions(self):
        """A mission's current subtask can itself be a mission - resolution must follow the whole chain down to
        the real leaf task, not stop after one hop."""
        self.active_tasks.add(make_mission_client("m1", goal_id_byte=1))
        self.active_tasks.add(make_mission_client("m2", goal_id_byte=2))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        subtask_by_goal_id = {bytes([1] * 16): "m2", bytes([2] * 16): "leaf1"}
        self.mission.get_current_subtask_id.side_effect = lambda goal_id: subtask_by_goal_id.get(goal_id)

        self.assertEqual(_resolve_leaf_task_id("m1", self.active_tasks, self.mission), "leaf1")

    def test_mission_with_no_tracked_subtask_resolves_to_itself(self):
        """If the mission hasn't recorded its first subtask yet (e.g. paused right as it starts), resolution falls back
        to the mission's own task_id."""
        self.active_tasks.add(make_mission_client("m1", goal_id_byte=1))
        self.mission.get_current_subtask_id.return_value = None

        self.assertEqual(_resolve_leaf_task_id("m1", self.active_tasks, self.mission), "m1")

    def test_unknown_task_id_raises_key_error(self):
        self.assertRaises(KeyError, _resolve_leaf_task_id, "unknown", self.active_tasks, self.mission)


class MirrorOwningMissionStatusTests(unittest.TestCase):
    """Unit tests for system_tasks._mirror_owning_mission_status."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)

    def test_mirrors_status_onto_direct_and_nested_owning_missions(self):
        """Pausing/resuming a leaf task must be reflected on every mission (any nesting depth) currently tracking it as
        their active subtask, regardless of whether the request targeted the leaf or an owning mission."""
        self.active_tasks.add(make_mission_client("m1", goal_id_byte=1))
        self.active_tasks.add(make_mission_client("m2", goal_id_byte=2))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        subtask_by_goal_id = {bytes([1] * 16): "m2", bytes([2] * 16): "leaf1"}
        self.mission.get_current_subtask_id.side_effect = lambda goal_id: subtask_by_goal_id.get(goal_id)

        _mirror_owning_mission_status("leaf1", TaskStatus.PAUSED, self.active_tasks, self.mission)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.PAUSED)
        self.assertEqual(self.active_tasks.get_task_client("m2").task_details.status, TaskStatus.PAUSED)

    def test_leaves_unrelated_missions_untouched(self):
        self.active_tasks.add(make_mission_client("m1", goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.active_tasks.add(make_task_client("other_leaf", "some_task"))
        subtask_by_goal_id = {bytes([1] * 16): "other_leaf"}
        self.mission.get_current_subtask_id.side_effect = lambda goal_id: subtask_by_goal_id.get(goal_id)

        _mirror_owning_mission_status("leaf1", TaskStatus.PAUSED, self.active_tasks, self.mission)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.IN_PROGRESS)


if __name__ == "__main__":
    unittest.main()
