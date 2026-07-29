# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ROS 2 (`ament_cmake` + `ament_cmake_python`) package. Centralized task management node that turns any existing
ROS 2 service or action into a trackable, cancellable "Task", with support for blocking tasks, missions (sequential
composite tasks) and parallel tasks (tasks that cancel each other when one finishes).

Two ROS packages in this repo:
- `task_manager/` — the Python implementation.
- `task_manager_msgs/` — message/service/action interface definitions (`msg/`, `srv/`, `action/`).

## Build & test

This is a ROS 2 workspace package, not a standalone Python project — it expects to live under `<ros2_ws>/src/`.

Recommended: use the Docker container (has all ROS deps preinstalled):
```
docker compose -f docker/task_manager/docker-compose.yaml run --build task_manager
```

Run the full test suite (inside the container / ROS 2 environment):
```
python3 -m pytest /ros2_ws/src/task_manager/test/
```

Run a single test file / test (standard pytest, run from `task_manager/` package dir or with full path):
```
python3 -m pytest test/test_task_registrator.py
python3 -m pytest test/test_task_registrator.py::test_name -k "something"
```

Tests are split into:
- `task_manager/test/*.py` — unit tests (one per source module).
- `task_manager/test/integration_tests/*.py` — spin up real rclpy nodes/mock action & service servers
  (`integration_tests/mock_servers.py`) and exercise the system end-to-end.

Linting/formatting is enforced via pre-commit (`black`, `isort`, `pycln`, `pyupgrade`, `ruff`, `docformatter`,
plus ROS-specific hooks for xacro/launch/package xml). Run with `pre-commit run --all-files`.

## Architecture

Everything is driven off a single `TaskManager` node (`task_manager/task_manager_node.py`), which is the only
public entry point (`/task_manager/execute_task` action). Request flow:

1. `TaskManager.declare_tasks()` reads the `tasks` ROS parameter list and builds a `TaskSpecs` (`task_specs.py`)
   per declared task — this is where a plain ROS action/service topic + message interface becomes a named "Task"
   with properties like `blocking`, `reentrant`, `cancel_on_stop`, `cancel_timeout`.
2. A goal to `/task_manager/execute_task` is handled by `TaskManager.execute_task()`, which delegates starting the
   task to `TaskRegistrator.start_new_task()` (`task_registrator.py`). This is where conflict resolution happens:
   duplicate task-id rejection, cancelling an existing task of the same name (unless `reentrant`), cancelling the
   currently active blocking task (if the new task is `blocking`).
3. Starting a task wraps the underlying ROS action/service call in a `TaskClient` (`task_client.py`):
   `ActionTaskClient` or `ServiceTaskClient`, chosen automatically from the message interface shape
   (`detect_task_server_type` — has `Goal`/`Result` → action, has `Request`/`Response` → service). `TaskClient` is
   the abstraction that gives every task a uniform `cancel_task()` / `goal_done` / done-callback interface
   regardless of whether it's backed by an action or a service (services can't really be cancelled — cancelling
   just waits out `cancel_timeout`).
4. `ActiveTasks` (`active_tasks.py`) is the single source of truth for what's currently running (keyed by task_id).
   `TaskManager` subscribes to its changed-callback to publish `/task_manager/active_tasks`, and every finished
   `TaskClient` publishes its result to `/task_manager/results`.
5. Task goal/result payloads cross the JSON boundary via `rosbridge_library.internal.message_conversion`
   (`populate_instance`/`extract_values`), so tasks are started/reported using JSON-encoded `task_data` /
   `task_result` regardless of the underlying ROS interface.

Everything under `tasks/` are the built-in "system tasks", each self-registered in `TaskManager.setup_system_tasks()`
and each exposing `get_task_specs()` so it's treated like any other declared task:
- `system_tasks.py` — `StopTasksService` (`system/stop`, cancels everything with `cancel_on_stop=True`),
  `CancelTasksService` (`system/cancel_task`, cancel-by-id), `PauseTasksService` / `ResumeTasksService`
  (`system/pause_task` / `system/resume_task`, pause-by-id/resume-by-id; pausing/resuming a Mission or a
  `perform_in_parallel` group by its own task_id redirects to whichever of its children are currently active, via
  the `ActiveChildrenTracker` protocol/`_resolve_down`/`_find_enclosing_composite`/`_sync_composite_statuses`
  helpers), `WaitTask` (`system/wait`, blocking wait/indefinite-wait task).
- `mission.py` — `Mission` (`system/mission`), runs a list of subtasks sequentially by re-entering
  `TaskManager.execute_task()` per subtask; aborts/cancels the whole mission on subtask failure unless
  `allow_skipping` is set on that subtask.
- `parallel_task.py` / `parallel_task_executor.py` — `system/perform_in_parallel`. Wraps multiple started
  `TaskClient`s as `ParallelTask`s; when any one finishes, the executor cancels the rest
  (`require_finish_on_parallel_cancel` controls whether a task is expected to react to cancel quickly).
- `task_action_server.py` / `task_service_server.py` — optional per-task action/service servers created only when
  `enable_task_servers:=True`, exposing each declared task directly under `/task_manager/task/<task_name>` using
  its native ROS message type (bypassing JSON) — debugging/dev convenience only.

Task identity note: `TaskSpecs` (static config from parameters) and `TaskDetails` (per-invocation runtime state:
task_id, source, status, result) are deliberately separate — a `TaskClient` instance carries one of each.

## Parameters

Tasks are declared entirely through ROS parameters (see `params/` and README "Parameters" table for the full
per-task schema: `task_name`, `topic`, `msg_interface`, `blocking`, `cancel_on_stop`, `reentrant`,
`service_success_field`, `cancel_timeout`, `require_finish_on_parallel_cancel`). `msg_interface` is a dotted string
(e.g. `"example_interfaces.action.Fibonacci"`) resolved at runtime via `import_module`
(`get_plugin_class_from_string` in `task_manager_node.py`).

## Examples

`examples/` has runnable scripts (`send_task_request.py`, `send_mission_request.py`, `track_active_tasks.py`,
`nav2_example.py`) plus a Nav2/Gazebo/Turtlebot Docker demo under `docker/nav2_example/` — see `examples/README.md`
for exact run steps.
