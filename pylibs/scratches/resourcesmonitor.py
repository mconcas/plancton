#! /bin/python

# -*- coding: utf-8 -*-
import os
import psutil as ps
from psutil import Process
import time
import pprint

_timer = getattr(time, 'monotonic', time.time)
_last_sys_cpu_times = None
_last_proc_cpu_times = None
num_cpus = pustil.cpu_count()

def timer():
    return _timer() * num_cpus

def cpu_percent_netof(pidlist, interval=None):
    blocking = interval is not None and interval > 0.0
    if blocking:
        st1 = timer()
        pt1 = psutil.cpu_times()
        time.sleep(interval)
        st2 = timer()
        pt2 = psutil.cpu_times()
    else:
        st1 = _last_sys_cpu_times
        pt1 = _last_proc_cpu_times
        st2 = timer()
        pt2 = psutil.cpu_times()
        if st1 is None or pt1 is None:
            _last_sys_cpu_times = st2
            _last_proc_cpu_times = pt2
            return 0.0
    # Deltas
    delta_proc = (pt2.user - pt1.user) + (pt2.system - pt1.system)
    delta_time = st2 - st1

def cpu_percent(self, interval=None):
    """Return a float representing the current process CPU
    utilization as a percentage.
    When interval is 0.0 or None (default) compares process times
    to system CPU times elapsed since last call, returning
    immediately (non-blocking). That means that the first time
    this is called it will return a meaningful 0.0 value.
    When interval is > 0.0 compares process times to system CPU
    times elapsed before and after the interval (blocking).
    In this case is recommended for accuracy that this function
    be called with at least 0.1 seconds between calls.
    Examples:
      >>> import psutil
      >>> p = psutil.Process(os.getpid())
      >>> # blocking
      >>> p.cpu_percent(interval=1)
      2.0
      >>> # non-blocking (percentage since last call)
      >>> p.cpu_percent(interval=None)
      2.9
      >>>
    """
    blocking = interval is not None and interval > 0.0
    num_cpus = cpu_count()
    if _POSIX:
        def timer():
            return _timer() * num_cpus
    else:
        def timer():
            return sum(cpu_times())
    if blocking:
        st1 = timer()
        pt1 = _proc.cpu_times()
        time.sleep(interval)
        st2 = timer()
        pt2 = _proc.cpu_times()
    else:
        st1 = _last_sys_cpu_times
        pt1 = _last_proc_cpu_times
        st2 = timer()
        pt2 = _proc.cpu_times()
        if st1 is None or pt1 is None:
            _last_sys_cpu_times = st2
            _last_proc_cpu_times = pt2
            return 0.0

    delta_proc = (pt2.user - pt1.user) + (pt2.system - pt1.system)
    delta_time = st2 - st1
    # reset values for next call in case of interval == None
    _last_sys_cpu_times = st2
    _last_proc_cpu_times = pt2

    try:
        # The utilization split between all CPUs.
        # Note: a percentage > 100 is legitimate as it can result
        # from a process with multiple threads running on different
        # CPU cores, see:
        # http://stackoverflow.com/questions/1032357
        # https://github.com/giampaolo/psutil/issues/474
        overall_percent = ((delta_proc / delta_time) * 100) * num_cpus
    except ZeroDivisionError:
        # interval was too low
        return 0.0
    else:
        return round(overall_percent, 1)
