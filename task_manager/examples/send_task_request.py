import json

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.srv import StopTasks

from rosbridge_library.internal.message_conversion import extract_values, populate_instance

# pylint: disable=duplicate-code
# Disables duplicate code warning, fine in examples


class TaskSender(Node):
    """ Sends a request to Task Manager to start a new task """
    def __init__(self) -> None:
        super().__init__("task_sender")
        self._client = ActionClient(self, ExecuteTask, "/task_manager/execute_task")
        self._client.wait_for_server()

    def start_task(self):
        """ Sends Stop task request to Task Manager"""
        goal = ExecuteTask.Goal()
        goal.task_name = "system/stop"
        goal.source = "Task Sender"
        goal.task_data = json.dumps(extract_values(StopTasks.Request()))
        print("Sending stop -task from TaskSender node")
        future = self._client.send_goal_async(goal)
        future.add_done_callback(self._task_accepted_callback)

    def _task_accepted_callback(self, future):
        """ Callback to handle Accepted action goal"""
        goal_handle = future.result()
        future = goal_handle.get_result_async()
        future.add_done_callback(self._task_done_callback)

    def _task_done_callback(self, future):
        """ Called when Task finishes """
        response: ExecuteTask.Result = future.result()
        task_id = response.result.task_id
        task_status = response.result.task_status
        task_result = json.loads(response.result.task_result)
        _stop_response = populate_instance(task_result, StopTasks.Response())  # Converts back to ROS msg if needed
        print(f"Task finished with status {task_status}")
        print(f"Task ID: {task_id}")
        print(f"Task result: {task_result}")

        # Note that the response status from the Action server is different from the task_status!
        # response.status == GoalStatus.STATUS_SUCCEEDED

        raise KeyboardInterrupt  # Stopping the execution of the Node


if __name__ == "__main__":
    rclpy.init()

    node = TaskSender()
    node.start_task()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.try_shutdown()
