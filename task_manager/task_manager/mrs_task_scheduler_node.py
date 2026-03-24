#!/usr/bin/env python3

#  ------------------------------------------------------------------
#   Copyright 2026, Frantisek Nekovar
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

import uuid
import time
from datetime import datetime, timedelta
from threading import Lock
from typing import Dict, List, Optional
import asyncio

import rclpy
from rclpy import Parameter
from rclpy.action import ActionClient, ActionServer
from rclpy.action.server import ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

from task_manager.robot_status import RobotStatus
from task_manager.task_assignment import TaskAssignment
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import ActiveTaskArray, TaskDoneResult, TaskStatus


class UAVTaskScheduler(Node):
    """
    Central scheduler node that distributes tasks to multiple robots.

    Monitors each robot's active_tasks topic and distributes incoming tasks
    evenly across available robots.
    """

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__("uav_task_scheduler", parameter_overrides=parameter_overrides)

        # Parameters
        self.robot_prefixes: List[str] = self.declare_parameter(
            "robot_prefixes", Parameter.Type.STRING_ARRAY
        ).value
        if not self.robot_prefixes:
            self.get_logger().error(
                "No robot prefixes configured! Please specify robot_prefixes parameter (e.g., ['uav1', 'uav2'])"
            )
            raise ValueError("robot_prefixes parameter is required")

        self.connection_timeout: float = self.declare_parameter("connection_timeout", 5.0).value

        # Robot tracking
        self.robots: Dict[str, RobotStatus] = {}
        self.robots_lock = Lock()

        # Task assignments tracking
        self.task_assignments: Dict[str, TaskAssignment] = {}
        self.assignments_lock = Lock()

        # Initialize robot status for each configured robot
        for robot_id in self.robot_prefixes:
            self.robots[robot_id] = RobotStatus(robot_id=robot_id)

        # QoS profiles
        active_tasks_qos = QoSProfile(
            depth=10, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL
        )
        results_qos = QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE)

        # Subscribe to each robot's active_tasks topic
        self.active_tasks_subscribers = {}
        for robot_id in self.robot_prefixes:
            topic = f"/{robot_id}/task_manager/active_tasks"
            subscriber = self.create_subscription(
                ActiveTaskArray,
                topic,
                lambda msg, rid=robot_id: self._active_tasks_callback(rid, msg),
                qos_profile=active_tasks_qos,
            )
            self.active_tasks_subscribers[robot_id] = subscriber
            self.get_logger().info(f"Subscribed to {topic}")

        # Subscribe to each robot's results topic
        self.results_subscribers = {}
        for robot_id in self.robot_prefixes:
            topic = f"/{robot_id}/task_manager/results"
            subscriber = self.create_subscription(
                TaskDoneResult,
                topic,
                lambda msg, rid=robot_id: self._task_result_callback(rid, msg),
                qos_profile=results_qos,
            )
            self.results_subscribers[robot_id] = subscriber

        # Create action clients for each robot's execute_task action
        self.action_clients: Dict[str, ActionClient] = {}
        for robot_id in self.robot_prefixes:
            action_name = f"/{robot_id}/task_manager/execute_task"
            client = ActionClient(
                self, ExecuteTask, action_name, callback_group=ReentrantCallbackGroup()
            )
            self.action_clients[robot_id] = client
            self.get_logger().info(f"Created action client for {action_name}")

        # Create our own action server to receive task requests
        self._action_server = ActionServer(
            node=self,
            action_type=ExecuteTask,
            action_name="/uav_task_scheduler/execute_task",
            execute_callback=self._execute_task_callback,
            callback_group=ReentrantCallbackGroup(),
        )

        # Timer to check for disconnected robots
        #self.connection_check_timer = self.create_timer(1.0, self._check_robot_connections)

        self.get_logger().info(f"UAV Task Scheduler initialized with robots: {self.robot_prefixes}")

    def _active_tasks_callback(self, robot_id: str, msg: ActiveTaskArray) -> None:
        """Callback for receiving active tasks updates from a robot."""
        with self.robots_lock:
            if robot_id in self.robots:
                self.robots[robot_id].update_active_tasks(msg.active_tasks)
                # self.get_logger().info(f"UAV {robot_id} active tasks updated: {[t.task_id for t in msg.active_tasks]}")

                # Update task assignment statuses
                with self.assignments_lock:
                    for task_assignment in self.task_assignments.values():
                        if task_assignment.robot_id == robot_id:
                            # Check if task is in active tasks
                            task_found = False
                            for active_task in msg.active_tasks:
                                if active_task.task_id == task_assignment.task_id:
                                    task_assignment.status = active_task.task_status
                                    task_found = True
                                    break

                            # If task was in progress but not in active tasks anymore,
                            # wait for result callback to update final status

    def _task_result_callback(self, robot_id: str, msg: TaskDoneResult) -> None:
        """Callback for receiving task completion results from a robot."""
        with self.assignments_lock:
            if msg.task_id in self.task_assignments:
                assignment = self.task_assignments[msg.task_id]
                assignment.status = msg.task_status
                self.get_logger().info(
                    f"Task {msg.task_id} ({msg.task_name}) completed on {robot_id} with status {msg.task_status}"
                )

    def _check_robot_connections(self) -> None:
        """Periodically check for disconnected robots based on last_seen timestamp."""
        current_time = datetime.now()
        timeout = timedelta(seconds=self.connection_timeout)

        with self.robots_lock:
            for robot_id, robot in self.robots.items():
                if robot.is_connected and (current_time - robot.last_seen) > timeout:
                    self.get_logger().warning(f"Robot {robot_id} appears to be disconnected")
                    robot.mark_disconnected()

    def _select_robot_for_task(self) -> Optional[str]:
        """
        Select the best robot to assign the next task using even distribution.
        Returns robot_id or None if no robot is available.
        """
        with self.robots_lock:
            # Filter to available robots
            available_robots = [
                (robot_id, robot)
                for robot_id, robot in self.robots.items()
                if robot.is_available()
            ]

            if not available_robots:
                self.get_logger().info("No available robots to assign task")
                return None

            # Select robot with fewest total tasks assigned (even distribution)
            selected_robot_id = min(
                available_robots, key=lambda x: x[1].total_tasks_assigned
            )[0]


            self.get_logger().info(f"Selected robot {selected_robot_id} for task")
            return selected_robot_id

    async def _execute_task_callback(self, goal_handle: ServerGoalHandle) -> ExecuteTask.Result:
        """Handle incoming task execution requests and distribute to robots."""
        request = goal_handle.request

        # Generate task ID if not provided
        if request.task_id == "":
            request.task_id = str(uuid.uuid4())

        self.get_logger().info(
            f"Received task request: {request.task_name} (ID: {request.task_id}) from {request.source}"
        )

        result = ExecuteTask.Result()
        result.task_id = request.task_id

        # Wait for an available robot (with timeout)
        max_wait_time = 30.0  # seconds
        wait_interval = 0.5  # seconds
        total_waited = 0.0

        selected_robot_id = None
        while total_waited < max_wait_time and rclpy.ok():
            selected_robot_id = self._select_robot_for_task()
            if selected_robot_id:
                break

            # Check if goal was cancelled while waiting
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result.task_status = TaskStatus.CANCELED
                result.error_code = ""
                return result

            self.get_logger().info(f"No available robots, waiting... ({total_waited:.1f}s)")
            time.sleep(wait_interval)
            # rclpy.spin_once(self, timeout_sec=wait_interval)
            total_waited += wait_interval

        if not selected_robot_id:
            self.get_logger().error("No available robots found within timeout period")
            goal_handle.abort()
            result.task_status = TaskStatus.ERROR
            result.error_code = "no_available_robots"
            return result

        # Mark robot as having a task assigned
        with self.robots_lock:
            self.robots[selected_robot_id].total_tasks_assigned += 1

        # Create task assignment record
        assignment = TaskAssignment(
            task_id=request.task_id,
            task_name=request.task_name,
            robot_id=selected_robot_id,
            assigned_at=datetime.now(),
            status="PENDING",
            task_data=request.task_data,
            source=request.source,
        )

        with self.assignments_lock:
            self.task_assignments[request.task_id] = assignment

        self.get_logger().info(f"Assigning task {request.task_id} to robot {selected_robot_id}")

        # Send task to selected robot
        action_client = self.action_clients[selected_robot_id]

        # Wait for action server to be available
        if not action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error(f"Action server for {selected_robot_id} not available")
            goal_handle.abort()
            result.task_status = TaskStatus.ERROR
            result.error_code = "robot_action_server_unavailable"
            assignment.status = "ERROR"
            return result

        # Send goal to robot
        robot_goal = ExecuteTask.Goal()
        robot_goal.task_id = request.task_id
        robot_goal.task_name = request.task_name
        robot_goal.source = request.source
        robot_goal.task_data = request.task_data

        send_goal_future = await action_client.send_goal_async(robot_goal)

        # Wait for goal to be accepted
        # rclpy.spin_until_future_complete(self, send_goal_future, timeout_sec=5.0)
        if send_goal_future is None:
            self.get_logger().error(f"Failed to send goal to {selected_robot_id} (future is None)")
            goal_handle.abort()
            result.task_status = TaskStatus.ERROR
            result.error_code = "failed_to_send_goal"
            assignment.status = "ERROR"
            return result

        if not send_goal_future.accepted:
            self.get_logger().error(f"Goal rejected by {selected_robot_id}")
            goal_handle.abort()
            result.task_status = TaskStatus.ERROR
            result.error_code = "goal_rejected_by_robot"
            assignment.status = "ERROR"
            return result

        self.get_logger().info(f"Task {request.task_id} sent to {selected_robot_id} and was accepted")
        assignment.status = "SENT"

        # Wait for result from robot
        result_future = await send_goal_future.get_result_async()

        # while not result_future.done():
        #     # Check for cancellation
        #     if goal_handle.is_cancel_requested:
        #         # Cancel the goal on the robot
        #         self.get_logger().info(f"Cancelling task {request.task_id} on {selected_robot_id}")
        #         cancel_future = robot_goal_handle.cancel_goal_async()
        #         rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=5.0)
        #         goal_handle.canceled()
        #         result.task_status = TaskStatus.CANCELED
        #         assignment.status = "CANCELED"
        #         return result
        #
        #     rclpy.spin_once(self, timeout_sec=0.1)

        robot_result = result_future.result

        # Copy result from robot
        result.task_id = robot_result.task_id
        result.task_result = robot_result.task_result
        result.task_status = robot_result.task_status
        result.error_code = robot_result.error_code

        assignment.status = robot_result.task_status

        if result.task_status == TaskStatus.DONE:
            goal_handle.succeed()
        elif result.task_status == TaskStatus.CANCELED:
            goal_handle.canceled()
        else:
            goal_handle.abort()

        self.get_logger().info(
            f"Task {request.task_id} completed on {selected_robot_id} with status {result.task_status}"
        )

        return result

    def get_robot_status(self, robot_id: str) -> Optional[RobotStatus]:
        """Get the current status of a specific robot."""
        with self.robots_lock:
            return self.robots.get(robot_id)

    def get_all_robots_status(self) -> Dict[str, RobotStatus]:
        """Get status of all robots."""
        with self.robots_lock:
            return self.robots.copy()

    def get_task_assignments(self) -> Dict[str, TaskAssignment]:
        """Get all task assignments."""
        with self.assignments_lock:
            return self.task_assignments.copy()


def main() -> None:
    """Main entry point for the UAV Task Scheduler node."""
    rclpy.init()

    try:
        scheduler = UAVTaskScheduler()
        executor = MultiThreadedExecutor()
        rclpy.spin(scheduler, executor=executor)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if rclpy.ok():
            rclpy.try_shutdown()


if __name__ == "__main__":
    main()
