# UAV Task Scheduler

## Overview

The UAV Task Scheduler is a centralized task distribution system for multi-robot deployments. It monitors multiple robots running the `task_manager` node and distributes incoming tasks evenly across available robots.

## Architecture

### Components

1. **UAV Task Scheduler Node** (`uav_task_scheduler_node.py`)
   - Central scheduler that manages task distribution
   - Monitors robot availability via their `active_tasks` topics
   - Distributes tasks using even distribution (round-robin based on total tasks assigned)
   - Handles asynchronous communication and robot disconnections

2. **RobotStatus** (`robot_status.py`)
   - Tracks individual robot state
   - Monitors connection status based on last seen timestamp
   - Tracks active tasks on each robot

3. **TaskAssignment** (`task_assignment.py`)
   - Records task assignment history
   - Tracks which robot was assigned which task
   - Maintains task status throughout lifecycle

### How It Works

```
┌─────────────────────────────────────────┐
│      Client Application / UI            │
└──────────────┬──────────────────────────┘
               │ Send task via action
               ▼
┌─────────────────────────────────────────┐
│     UAV Task Scheduler Node              │
│  - Monitors all robots                   │
│  - Selects available robot               │
│  - Distributes tasks evenly              │
└──┬────────┬─────────┬───────────────────┘
   │        │         │
   ▼        ▼         ▼
┌─────┐  ┌─────┐   ┌─────┐
│ uav1│  │ uav2│   │ uav3│
│ TM  │  │ TM  │   │ TM  │  (Task Manager nodes)
└─────┘  └─────┘   └─────┘
```

### Communication Flow

1. **Task Request**: Client sends `ExecuteTask` action to `/uav_task_scheduler/execute_task`
2. **Robot Selection**: Scheduler selects an available robot with fewest total tasks
3. **Task Assignment**: Scheduler forwards task to selected robot's `/uav#/task_manager/execute_task`
4. **Monitoring**: Scheduler monitors robot's `/uav#/task_manager/active_tasks` topic
5. **Result**: Robot publishes result to `/uav#/task_manager/results`
6. **Response**: Scheduler returns result to original client

## Configuration

### Parameters

Configure robots in `params/uav_task_scheduler_defaults.yaml`:

```yaml
uav_task_scheduler:
  ros__parameters:
    # List of robot prefixes (e.g., uav1, uav2, uav3)
    robot_prefixes: ["uav1", "uav2", "uav3"]

    # Timeout for considering robot disconnected (seconds)
    connection_timeout: 5.0
```

### Robot Naming Convention

Robots must be named with the prefix specified in `robot_prefixes`:
- `uav1` → Topics: `/uav1/task_manager/execute_task`, `/uav1/task_manager/active_tasks`
- `uav2` → Topics: `/uav2/task_manager/execute_task`, `/uav2/task_manager/active_tasks`
- etc.

## Usage

### 1. Start Individual Robot Task Managers

On each robot (or in separate namespaces):

```bash
# Robot 1
ros2 launch task_manager task_manager.launch.py \
  namespace:=uav1 \
  params_file:=path/to/robot_config.yaml

# Robot 2
ros2 launch task_manager task_manager.launch.py \
  namespace:=uav2 \
  params_file:=path/to/robot_config.yaml

# Robot 3
ros2 launch task_manager task_manager.launch.py \
  namespace:=uav3 \
  params_file:=path/to/robot_config.yaml
```

### 2. Start the UAV Task Scheduler

```bash
ros2 launch task_manager uav_task_scheduler.launch.py \
  params_file:=params/uav_task_scheduler_defaults.yaml
```

### 3. Send Tasks to the Scheduler

#### Using the Example Script

```bash
python3 examples/send_task_to_scheduler.py
```

#### Using ROS 2 CLI

```bash
ros2 action send_goal /uav_task_scheduler/execute_task \
  task_manager_msgs/action/ExecuteTask \
  "{task_name: 'system/wait', source: 'CLI', task_data: '{\"duration\": 5.0}'}"
```

#### Programmatically

```python
import rclpy
from rclpy.action import ActionClient
from task_manager_msgs.action import ExecuteTask
import json

# Create action client
client = ActionClient(node, ExecuteTask, '/uav_task_scheduler/execute_task')
client.wait_for_server()

# Create goal
goal = ExecuteTask.Goal()
goal.task_name = "navigation/navigate_to_pose"
goal.source = "MyApp"
goal.task_data = json.dumps({
    "pose": {
        "header": {"frame_id": "map"},
        "pose": {
            "position": {"x": 1.0, "y": 2.0, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
        }
    }
})

# Send goal and wait for result
future = client.send_goal_async(goal)
```

