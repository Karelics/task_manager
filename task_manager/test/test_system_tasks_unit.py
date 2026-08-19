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
from task_manager.tasks.parallel_task_executor import ParallelTaskExecutor
from task_manager.tasks.system_tasks import (
    _find_enclosing_composite,
    _resolve_down,
    _resolve_target_task_ids,
    _sync_composite_statuses,
)

# pylint: disable=protected-access

MISSION = "system/mission"
PARALLEL = "system/perform_in_parallel"


# pylint: disable=duplicate-code
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


# pylint: enable=duplicate-code


def make_composite_client(task_id: str, task_name: str, goal_id_byte: int, status: str = TaskStatus.IN_PROGRESS):
    """Builds a Mock ActionTaskClient standing in for a composite task's (Mission/ParallelTaskExecutor) own task_client,
    with a fake goal_id."""
    client = make_task_client(task_id, task_name=task_name, status=status, spec=ActionTaskClient)
    client.goal_id = Mock(uuid=[goal_id_byte] * 16)
    return client


class ResolveDownTests(unittest.TestCase):
    """Unit tests for system_tasks._resolve_down."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_leaf_resolves_to_itself(self):
        """A plain task that isn't a composite resolves to itself."""
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.assertEqual(_resolve_down("leaf1", self.active_tasks, self.composites), ["leaf1"])

    def test_mission_resolves_to_its_active_child(self):
        """A Mission resolves down to whichever of its subtasks is currently active."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        self.assertEqual(_resolve_down("m1", self.active_tasks, self.composites), ["leaf1"])

    def test_parallel_resolves_to_all_its_active_children(self):
        """A ParallelTaskExecutor resolves down to all of its currently active members."""
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.active_tasks.add(make_task_client("leaf2", "some_task"))
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

        self.assertEqual(_resolve_down("p1", self.active_tasks, self.composites), ["leaf1", "leaf2"])

    def test_recurses_through_nested_composites_of_mixed_types(self):
        """A Mission's active child can itself be a parallel task (and vice versa) - resolution must follow the
        whole chain down to the real leaves, not stop after one hop."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=2))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.active_tasks.add(make_task_client("leaf2", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["p1"]}.get(goal_id, [])
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([2] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

        self.assertEqual(_resolve_down("m1", self.active_tasks, self.composites), ["leaf1", "leaf2"])

    def test_composite_with_no_active_children_resolves_to_itself(self):
        """If the composite hasn't recorded any active children yet (e.g. paused right as it starts), resolution falls
        back to the composite's own task_id."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.mission.get_active_children.return_value = []

        self.assertEqual(_resolve_down("m1", self.active_tasks, self.composites), ["m1"])

    def test_unknown_task_id_raises_key_error(self):
        """If the task_id isn't in ActiveTasks, resolution fails with KeyError."""
        self.assertRaises(KeyError, _resolve_down, "unknown", self.active_tasks, self.composites)


class FindEnclosingCompositeTests(unittest.TestCase):
    """Unit tests for system_tasks._find_enclosing_composite."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_finds_owning_mission(self):
        """A task resolves to its owning Mission."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        self.assertEqual(_find_enclosing_composite("leaf1", self.active_tasks, self.composites), "m1")

    def test_finds_owning_parallel_task_for_any_of_its_members(self):
        """Any member of a ParallelTaskExecutor resolves to that ParallelTaskExecutor."""
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.active_tasks.add(make_task_client("leaf2", "some_task"))
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

        self.assertEqual(_find_enclosing_composite("leaf1", self.active_tasks, self.composites), "p1")
        self.assertEqual(_find_enclosing_composite("leaf2", self.active_tasks, self.composites), "p1")

    def test_returns_none_for_untracked_task(self):
        """A task that isn't a member of any composite returns None."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.return_value = []

        self.assertIsNone(_find_enclosing_composite("leaf1", self.active_tasks, self.composites))


class ResolveTargetTaskIdsTests(unittest.TestCase):
    """Unit tests for system_tasks._resolve_target_task_ids - the combination of _find_enclosing_composite and
    _resolve_down that PauseTasksService/ResumeTasksService actually use."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {PARALLEL: self.parallel}

        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.active_tasks.add(make_task_client("leaf2", "some_task"))
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

    def test_targeting_the_group_owner_directly_expands_to_all_members(self):
        """Pausing/resuming the ParallelTaskExecutor itself must affect every member, not just the composite's own
        task_id."""
        self.assertEqual(_resolve_target_task_ids("p1", self.active_tasks, self.composites), ["leaf1", "leaf2"])

    def test_targeting_one_member_expands_to_the_whole_group(self):
        """Pausing/resuming one member of a parallel group must affect every member, not just that one."""
        self.assertEqual(_resolve_target_task_ids("leaf1", self.active_tasks, self.composites), ["leaf1", "leaf2"])
        self.assertEqual(_resolve_target_task_ids("leaf2", self.active_tasks, self.composites), ["leaf1", "leaf2"])

    def test_plain_unrelated_leaf_resolves_to_itself(self):
        """A task that isn't part of any composite resolves to itself."""
        self.active_tasks.add(make_task_client("other", "some_task"))
        self.assertEqual(_resolve_target_task_ids("other", self.active_tasks, self.composites), ["other"])


class SyncCompositeStatusesTests(unittest.TestCase):
    """Unit tests for system_tasks._sync_composite_statuses."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_marks_composite_paused_when_all_active_children_are_paused(self):
        """If all of a composite's active children are PAUSED, the composite itself must be marked PAUSED too."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.PAUSED))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.PAUSED)

    def test_leaves_composite_in_progress_when_a_child_is_still_running(self):
        """If any of a composite's active children are still running, the composite itself must be marked
        IN_PROGRESS."""
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.PAUSED))
        self.active_tasks.add(make_task_client("leaf2", "some_task", status=TaskStatus.IN_PROGRESS))
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("p1").task_details.status, TaskStatus.IN_PROGRESS)

    def test_flips_composite_back_to_in_progress_once_no_child_is_paused_anymore(self):
        """If none of a composite's active children are PAUSED anymore, the composite itself must be marked
        IN_PROGRESS."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1, status=TaskStatus.PAUSED))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.IN_PROGRESS))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.IN_PROGRESS)

    def test_settles_a_nested_chain_in_a_single_call(self):
        """A Mission whose active child is a parallel task, whose members are all paused, must end up PAUSED.

        itself too - in one call, regardless of internal scan order (multi-pass fixpoint).
        """
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=2))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.PAUSED))
        self.active_tasks.add(make_task_client("leaf2", "some_task", status=TaskStatus.PAUSED))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["p1"]}.get(goal_id, [])
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([2] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("p1").task_details.status, TaskStatus.PAUSED)
        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.PAUSED)

    def test_leaves_unrelated_composites_untouched(self):
        """Composites that have no active children should remain unaffected."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.IN_PROGRESS))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.IN_PROGRESS)


if __name__ == "__main__":
    unittest.main()
