#!/bin/bash
# Python interpreter wrapper that sources the ROS 2 environment first, so the VSCode
# Python extension (which does not read ~/.bashrc) can import rclpy and task_manager.
set +u
. "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
[ -f /ros2_ws/install/setup.bash ] && . /ros2_ws/install/setup.bash
exec python3 "$@"
