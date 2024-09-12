cmake_minimum_required(VERSION 3.5)
project(task_manager_msgs)

# Default to C++17
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

set(msg_files
  "msg/ActiveTaskArray.msg"
  "msg/ActiveTask.msg"
  "msg/SubtaskGoal.msg"
  "msg/SubtaskResult.msg"
  "msg/TaskDoneResult.msg"
  "msg/TaskStatus.msg"
)

set(srv_files
  "srv/CancelTasks.srv"
  "srv/StopTasks.srv"
)

set(action_files
  "action/Mission.action"
  "action/ExecuteTask.action"
  "action/Wait.action"
)

rosidl_generate_interfaces(${PROJECT_NAME}
  ${msg_files}
  ${srv_files}
  ${action_files}
  DEPENDENCIES ${dependencies}
 )

ament_package()
