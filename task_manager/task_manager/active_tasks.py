#  ------------------------------------------------------------------
#   Copyright 2024 Karelics Oy
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#  ------------------------------------------------------------------

import threading
from typing import Callable, Dict, List, Optional

# Task Manager
from task_manager.task_client import CancelTaskFailedError, TaskClient
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskSpecs


class ActiveTasks:
    """Keeps track of currently active tasks by storing Task Clients, and notifies callback whenever there are new or
    removed active tasks.

    Active tasks updates automatically whenever Task Client finishes
    """

    def __init__(self, active_tasks_changed_cb: Optional[Callable[[Dict[str, TaskClient]], None]] = None):
        self._active_tasks_changed_cb = active_tasks_changed_cb

        self._active_tasks_lock = threading.Lock()
        self._active_tasks: Dict[str, TaskClient] = {}  # task_id -> TaskClient
        with self._active_tasks_lock:
            self._active_tasks_changed()

    def set_active_tasks_changed_cb(self, active_tasks_changed_cb: Callable[[Dict[str, TaskClient]], None]):
        """Sets the callback, which is called when active tasks list changes.

        The callback is being called instantly with the current state of the active tasks.
        """
        self._active_tasks_changed_cb = active_tasks_changed_cb
        with self._active_tasks_lock:
            self._active_tasks_changed()

    def add(self, task_client: TaskClient) -> None:
        """Adds task client to active tasks."""
        with self._active_tasks_lock:
            self._active_tasks[task_client.task_details.task_id] = task_client
            self._active_tasks_changed()
        task_client.register_done_callback(self._task_done_cb)

    def _delete(self, task_id: str) -> None:
        with self._active_tasks_lock:
            del self._active_tasks[task_id]
            self._active_tasks_changed()

    def clear_all(self) -> None:
        """Clears the active task clients list, so that active tasks are no longer tracked.

        Doesn't cancel the active tasks. Can be used to publish empty active tasks list on shutdown
        """
        with self._active_tasks_lock:
            self._active_tasks.clear()
            self._active_tasks_changed()

    def get_active_tasks(self) -> List[TaskClient]:
        """Return all the task clients that are currently active.

        :return: List of active TaskClients. Empty list if there are no active tasks
        """
        task_clients = []
        with self._active_tasks_lock:
            for task_client in self._active_tasks.values():
                task_clients.append(task_client)
        return task_clients

    def get_active_tasks_by_name(self, task_name: str) -> List[TaskClient]:
        """Return all the task clients that exist with a given name.

        :param task_name: Name of the task to look for.
        :return: List of active TaskClients. Empty list if active task with the given name doesn't exist
        """
        task_clients = []
        with self._active_tasks_lock:
            for task_client in self._active_tasks.values():
                if task_client.task_specs.task_name == task_name:
                    task_clients.append(task_client)
        return task_clients

    def get_blocking_task(self) -> Optional[TaskClient]:
        """Gets the active blocking task client."""
        found_blocking_tasks = 0
        blocking_task = None

        with self._active_tasks_lock:
            for task_client in self._active_tasks.values():
                if task_client.task_specs.blocking:
                    found_blocking_tasks += 1
                    blocking_task = task_client

        assert found_blocking_tasks < 2, "Active tasks has multiple blocking tasks running at the same time!"
        return blocking_task

    def cancel_tasks_on_stop(self) -> None:
        """Cancels all the tasks that have their cancel_on_stop field set to True.

        :raises CancelTaskFailedError: if canceling of one or more tasks failed.
        """
        # self._active_tasks will change during iteration, so creating a list of
        # task clients that need to be cancelled
        err_msg = ""
        for task_client in list(self._active_tasks.values()):
            if task_client.task_specs.cancel_on_stop:
                try:
                    task_client.cancel_task()
                except CancelTaskFailedError as e:
                    err_msg += f"{task_client.task_specs.task_name} ({repr(e)}), "
        if err_msg != "":
            raise CancelTaskFailedError(err_msg)

    def cancel_task(self, task_id: str) -> None:
        """Cancels the active task based on task ID.

        :param task_id: ID of the task to cancel
        :raises KeyError: if a task with the given id was not found.
        :raises CancelTaskFailedError: if canceling of the task fails.
        """
        task_client = self._active_tasks[task_id]
        task_client.cancel_task()

    def _active_tasks_changed(self) -> None:
        if self._active_tasks_changed_cb is not None:
            self._active_tasks_changed_cb(self._active_tasks)

    def _task_done_cb(self, _task_specs: TaskSpecs, task_details: TaskDetails) -> None:
        """Deletes the task client from active tasks as soon as the task finishes."""
        with self._active_tasks_lock:
            self._active_tasks_changed()  # Notify that the task_client status has been changed
        self._delete(task_details.task_id)
