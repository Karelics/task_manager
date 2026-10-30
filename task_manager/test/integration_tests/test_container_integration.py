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
import json
import time
import unittest
from typing import Callable, Dict, List

# ROS
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values
from task_manager_test_utils import TaskManagerTestNode

# ROS messages
from action_msgs.msg import GoalStatus
from example_interfaces.action import Fibonacci

# Task Manager messages
from task_manager_msgs.action import Container, ExecuteTask, Mission, PerformInParallel
from task_manager_msgs.msg import SubtaskGoal, TaskStatus

# Task Manager
from task_manager.task_client import TaskClient
from task_manager.task_specs import TaskServerType, TaskSpecs

# pylint: disable=protected-access


class RecordingActionServer:  # pylint: disable=too-few-public-methods
    """Fibonacci action server that records how many goals it received.

    A goal runs for `order` * 0.1 seconds. On cancel it either cancels normally or, with abort_on_cancel, aborts - to
    simulate servers that don't report cancellation properly.
    """

    def __init__(self, node, action_name: str, abort_on_cancel: bool = False):
        self.goals_received = 0
        self._abort_on_cancel = abort_on_cancel
        self._server = ActionServer(
            node=node,
            action_type=Fibonacci,
            action_name=action_name,
            execute_callback=self._execute_cb,
            cancel_callback=lambda _goal_handle: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup(),
        )

    def _execute_cb(self, goal_handle: ServerGoalHandle) -> Fibonacci.Result:
        self.goals_received += 1
        end = time.time() + goal_handle.request.order * 0.1
        while time.time() < end:
            if goal_handle.is_cancel_requested:
                if self._abort_on_cancel:
                    goal_handle.abort()
                else:
                    goal_handle.canceled()
                return Fibonacci.Result()
            time.sleep(0.01)
        goal_handle.succeed()
        return Fibonacci.Result()

    def destroy(self):
        """Destroys the underlying action server."""
        self._server.destroy()


def subtask(task_id: str, task_name: str, run_time_tenths: int = 1) -> SubtaskGoal:
    """SubtaskGoal for one of the recording test tasks."""
    return SubtaskGoal(task_id=task_id, task_name=task_name, task_data=json.dumps({"order": run_time_tenths}))


def container_subtask(task_id: str, subtasks: List[SubtaskGoal]) -> SubtaskGoal:
    """SubtaskGoal running the given subtasks in a system/container."""
    goal = Container.Goal(subtasks=subtasks)
    return SubtaskGoal(task_id=task_id, task_name="system/container", task_data=json.dumps(extract_values(goal)))