## Features

### Even Distribution

Tasks are distributed using a round-robin approach based on total tasks assigned to each robot:
- Tracks total tasks assigned to each robot
- Always selects the robot with the fewest total assignments
- Ensures balanced workload across the fleet

### Asynchronous Communication

- Monitors robot connectivity via active_tasks topic
- Marks robots as disconnected if no updates received within `connection_timeout`
- Only assigns tasks to connected, available robots
- Gracefully handles robot disconnections and reconnections

### Availability Detection

A robot is considered available when:
1. It is connected (recently published active_tasks)
2. It has no active tasks (empty active_tasks list)

### Task Assignment Tracking

The scheduler maintains a complete history of:
- Which robot received which task
- Task assignment timestamp
- Current status of each task
- Source of the task request

## Monitoring

### Check Robot Status

Subscribe to active_tasks topics:

```bash
# Monitor all robots
ros2 topic echo /uav1/task_manager/active_tasks
ros2 topic echo /uav2/task_manager/active_tasks
ros2 topic echo /uav3/task_manager/active_tasks
```

### Check Task Results

```bash
# Monitor results from all robots
ros2 topic echo /uav1/task_manager/results
ros2 topic echo /uav2/task_manager/results
ros2 topic echo /uav3/task_manager/results
```

### Check Scheduler Logs

```bash
ros2 node info /uav_task_scheduler
```

The scheduler logs will show:
- Robot availability status
- Task assignments (which robot received which task)
- Task completion status
- Robot disconnection warnings

## Example Scenarios

### Scenario 1: Two Robots, Multiple Tasks

1. Both uav1 and uav2 are idle
2. Task A arrives → Assigned to uav1 (0 previous tasks)
3. Task B arrives → Assigned to uav2 (0 previous tasks)
4. Task C arrives while A and B are running → Waits for available robot
5. Task A completes → Task C assigned to uav1

### Scenario 2: Robot Disconnection

1. Three robots running: uav1, uav2, uav3
2. uav2 network disconnects
3. Scheduler marks uav2 as disconnected after timeout
4. New tasks distributed only to uav1 and uav3
5. uav2 reconnects → Resumes receiving tasks

### Scenario 3: Mission Distribution

```python
# Send a mission that will be assigned to a single robot
ros2 action send_goal /uav_task_scheduler/execute_task \
  task_manager_msgs/action/ExecuteTask \
  "{task_name: 'system/mission', source: 'CLI',
    task_data: '{\"subtasks\": [{\"task_name\": \"system/wait\",
                \"task_data\": \"{\\\"duration\\\": 2.0}\"},
               {\"task_name\": \"navigation/navigate_to_pose\",
                \"task_data\": \"...\"}]}'}"
```

## Troubleshooting

### No Available Robots

**Problem**: Scheduler reports "No available robots found"

**Solutions**:
- Check that robot task_manager nodes are running
- Verify robot namespaces match configuration
- Check network connectivity between scheduler and robots
- Verify robots have no active tasks blocking them

### Tasks Not Being Assigned

**Problem**: Tasks accepted but not executed

**Solutions**:
- Check robot action servers are running: `ros2 action list | grep execute_task`
- Verify task names are configured in robot's parameters
- Check scheduler logs for error messages
- Ensure robots are publishing active_tasks

### Robot Marked as Disconnected

**Problem**: Scheduler logs show robot disconnected but it's running

**Solutions**:
- Check topic connectivity: `ros2 topic hz /uav#/task_manager/active_tasks`
- Verify QoS settings match between publisher and subscriber
- Increase `connection_timeout` parameter if network is slow
- Check for network issues or firewall rules

## Limitations

1. **Sequential Task Execution**: Each robot executes one task at a time (unless configured as reentrant)
2. **No Priority**: Tasks are processed in order received, no prioritization
3. **No Load Balancing by Task Duration**: Distribution is count-based, not time-based
4. **No Task Migration**: Running tasks cannot be moved between robots

## Future Enhancements

- Task priority levels
- Load balancing based on estimated task duration
- Task migration capabilities
- Robot capability matching (assign tasks to robots with required capabilities)
- Advanced scheduling algorithms (earliest deadline first, etc.)
