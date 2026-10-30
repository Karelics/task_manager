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
children and paused flag.

Pause-as-a-unit tasks ("units", e.g. Container) are deliberately *not* composites here: resolution never descends into
them, so they are paused/resumed as a whole leaf. They are only consulted to redirect a pause/resume of one of their
(possibly deeply nested) children up to the outermost such unit.
"""

from typing import Callable, Dict, List, Optional, Tuple

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import TaskClient
from task_manager.tasks.composite_pause_tracker import ActiveChildrenProvider, CompositePauseTracker


def _composite_active_children(
    task_client: TaskClient, composites: Dict[str, "ActiveChildrenProvider"]
) -> Optional[List[str]]:
    """If task_client is one of the registered composite (or unit) task types, returns its currently active children's
    task_ids (possibly empty).

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
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    units: Optional[Dict[str, "ActiveChildrenProvider"]] = None,
) -> Optional[str]:
    """Return the task_id of the active composite (or unit) that currently tracks task_id as one of its active children,
    one hop up, or None if no such composite exists right now."""
    providers = {**composites, **(units or {})}
    for task_name in providers:
        for candidate in active_tasks.get_active_tasks_by_name(task_name):
            if task_id in (_composite_active_children(candidate, providers)):
                return candidate.task_details.task_id
    return None


def _find_outermost_unit(
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    units: Dict[str, "ActiveChildrenProvider"],
) -> Optional[str]:
    """Walks up from task_id (inclusive) through every enclosing composite/unit and returns the task_id of the outermost
    unit on that chain, or None if task_id isn't (inside) a unit.

    Walking all the way up, rather than one hop, matters for children nested deeper inside a unit (e.g.
    Container[nav, Parallel[x, y]]) - pausing x must still pause the whole container, not just the parallel task.
    """
    outermost = None
    current: Optional[str] = task_id
    visited = set()
    while current is not None and current not in visited:
        visited.add(current)
        try:
            if active_tasks.get_task_client(current).task_specs.task_name in units:
                outermost = current
        except KeyError:
            pass  # Not active (anymore) - can't be a unit, but keep walking up in case its parent still is.
        current = _find_enclosing_composite(current, active_tasks, composites, units)
    return outermost


def _resolve_start_id(
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    units: Optional[Dict[str, "ActiveChildrenProvider"]] = None,
) -> str:
    """The task_id resolve_down should actually expand from: task_id's enclosing composite if it's currently a tracked
    active child of one, otherwise task_id itself.

    If task_id is (nested in) a unit, the outermost such unit takes task_id's place first, so the unit is paused as a
    whole and its enclosing composite (if any) is still redirected to exactly as for any other child - keeping e.g. a
    Mission's paused flag consistent no matter whether the Mission, the container or the container's child is named.
    """
    if units:
        outermost_unit = _find_outermost_unit(task_id, active_tasks, composites, units)
        if outermost_unit is not None:
            task_id = outermost_unit
    enclosing = _find_enclosing_composite(task_id, active_tasks, composites)
    return enclosing if enclosing is not None else task_id


def _resolve_target_task_ids(
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "CompositePauseTracker"],
    paused_flag: Optional[bool] = None,
    units: Optional[Dict[str, "ActiveChildrenProvider"]] = None,
) -> List[str]:
    """Resolves a task_id given in a pause/resume request to the full set of leaf task_ids that must actually be
    paused/resumed together.

    :param paused_flag: forwarded to resolve_down - see its docstring for what arming every composite
        encountered while descending means and why it matters.
    :param units: pause-as-a-unit task types (e.g. Container) - see _resolve_start_id.
    :raises KeyError: if task_id (or its resolved starting point) is not an active task.
    """
    start_id = _resolve_start_id(task_id, active_tasks, composites, units)
    return resolve_down(start_id, active_tasks, composites, paused_flag)


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
    paused_by: Optional[str] = None,
    units: Optional[Dict[str, "ActiveChildrenProvider"]] = None,
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
    :param paused_by: the identifier of the entity requesting the pause or reason for pausing, if applicable.
    :param units: pause-as-a-unit task types (e.g. Container), paused/resumed as a whole - see _resolve_start_id.
    :raises KeyError: if task_id (or its resolved starting point) is not an active task.
    :return: True if everything succeeded.
    """
    target_ids = _resolve_target_task_ids(task_id, active_tasks, composites, paused_flag=pause, units=units)

    success = True
    for member_id in target_ids:
        try:
            member_status = active_tasks.get_task_client(member_id).task_details.status
        except KeyError:
            continue  # Finished on its own - not a failure (matches this function's documented contract).
        if member_status not in start_statuses:
            continue  # Already in the target state, or finished on its own - nothing to do, not a failure.
        if not callback(member_id, paused_by):
            success = False

    _sync_composite_statuses(active_tasks, composites)
    return success
