# ROS
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

# Task Manager messages
from task_manager_msgs.msg import ActiveTask, ActiveTaskArray, TaskStatus


class TaskTracker(Node):
    """Tracks all the currently active Task Manager tasks."""

    def __init__(self) -> None:
        super().__init__("task_tracker")
        self.active_tasks = {str: ActiveTask}

        self.create_subscription(
            ActiveTaskArray,
            "task_manager/active_tasks",
            self._active_tasks_callback,
            qos_profile=QoSProfile(
                depth=10,  # With 1 has a chance to miss some of the messages as they arrive fast
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                # Has to be BEST_EFFORT, otherwise with RELIABLE we will get a bunch of old messages on startup
            ),
        )
        print("Waiting for tasks")

    def _active_tasks_callback(self, data: ActiveTaskArray):
        """Called every time the active tasks list changes."""
        for active_task in data.active_tasks:
            if active_task.task_status == TaskStatus.IN_PROGRESS and active_task.task_id not in self.active_tasks:
                self.active_tasks[active_task.task_id] = active_task
                print(f"Task {active_task.task_name} ({active_task.task_id}) is in progress.")

            elif active_task.task_status in [TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELED]:
                del self.active_tasks[active_task.task_id]
                if active_task.task_status == TaskStatus.DONE:
                    print(f"Task {active_task.task_name} ({active_task.task_id}) finished successfully!")
                elif active_task.task_status == TaskStatus.ERROR:
                    print(f"Task  {active_task.task_name} ({active_task.task_id}) failed!")
                elif active_task.task_status == TaskStatus.CANCELED:
                    print(f"Task  {active_task.task_name} ({active_task.task_id}) was canceled!")


if __name__ == "__main__":
    rclpy.init()

    node = TaskTracker()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.try_shutdown()
