# Plancton

Plancton is a daemonized script written in python. It is basically an automated  
[docker-py](https://github.com/docker/docker-py) client able to manage a pool of Linux
containers known as **plancton-slaves**.
Its main task is to dynamically resize the slave pool the number of running slaves on the host,
basing on resources utilization like RAM, CPU usage, Disks availability.

In a final stable version Plancton is supposed to have:
  * Configurable policies
  *

[Thanks G. for suggesting the name]
