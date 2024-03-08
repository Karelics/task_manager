# Examples

## Task Manager


Build and bring up the Task Manager Docker container by running in the repository root:
```
docker compose -f docker/task_manager/docker-compose.yaml up --build
```

Launch a new terminal and exec into Docker container with `docker exec -it task_manager bash` before each of the following commands:

Launch Task Manager
```
ros2 launch task_manager task_manager.launch.py
```

Run active tasks tracker
```
python3 examples/track_active_tasks.py
```

Launch a task or a mission
```
python3 examples/send_task_request.py

python3 examples/send_mission_request.py
```

## Nav2 example
Provides an example on how a blocking task "`spin`" cancels an already running blocking task "`navigate_to_pose`". Demonstrates also the Mission feature, by combining multiple `navigate_to_pose` and `spin` tasks.


1. Launch a Docker container to start Gazebo simulation and Nav2 on Turtlebot by running in the repository root:

```
docker compose -f docker/nav2_example/docker-compose.yaml up --build
```
- Your first simulation launch most likely takes a long time, around 1-5 minutes due to slow Gazebo startup. Gazebo files are automatically cached so any subsequent container starts should be fast. Robot spawning might crash on the first time due to this slow start, but a restart will fix the issue

2. Give a pose estimate for the Turtlebot in Rviz
3. Run navigation example in the Task Manager container
```
docker exec -it task_manager_nav2_example bash
python3 /examples/nav2_example.py
```
