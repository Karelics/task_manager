import json

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from task_manager_msgs.action import ExecuteTask, Mission
from task_manager_msgs.srv import StopTasks
from task_manager_msgs.msg import SubtaskGoal, SubtaskResult

from rosbridge_library.internal.message_conversion import extract_values, populate_instance


class MissionSender(Node):
    """ Sends a request to Task Manager to start a new task """
    def __init__(self) -> None:
        super().__init__("mission_sender")
        self._client = ActionClient(self, ExecuteTask, "/task_manager/execute_task")
        self._client.wait_for_server()

    def start_mission(self):
        """ Sends a Mission with 2 consecutive Stop subtask requests to Task Manager"""
        goal = ExecuteTask.Goal()
        goal.task_name = "system/mission"
        goal.source = "Mission Sender"
        mission_goal = Mission.Goal()

        stop_subtask = SubtaskGoal()
        stop_subtask.task_name = "system/stop"
        stop_subtask.task_data = json.dumps(extract_values(StopTasks.Request()))

        mission_goal.subtasks.append(stop_subtask)
        mission_goal.subtasks.append(stop_subtask)

        goal.task_data = json.dumps(extract_values(mission_goal))
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
        mission_result = populate_instance(json.loads(response.result.task_result), Mission.Result())
        print("---")
        print(f"Mission ID: {task_id}")
        print(f"Mission status: {task_status}")

        for subtask_result in mission_result.mission_results:
            print("-")
            print(f"Subtask ID: {subtask_result.task_id}")
            print(f"Subtask name: {subtask_result.task_name}")
            print(f"Subtask status: {subtask_result.task_status}")
            print(f"Subtask skipped: {subtask_result.skipped}")

            # Subtask result is published to /task_manager/results

        # Note that the response status from the Action server is different from the task_status!
        # response.status == GoalStatus.STATUS_SUCCEEDED

        raise KeyboardInterrupt  # Stopping the execution of the Node


if __name__ == "__main__":
    rclpy.init()

    node = MissionSender()
    node.start_mission()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.try_shutdown()
