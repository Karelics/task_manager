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
"""Resolves a requested task_id down to the actual leaf task(s) to act on for pause/resume/cancel, redirecting through
composite tasks (Mission, ParallelTaskExecutor, ...), and keeps a composite's own displayed status in sync with its
children and paused flag."""

from typing import Callable, Dict, List, Optional, Tuple

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import TaskClient
from task_manager.tasks.composite_pause_tracker import CompositePauseTracker


def _composite_active_children(
    task_client: TaskClient, composites: Dict[str, "CompositePauseTracker"]
) -> Optional[List[str]]:
    """If task_client is one of the registered composite task types, returns its currently active children's task_ids
    (possibly empty).

    None if it isn't a registered composite at all.
    """
    tracker = composites.get(task_client.task_specs.task_name)
    if tracker is None:
        return None
    goal_id = task_client.goal_id  # Composites are always action-backed, so this is never None once started
    if goal_id is None:
        return []
    return tracker.get_active_children(bytes(goal_id.uuid))


def resolve_down(
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    paused_flag: Optional[bool] = None,
) -> List[str]:
    """Expands task_id down to the leaf task(s) that should actually be paused/resumed.

    A plain leaf resolves to itself. A composite (Mission, ParallelTaskExecutor, ...) resolves to its currently
    active children, expanded recursively - so composites nested inside each other (e.g. a Mission subtask that
    is itself a parallel task) are handled uniformly, regardless of depth or mix of composite types. A composite
    with no active children yet (e.g. paused right as it starts, before it has recorded any) resolves to itself.
    A child that finishes on its own between being listed as active and being resolved here (e.g. a
    service-backed task that can't actually be paused) simply contributes no leaves.

    :param task_id: The ID of the task to resolve down to its leaf tasks.
    :param active_tasks: The collection of currently active tasks.
    :param composites: A mapping from composite task names to their active children trackers.
    :param paused_flag: If not None, arms (True) or disarms (False) the own paused flag of *every* composite
        encountered while descending - not just task_id itself - before that composite's active children are
        computed, so each level is armed/disarmed deterministically ahead of any race with one of its children's
        natural completion. Arming every level (rather than only the starting point) keeps a pause entering at
        one depth (e.g. redirected up from a leaf to its immediate parent) and a later resume entering at
        another depth (e.g. the outermost composite's own task_id) from leaving an intermediate composite's flag
        stuck. None (the default) leaves every composite's paused flag untouched - used by plain resolution that
        doesn't represent an actual pause/resume request (e.g. CancelTasksService's best-effort reporting).
    :raises KeyError: if task_id itself is not an active task.
    """
    client = active_tasks.get_task_client(task_id)
    if paused_flag is not None:
        tracker = composites.get(client.task_specs.task_name)
        if tracker is not None and client.goal_id is not None:
            (tracker.request_pause if paused_flag else tracker.request_resume)(bytes(client.goal_id.uuid))

    children = _composite_active_children(client, composites)
    if not children:
        return [task_id]
    resolved = []
    for child in children:
        try:
            resolved.extend(resolve_down(child, active_tasks, composites, paused_flag))
        except KeyError:
            continue  # Vanished (finished on its own) between being listed active and being resolved.
    return resolved


def _find_enclosing_composite(
    task_id: str, active_tasks: ActiveTasks, composites: Dict[str, "CompositePauseTracker"]
) -> Optional[str]:
    """If task_id is currently a tracked active child of some other active composite task, returns that.

    composite's own task_id - one hop up. None if task_id isn't a tracked child of anything right now.

    This is what makes pausing/resuming one member of a group and pausing/resuming the group itself converge on
    the same outcome: the request gets redirected to the parent first, then `resolve_down` re-expands it back to
    every one of its currently active children.
    """
    for task_name in composites:
        for candidate in active_tasks.get_active_tasks_by_name(task_name):
            if task_id in (_composite_active_children(candidate, composites)):
                return candidate.task_details.task_id
    return None


def _resolve_start_id(task_id: str, active_tasks: ActiveTasks, composites: Dict[str, "CompositePauseTracker"]) -> str:
    """The task_id resolve_down should actually expand from: task_id's enclosing composite if it's currently a tracked
    active child of one, otherwise task_id itself."""
    enclosing = _find_enclosing_composite(task_id, active_tasks, composites)
    return enclosing if enclosing is not None else task_id