class ContainerTests(TaskManagerTestNode):
    """Integration tests for system/container: a group of subtasks that is paused as a unit and restarts from its first
    subtask on resume."""

    def setUp(self) -> None:
        super().setUp()
        self.nav_server = RecordingActionServer(self.test_tasks_node, "container_test/nav")
        self.work_server = RecordingActionServer(self.test_tasks_node, "container_test/work")
        self.abort_server = RecordingActionServer(
            self.test_tasks_node, "container_test/work_abort_on_cancel", abort_on_cancel=True
        )
        self._declare_test_task("nav", "/container_test/nav")
        self._declare_test_task("work", "/container_test/work")
        self._declare_test_task("work_b", "/container_test/work")
        self._declare_test_task("work_abort_on_cancel", "/container_test/work_abort_on_cancel")

    def tearDown(self) -> None:
        for server in (self.nav_server, self.work_server, self.abort_server):
            server.destroy()
        super().tearDown()

    def _declare_test_task(self, task_name: str, topic: str) -> None:
        self.task_manager_node.known_tasks[task_name] = TaskSpecs(
            task_name=task_name,
            topic=topic,
            msg_interface=Fibonacci,
            task_server_type=TaskServerType.ACTION,
            cancel_timeout=2.0,
        )

    def _start_mission(self, subtasks: List[SubtaskGoal]):
        goal = ExecuteTask.Goal(
            task_name="system/mission",
            task_data=json.dumps(extract_values(Mission.Goal(subtasks=subtasks))),
            source="TEST",
        )
        return self._start_task(goal)

    def _active_clients(self) -> Dict[str, TaskClient]:
        return {
            client.task_details.task_id: client for client in self.task_manager_node.active_tasks.get_active_tasks()
        }

    def _mission_id(self) -> str:
        return next(
            task_id
            for task_id, client in self._active_clients().items()
            if client.task_specs.task_name == "system/mission"
        )

    @staticmethod
    def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
        start = time.time()
        while not condition():
            assert time.time() - start < timeout, "Condition not met within timeout"
            time.sleep(0.01)

    def _wait_for_status(self, task_id: str, status: str) -> None:
        def has_status():
            client = self._active_clients().get(task_id)
            return client is not None and client.task_details.status == status

        self._wait_until(has_status)

    def _two_pair_mission(self, work_task: str = "work") -> List[SubtaskGoal]:
        return [
            container_subtask("c1", [subtask("nav1", "nav"), subtask("work1", work_task, run_time_tenths=20)]),
            container_subtask("c2", [subtask("nav2", "nav"), subtask("work2", "work")]),
        ]

    def test_mission_of_containers_succeeds(self):
        """All the container subtasks run once, in order, and the mission succeeds."""
        mission_handle = self._start_mission(
            [
                container_subtask("c1", [subtask("nav1", "nav"), subtask("work1", "work")]),
                container_subtask("c2", [subtask("nav2", "nav"), subtask("work2", "work")]),
            ]
        )

        response = mission_handle.get_result()

        self.assertEqual(response.status, GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(self.nav_server.goals_received, 2)
        self.assertEqual(self.work_server.goals_received, 2)

    def _assert_pause_resume_reruns_first_step(self, pause_target: str, resume_target: str, work_task: str = "work"):
        """Pauses the first container while its second subtask (work1) runs, then resumes: the container must rerun its
        navigation subtask first, and the whole mission must still succeed."""
        work_server = self.abort_server if work_task == "work_abort_on_cancel" else self.work_server
        mission_handle = self._start_mission(self._two_pair_mission(work_task))
        self._wait_for_status("work1", TaskStatus.IN_PROGRESS)
        mission_id = self._mission_id()
        targets = {"mission": mission_id, "container": "c1", "child": "work1"}

        pause_response = self.execute_pause_task([targets[pause_target]])

        self.assertEqual(json.loads(pause_response.result.task_result)["success"], True)
        self._wait_for_status("c1", TaskStatus.PAUSED)
        self._wait_for_status(mission_id, TaskStatus.PAUSED)
        self.assertNotIn("work1", self._active_clients())  # Cancelled with the container, not paused on its own
        self.assertEqual(self.nav_server.goals_received, 1)

        resume_response = self.execute_resume_task([targets[resume_target]])

        self.assertEqual(json.loads(resume_response.result.task_result)["success"], True)
        self._wait_until(lambda: self.nav_server.goals_received == 2)  # Navigation reran before work1
        self._wait_until(lambda: work_server.goals_received == 2)
        response = mission_handle.get_result()
        self.assertEqual(response.status, GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(self.nav_server.goals_received, 3)

    def test_pause_and_resume_by_mission_id(self):
        """Pausing and resuming the outer mission restarts the running container from its first subtask."""
        self._assert_pause_resume_reruns_first_step("mission", "mission")

    def test_pause_and_resume_by_container_id(self):
        """Pausing and resuming the container itself restarts it from its first subtask."""
        self._assert_pause_resume_reruns_first_step("container", "container")

    def test_pause_by_child_id(self):
        """Pausing the container's running child pauses the whole container instead of just the child."""
        self._assert_pause_resume_reruns_first_step("child", "container")

    def test_pause_by_mission_resume_by_container(self):
        """Resuming via the container also clears the paused flag the mission got when it was paused by its own id, so
        the mission continues past the container instead of staying paused."""
        self._assert_pause_resume_reruns_first_step("mission", "container")

    def test_pause_with_child_aborting_on_cancel(self):
        """A child whose server aborts instead of cancelling must not turn the pause into a mission failure."""
        self._assert_pause_resume_reruns_first_step("mission", "mission", work_task="work_abort_on_cancel")

    def test_pause_child_nested_deeper_inside_container(self):
        """Pausing a task inside a parallel task inside a container pauses the whole container."""
        parallel_goal = PerformInParallel.Goal(
            subtasks=[subtask("work1", "work", run_time_tenths=50), subtask("work2", "work_b", run_time_tenths=50)]
        )
        parallel = SubtaskGoal(
            task_id="parallel",
            task_name="system/perform_in_parallel",
            task_data=json.dumps(extract_values(parallel_goal)),
        )
        mission_handle = self._start_mission([container_subtask("c1", [subtask("nav1", "nav"), parallel])])
        self._wait_for_status("work1", TaskStatus.IN_PROGRESS)
        self._wait_for_status("work2", TaskStatus.IN_PROGRESS)
        mission_id = self._mission_id()

        self.execute_pause_task(["work1"])

        self._wait_for_status("c1", TaskStatus.PAUSED)
        self.assertNotIn("parallel", self._active_clients())

        self.execute_resume_task([mission_id])

        self._wait_until(lambda: self.nav_server.goals_received == 2)
        self.execute_cancel_task([mission_id])
        self.assertEqual(mission_handle.get_result().result.task_status, TaskStatus.CANCELED)

    def test_cancel_paused_mission(self):
        """Cancelling a mission while its container is paused cancels both."""
        mission_handle = self._start_mission(self._two_pair_mission())
        self._wait_for_status("work1", TaskStatus.IN_PROGRESS)
        mission_id = self._mission_id()
        self.execute_pause_task([mission_id])
        self._wait_for_status("c1", TaskStatus.PAUSED)

        self.execute_cancel_task([mission_id])

        self.assertEqual(mission_handle.get_result().result.task_status, TaskStatus.CANCELED)
        self._wait_until(lambda: "c1" not in self._active_clients())
        self.assertEqual(self.nav_server.goals_received, 1)


if __name__ == "__main__":
    unittest.main()
