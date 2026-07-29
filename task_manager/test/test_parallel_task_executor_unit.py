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

from typing import Generator
from unittest.mock import MagicMock, patch

# ROS
import rclpy
from rclpy.node import Node

# Thirdparty
import pytest

# Task Manager messages
from task_manager_msgs.action import PerformInParallel
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.task_client import TaskClient
from task_manager.task_details import TaskDetails
from task_manager.tasks.parallel_task import ParallelTask
from task_manager.tasks.parallel_task_executor import ParallelTaskExecutor, PreemptedException

# This test file covers the lines which are difficult to cover with integration tests
# (./integration_tests/test_parallel_task_executor.py)

# pylint: disable=protected-access


@pytest.fixture(name="parallel_task_executor")
def fixture_parallel_task_executor() -> Generator[ParallelTaskExecutor, None, None]:
    """Fixture for creating a ParallelTaskExecutor instance."""
    rclpy.init()
    pte = ParallelTaskExecutor(
        node=Node("test_node"),
        topic="/test/parallel_task_executor",
        prepare_execute_task_result_cb=lambda goal: PerformInParallel.Result(task_id=goal.task_id),
        start_single_task_cb=lambda goal, response: (MagicMock(spec=TaskClient), ""),
    )
    yield pte
    pte._node.destroy_node()
    rclpy.shutdown()


def test_wait_actions_done_goal_handle_inactive(parallel_task_executor: ParallelTaskExecutor) -> None:
    """Test that wait_actions_done returns when the goal_handle is inactive."""
    goal_handle = MagicMock()
    goal_handle.is_cancel_requested = False
    goal_handle.is_active = False
    parallel_task_executor._wait_actions_done(goal_handle, [])


@patch("task_manager.tasks.parallel_task_executor.rclpy.ok")
@patch("task_manager.tasks.parallel_task_executor.ParallelTaskExecutor._gather_and_try_to_run_subtasks")
def test_perform_in_parallel_cb_rclpy_not_ok(
    mock_gather: MagicMock, mock_rclpy_ok: MagicMock, parallel_task_executor: ParallelTaskExecutor
) -> None:
    """Test that perform_in_parallel_cb aborts when rclpy.ok() is False ."""
    mock_gather.side_effect = RuntimeError("Some error")
    mock_rclpy_ok.return_value = False
    goal_handle = MagicMock()
    goal_handle.request.subtasks = [MagicMock()]
    result = parallel_task_executor.perform_in_parallel_cb(goal_handle)
    assert result == PerformInParallel.Result()
    assert goal_handle.abort.called_once()


def test_gather_and_try_preempt(parallel_task_executor: ParallelTaskExecutor) -> None:
    """Test that _gather_and_try_to_run_subtasks preempts tasks when the latest goal handle is different."""
    goal_handle = MagicMock()
    goal_handle.request.subtasks = [MagicMock(), MagicMock()]
    parallel_task_executor._latest_goal_handle = MagicMock()
    assert pytest.raises(PreemptedException, parallel_task_executor._gather_and_try_to_run_subtasks, goal_handle, [])


def make_parallel_task(status: str, task_id: str = "t1") -> ParallelTask:
    """Builds a ParallelTask wrapping a Mock TaskClient with the given status."""
    task_client = MagicMock(spec=TaskClient)
    task_client.task_details = TaskDetails(task_id=task_id, source="TEST", status=status)
    task_client.task_specs.task_name = "some_task"
    return ParallelTask(task_client)


def test_paused_member_is_still_active() -> None:
    """A paused member is still a live part of the group - not finished - so it must not look "done" to
    _wait_actions_done and trigger a premature group teardown."""
    assert make_parallel_task(TaskStatus.PAUSED).active is True


@pytest.mark.parametrize("status", [TaskStatus.PAUSED, TaskStatus.IN_PROGRESS])
def test_cancel_async_always_requests_canceling_regardless_of_paused_state(status: str) -> None:
    """cancel_async() doesn't need to special-case PAUSED itself anymore - request_canceling() now handles a
    paused (already-cancelled) goal transparently, so the caller just always calls it the same way."""
    task = make_parallel_task(status)
    task.cancel_async()
    task._task_client.request_canceling.assert_called_once()
    task._task_client.cancel_task.assert_not_called()


def test_get_active_children_filters_out_finished_members(parallel_task_executor: ParallelTaskExecutor) -> None:
    """Test that get_active_children returns only the active children of a given goal_id."""
    goal_id = b"\x01" * 16
    parallel_task_executor._active_subtasks[goal_id] = [
        make_parallel_task(TaskStatus.IN_PROGRESS, task_id="running"),
        make_parallel_task(TaskStatus.PAUSED, task_id="paused"),
        make_parallel_task(TaskStatus.DONE, task_id="done"),
    ]

    assert parallel_task_executor.get_active_children(goal_id) == ["running", "paused"]


def test_get_active_children_unknown_goal_id_returns_empty(parallel_task_executor: ParallelTaskExecutor) -> None:
    """Test that get_active_children returns an empty list for an unknown goal_id."""
    assert parallel_task_executor.get_active_children(b"\x00" * 16) == []
