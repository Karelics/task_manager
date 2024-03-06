# Examples

## Task Manager

Launch Task Manager

```
ros2 launch task_manager task_manager.launch.py
```

Run active tasks tracker

```
python3 track_active_tasks.py
```

Launch a task or a mission
```
python3 send_task_request.py
```
```
python3 send_mission_request.py
```

## Nav2 example
Provides an example on how a blocking task "`spin`" cancels an already running blocking task "`navigate_to_pose`". Demonstrates also the Mission feature, by combining multiple `navigate_to_pose` and `spin` tasks.

1. Launch Task Manager in its own container with nav2 configuration

```
ros2 launch task_manager task_manager.launch.py params_file:=/ros2_ws/src/task_manager/params/nav2_example.yaml
```

2. Launch nav2 container to start Gazebo simulation and nav2 on Turtlebot. Xhost rule needs to be set for GUI.

```
cd <REPO_PATH>/task_manager/docker/nav2_example
docker compose up
```
- Your first simulation launch most likely takes a long time, around 1-5 minutes due to slow Gazebo startup. Gazebo files are automatically cached so any subsequent container starts should be fast. Robot spawning might crash on the first time due to this slow start, but a restart will fix the issue

3. Give a pose estimate for Turtlebot in Rviz
4. Run navigation example (in Task Manager container)
```
python3 nav2_example.py
```
