Plancton: opportunistic computing using Docker containers
=========================================================

[![Build Status](https://travis-ci.org/mconcas/plancton.svg?branch=master)](https://travis-ci.org/mconcas/plancton)
[![PyPI version](https://badge.fury.io/py/plancton.svg)](https://badge.fury.io/py/plancton)

Plancton continuously deploys pilot Docker containers running any application
you want based on the amount of available system resources.


Main features
-------------

* **Upgrade pilot jobs to pilot containers.** Plancton is meant to run "pilot"
  containers: your container starts and tries to fetch something to do. When the
  container exits, Plancton will replace it with a brand new one. An example of
  application easy to containerize is
  [WorkQueue from cctools](https://github.com/cooperative-computing-lab/cctools)).

* **Meant for clusters.** Pilot applications are containerized and deployed on
  a cluster of nodes, each one of them running a Plancton instance. Plancton
  instances are totally independent, therefore it naturally scales.

* **Monitoring.** Sends monitoring data to [InfluxDB](https://www.influxdata.com/),
  easy to plot via [Grafana](http://grafana.org/).

* **Containers for the masses.** Plancton brings the features of Docker
  containers (environment consistency, isolation, sandboxing) to disposable
  cluster applications. Plancton is not a replacement to
  [Apache Mesos](http://mesos.apache.org/) or [Kubernetes](http://kubernetes.io/)
  but it is a very simple and lightweight alternative when you don't need all
  the extra features they offer.


Instant gratification
---------------------

[Docker](https://www.docker.com) is required, and a recent Linux operating
system.

Install the latest version with `pip`:

    pip install plancton

If you want to install from the master branch (use at your own risk):

    pip install git+https://github.com/mconcas/plancton

Plancton can be run as root or as any user with Docker privileges:

    planctonctl start


Configure
---------

The configuration file is located under `/etc/plancton/config.yaml` and it can
be modified while Plancton is running. By default it starts with an empty
configuration running dummy `busybox` containers.

You can get configurations with:

    plancton-bootstrap <gh-user/gh-repo:branch>

and they'll be downloaded to the correct place. An example dry run configuration
can be obtained with:

    plancton-bootstrap <mconcas/plancton-conf:dryrun>


Credits
-------

Credits for the name go to G.
