# Plancton: an opoortunistic computing project based on Docker containers
[![Build Status](https://travis-ci.org/mconcas/plancton.svg?branch=master)](https://travis-ci.org/mconcas/plancton)

Plancton is a lightweight daemon written in Python, with the aim to administer a set of docker containers, regardless
of the application running on top of the service (i.e. inside containers).

To work it requires the [Docker](https://www.docker.com/) engine on the host, and few more python modules (installed via `pip`):
1. [`docker-py`](https://github.com/docker/docker-py) Python module to access the Docker API interface
2. [`prettytable`](https://pypi.python.org/pypi/PrettyTable) to better format Plancton logfile
3. [`pyyaml`](http://pyyaml.org/) to parse Plancton configuration

## Installation
Plancton is installable via pip:

	$ pip install plancton

## Configuration
Plancton needs to be bootstrapped from a configuration, by design cloned from a `git` repository.
As super-user run:

	# plancton-bootstrap <repository-name:branch-tag>

### Dryrun
A dry-run example is available by running:

	# plancton-bootstrap <mconcas/plancton-conf:dryrun>

It will run a sample of a Plancton setup relying only on `busybox` pilot containers that will sleep
[Credits for the name to G.]
