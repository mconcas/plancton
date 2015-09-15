# Plancton

Plancton is a daemonized script written in python. It is basically an automated  
[docker-py](https://github.com/docker/docker-py) client able to manage a pool of Linux
containers known as **plancton-slaves**.
Its main task is to dynamically manage workers pool size basing on CPU usage on the host and keep order of running/exited/dead container.
This is thought for a voluntary computing project where resources (i.e. CPUshares, disks, vmemory) are shared with the host regular users. That is jobs execution must not interfere, at the level of resources usage, with normal users' sessions.

[Thanks G. for suggesting the name]