def _resolve_target_task_ids(
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    paused_flag: Optional[bool] = None,
) -> List[str]:
    """Resolves a task_id given in a pause/resume request to the full set of leaf task_ids that must actually be
    paused/resumed together.

    :param paused_flag: forwarded to resolve_down - see its docstring for what arming every composite
        encountered while descending means and why it matters.
    :raises KeyError: if task_id (or its resolved starting point) is not an active task.
    """
    return resolve_down(_resolve_start_id(task_id, active_tasks, composites), active_tasks, composites, paused_flag)


def _sync_composite_statuses(active_tasks: ActiveTasks, composites: Dict[str, "CompositePauseTracker"]) -> None:
    """Re-derives every active composite task's own displayed status, at any nesting depth: PAUSED once either its own
    paused flag is armed (see CompositePauseTracker.is_paused) or all of its currently active children are PAUSED,
    IN_PROGRESS otherwise.

    A composite's own paused flag takes priority - it's what lets a composite report PAUSED even while it has no
    active children at all (e.g. a Mission paused right between two subtasks, whose previous subtask already
    finished/vanished). Composites without such a flag of their own fall back entirely to reflecting what their
    children are doing. Runs repeated passes (bounded by the number of composites) so a multi-level nesting
    chain settles regardless of scan order.
    """
    all_composites = [client for task_name in composites for client in active_tasks.get_active_tasks_by_name(task_name)]
    for _ in range(len(all_composites) + 1):
        changed = False
        for client in all_composites:
            tracker = composites[client.task_specs.task_name]
            boundary_paused = tracker.is_paused(bytes(client.goal_id.uuid)) if client.goal_id else False

            if boundary_paused:
                if client.task_details.status != TaskStatus.PAUSED:
                    client.task_details.status = TaskStatus.PAUSED
                    changed = True
                continue

            children = _composite_active_children(client, composites)
            if not children:
                continue

            child_statuses = set()
            for child in children:
                try:
                    child_statuses.add(active_tasks.get_task_client(child).task_details.status)
                except KeyError:
                    continue  # Vanished (e.g. a service task finishing naturally mid-sync).

            if child_statuses == {TaskStatus.PAUSED} and client.task_details.status != TaskStatus.PAUSED:
                client.task_details.status = TaskStatus.PAUSED
                changed = True
            elif TaskStatus.PAUSED not in child_statuses and client.task_details.status == TaskStatus.PAUSED:
                client.task_details.status = TaskStatus.IN_PROGRESS
                changed = True
        if not changed:
            break


def pause_or_resume_group(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    start_statuses: Tuple[TaskStatus, ...],
    callback: Callable[[str], bool],
    pause: bool,
) -> bool:
    """Run function 'callback' on every task which is linked to the task with given 'task_id'.

    Finds task ids of all related tasks, eg. if the given task is part of a mission, all the mission
    tasks will be listed and then the given function will be performed for all of the tasks, unless
    the task has already finished or already in the target state.
    Finally all of the related tasks should be in the same state.

    :param task_id: the task_id of task we want to operate on.
    :param active_tasks: the ActiveTasks instance to operate on.
    :param composites: Dict of available composite task names.
    :param start_statuses: the set of statuses that a member must be in to be attempted for transition.
        Members already in the target state and members that have finished on their own are skipped.
    :param callback: the function to call for each task. Should return True
        if the transition succeeded, False if it failed.
    :param pause: True to arm every composite's own paused flag encountered while resolving down to the leaves,
        False to disarm them - see resolve_down's `paused_flag` parameter.
    :raises KeyError: if task_id (or its resolved starting point) is not an active task.
    :return: True if everything succeeded.
    """
    target_ids = _resolve_target_task_ids(task_id, active_tasks, composites, paused_flag=pause)

    success = True
    for member_id in target_ids:
        try:
            member_status = active_tasks.get_task_client(member_id).task_details.status
        except KeyError:
            continue  # Finished on its own - not a failure (matches this function's documented contract).
        if member_status not in start_statuses:
            continue  # Already in the target state, or finished on its own - nothing to do, not a failure.
        if not callback(member_id):
            success = False

    _sync_composite_statuses(active_tasks, composites)
    return success
