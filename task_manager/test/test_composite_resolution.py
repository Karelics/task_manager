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
from task_manager.tasks.composite_resolution import (
    _find_enclosing_composite,
    _resolve_start_id,
    _resolve_target_task_ids,
    _sync_composite_statuses,
    pause_or_resume_group,
    resolve_down,
)
from task_manager.tasks.mission import Mission
from task_manager.tasks.parallel_task_executor import ParallelTaskExecutor

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
    """Unit tests for composite_resolution.resolve_down."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_leaf_resolves_to_itself(self):
        """A plain task that isn't a composite resolves to itself."""
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.assertEqual(resolve_down("leaf1", self.active_tasks, self.composites), ["leaf1"])

    def test_mission_resolves_to_its_active_child(self):
        """A Mission resolves down to whichever of its subtasks is currently active."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        self.assertEqual(resolve_down("m1", self.active_tasks, self.composites), ["leaf1"])

    def test_parallel_resolves_to_all_its_active_children(self):
        """A ParallelTaskExecutor resolves down to all of its currently active members."""
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.active_tasks.add(make_task_client("leaf2", "some_task"))
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "leaf2"]}.get(
            goal_id, []
        )

        self.assertEqual(resolve_down("p1", self.active_tasks, self.composites), ["leaf1", "leaf2"])

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

        self.assertEqual(resolve_down("m1", self.active_tasks, self.composites), ["leaf1", "leaf2"])

    def test_composite_with_no_active_children_resolves_to_itself(self):
        """If the composite hasn't recorded any active children yet (e.g. paused right as it starts), resolution falls
        back to the composite's own task_id."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.mission.get_active_children.return_value = []

        self.assertEqual(resolve_down("m1", self.active_tasks, self.composites), ["m1"])

    def test_unknown_task_id_raises_key_error(self):
        """If the task_id isn't in ActiveTasks, resolution fails with KeyError."""
        self.assertRaises(KeyError, resolve_down, "unknown", self.active_tasks, self.composites)

    def test_child_vanished_before_resolution_contributes_no_leaves(self):
        """A child listed as active by the composite's own bookkeeping but no longer in ActiveTasks (e.g. a service task
        that finished naturally between being listed and being resolved) is skipped, not a.

        KeyError - only the originally requested top-level task_id must be genuinely active.
        """
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "vanished"]}.get(
            goal_id, []
        )

        self.assertEqual(resolve_down("m1", self.active_tasks, self.composites), ["leaf1"])


class FindEnclosingCompositeTests(unittest.TestCase):
    """Unit tests for composite_resolution._find_enclosing_composite."""

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
    """Unit tests for composite_resolution._resolve_target_task_ids - the combination of _find_enclosing_composite and
    resolve_down that PauseTasksService/ResumeTasksService actually use."""

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
    """Unit tests for composite_resolution._sync_composite_statuses."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        # A bare Mock's methods are truthy by default - without this, every composite would spuriously look
        # "boundary paused" in every test below, since is_paused() is now consulted unconditionally.
        self.mission.is_paused.return_value = False
        self.parallel.is_paused.return_value = False
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

    def test_marks_composite_paused_via_own_flag_with_no_active_children(self):
        """A composite whose own paused flag is armed (e.g. a Mission paused right between two subtasks, whose previous
        subtask already finished and vanished) must be marked PAUSED even though it currently has no.

        active children at all - this is the fix for pausing a mission across a service-backed subtask.
        """
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.mission.get_active_children.return_value = []
        self.mission.is_paused.return_value = True

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.PAUSED)

    def test_own_paused_flag_takes_priority_over_children_derived_status(self):
        """A composite's own paused flag being armed marks it PAUSED even while its active children are still
        IN_PROGRESS (e.g. the mission has already dispatched its next subtask's action call before pausing was.

        requested for it - see resolve_down's `paused_flag` parameter and its ordering guarantee).
        """
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.IN_PROGRESS))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])
        self.mission.is_paused.return_value = True

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.PAUSED)

    def test_tolerates_a_child_that_vanished_between_being_listed_and_being_checked(self):
        """A child reported active by the composite's own bookkeeping but no longer in ActiveTasks (e.g. a.

        service task that just finished) must not raise - it simply doesn't count towards "all children
        paused".
        """
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task", status=TaskStatus.PAUSED))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1", "vanished"]}.get(
            goal_id, []
        )

        _sync_composite_statuses(self.active_tasks, self.composites)

        self.assertEqual(self.active_tasks.get_task_client("m1").task_details.status, TaskStatus.PAUSED)


class ResolveStartIdTests(unittest.TestCase):
    """Unit tests for composite_resolution._resolve_start_id."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_resolve_start_id_returns_enclosing_composite_for_a_tracked_child(self):
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["leaf1"]}.get(goal_id, [])

        self.assertEqual(_resolve_start_id("leaf1", self.active_tasks, self.composites), "m1")

    def test_resolve_start_id_returns_task_id_itself_when_untracked(self):
        self.active_tasks.add(make_task_client("other", "some_task"))
        self.assertEqual(_resolve_start_id("other", self.active_tasks, self.composites), "other")


