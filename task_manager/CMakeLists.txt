cmake_minimum_required(VERSION 3.5)
project(task_manager)

# Default to C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)

install(DIRECTORY
  launch
  params
  DESTINATION share/${PROJECT_NAME}/
)

### Python support ###
find_package(ament_cmake_python REQUIRED)
find_package(rclpy REQUIRED)

# Install Python modules
ament_python_install_package(${PROJECT_NAME})

# Install Python executables
install(PROGRAMS
  task_manager/task_manager_node.py
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
    find_package(example_interfaces REQUIRED)
    find_package(ament_cmake_pytest REQUIRED)
    ament_add_pytest_test(${PROJECT_NAME}_test_task_manager . TIMEOUT 120)
endif()

ament_package()
