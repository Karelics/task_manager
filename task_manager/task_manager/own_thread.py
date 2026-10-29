#!/usr/bin/env python3

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

import functools
import threading
from typing import Any, Awaitable, Callable

# ROS
from rclpy.task import Future


def run_in_own_thread(callback: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
    """Wraps a long running blocking callback to run in a new thread of its own, outside of the executor's thread pool.

    The returned coroutine function starts the callback in a new thread and awaits an rclpy Future. The
     execute thread is handed back to the executor while the callback runs. When the callback returns or raises,
     the thread completes the Future, which makes the executor resume the execution by handling the finished Future.

    :param callback: Blocking callback to wrap
    :return: Coroutine function taking the same arguments as the callback, returning its result
    """

    @functools.wraps(callback)
    async def wrapper(*args):
        future = Future()

        def target():
            try:
                future.set_result(callback(*args))
            except Exception as e:  # pylint: disable=broad-exception-caught
                future.set_exception(e)

        threading.Thread(target=target, daemon=True).start()
        return await future

    return wrapper