class ResolveDownPausedFlagTests(unittest.TestCase):
    """Unit tests for composite_resolution.resolve_down's `paused_flag` parameter."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_paused_flag_true_calls_request_pause_with_the_right_goal_id(self):
        """Tests that resolve_down calls request_pause on the composite with the correct goal_id when paused_flag is
        True."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=7))
        self.mission.get_active_children.return_value = []

        resolve_down("m1", self.active_tasks, self.composites, paused_flag=True)

        self.mission.request_pause.assert_called_once_with(bytes([7] * 16))
        self.mission.request_resume.assert_not_called()

    def test_paused_flag_false_calls_request_resume_with_the_right_goal_id(self):
        """Tests that resolve_down calls request_resume on the composite with the correct goal_id when paused_flag is
        False."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=7))
        self.mission.get_active_children.return_value = []

        resolve_down("m1", self.active_tasks, self.composites, paused_flag=False)

        self.mission.request_resume.assert_called_once_with(bytes([7] * 16))
        self.mission.request_pause.assert_not_called()

    def test_paused_flag_none_default_touches_no_paused_flags(self):
        """Tests that resolve_down does not touch any paused flags when paused_flag is None (the default)."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=7))
        self.mission.get_active_children.return_value = []

        resolve_down("m1", self.active_tasks, self.composites)

        self.mission.request_pause.assert_not_called()
        self.mission.request_resume.assert_not_called()

    def test_paused_flag_is_a_no_op_for_a_plain_leaf(self):
        """Tests that resolve_down does not attempt to pause or resume composites when the task_id is a plain leaf."""
        self.active_tasks.add(make_task_client("leaf1", "some_task"))

        resolve_down("leaf1", self.active_tasks, self.composites, paused_flag=True)

        self.mission.request_pause.assert_not_called()
        self.parallel.request_pause.assert_not_called()

    def test_arms_every_composite_encountered_while_descending_through_nested_composites(self):
        """A Mission whose active subtask is itself a parallel task must have BOTH the Mission's and the.

        ParallelTaskExecutor's own paused flags armed - not just the top-level one - so that a pause entering at
        one depth (e.g. redirected up from a leaf to its immediate parallel-task parent) and a later resume
        entering at another depth (e.g. the mission's own task_id) don't leave an intermediate composite's flag
        stuck armed forever. This is a regression guard for that exact bug.
        """
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.active_tasks.add(make_composite_client("p1", PARALLEL, goal_id_byte=2))
        self.active_tasks.add(make_task_client("leaf1", "some_task"))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["p1"]}.get(goal_id, [])
        self.parallel.get_active_children.side_effect = lambda goal_id: {bytes([2] * 16): ["leaf1"]}.get(goal_id, [])

        resolve_down("m1", self.active_tasks, self.composites, paused_flag=True)

        self.mission.request_pause.assert_called_once_with(bytes([1] * 16))
        self.parallel.request_pause.assert_called_once_with(bytes([2] * 16))


class PauseOrResumeGroupTests(unittest.TestCase):
    """Unit tests for composite_resolution.pause_or_resume_group."""

    def setUp(self) -> None:
        self.active_tasks = ActiveTasks()
        self.mission = Mock(spec=Mission)
        self.parallel = Mock(spec=ParallelTaskExecutor)
        self.mission.is_paused.return_value = False
        self.parallel.is_paused.return_value = False
        self.composites = {MISSION: self.mission, PARALLEL: self.parallel}

    def test_arms_the_enclosing_composite_before_resolving_down(self):
        """The composite's own paused flag must be armed even when its only currently-listed child has already vanished
        from ActiveTasks by the time resolution runs (e.g. a service task finishing naturally)."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["vanished"]}.get(goal_id, [])

        success = pause_or_resume_group(
            "m1", self.active_tasks, self.composites, (TaskStatus.RECEIVED, TaskStatus.IN_PROGRESS), Mock(), pause=True
        )

        self.mission.request_pause.assert_called_once_with(bytes([1] * 16))
        self.assertTrue(success)

    def test_a_member_that_vanished_before_the_callback_runs_is_not_a_failure(self):
        """Tests that pause_or_resume_group considers it a success even if a child has vanished before the callback
        runs."""
        self.active_tasks.add(make_composite_client("m1", MISSION, goal_id_byte=1))
        self.mission.get_active_children.side_effect = lambda goal_id: {bytes([1] * 16): ["vanished"]}.get(goal_id, [])
        callback = Mock()

        success = pause_or_resume_group(
            "m1",
            self.active_tasks,
            self.composites,
            (TaskStatus.RECEIVED, TaskStatus.IN_PROGRESS),
            callback,
            pause=True,
        )

        callback.assert_not_called()
        self.assertTrue(success)


if __name__ == "__main__":
    unittest.main()
