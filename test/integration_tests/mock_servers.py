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

import time

# ROS
import rclpy
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.node import Node

# ROS messages
from example_interfaces.action import Fibonacci
from example_interfaces.srv import AddTwoInts


def create_fib_action_server(node, action_name):
    """Action server that execution time depends on the given Fibonacci goal."""
    return ActionServer(
        node=node,
        action_type=Fibonacci,
        action_name=action_name,
        execute_callback=_execute_cb,
        cancel_callback=_cancel_cb,
        callback_group=ReentrantCallbackGroup(),
    )


def _cancel_cb(_goal_handle):
    return CancelResponse.ACCEPT


def _execute_cb(goal_handle: ServerGoalHandle) -> Fibonacci.Result:
    """Implementation of the fibonacci action server."""
    request = goal_handle.request
    result = Fibonacci.Result()

    # Append the seeds for the Fibonacci sequence
    feedback_msg = Fibonacci.Feedback()
    feedback_msg.sequence = [0, 1]

    # Start executing the action
    for i in range(0, request.order):
        if not rclpy.ok():
            return result

        # Sleep for one second, while constantly checking goal_handle activity
        start = time.time()
        while rclpy.ok():
            if time.time() - start >= 1:
                break

            # If goal is flagged as no longer active then stop executing
            if not goal_handle.is_active:
                goal_handle.abort()
                return result

            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return result
            time.sleep(1 / 100)

        # For first iteration we just want to sleep
        if i == 0:
            continue

        # Update Fibonacci sequence
        feedback_msg.sequence.append(feedback_msg.sequence[i] + feedback_msg.sequence[i - 1])

        # Publish the feedback
        goal_handle.publish_feedback(feedback_msg)

    if not goal_handle.is_active:
        goal_handle.abort()
        return result

    if goal_handle.is_cancel_requested:
        goal_handle.canceled()
        return result

    # Populate result message
    result.sequence = feedback_msg.sequence
    goal_handle.succeed()
    return result


def create_add_two_ints_service(node: Node, service_name):
    """Creates a AddTwoInts service."""

    def service_callback(req: AddTwoInts.Request, result: AddTwoInts.Response) -> AddTwoInts.Response:
        """Service to add two ints.

        Execution time is determined by the sum.
        """
        result.sum = req.a + req.b
        time.sleep(result.sum)
        return result

    return node.create_service(
        AddTwoInts, f"/{service_name}", callback=service_callback, callback_group=MutuallyExclusiveCallbackGroup()
    )
