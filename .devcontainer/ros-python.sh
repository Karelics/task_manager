#!/bin/bash
# Python interpreter wrapper that sources the ROS 2 environment first, so the VSCode
# Python extension (which does not read ~/.bashrc) can import rclpy and task_manager.
set +u
. "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
[ -f /ros2_ws/install/setup.bash ] && . /ros2_ws/install/setup.bash
# Import task_manager from the /workspace sources (the path VSCode has open) instead of the
# /ros2_ws/install symlink, otherwise debugger paths do not match and breakpoints stay grey.
export PYTHONPATH="/workspace/task_manager:${PYTHONPATH}"
exec python3 "$@"
