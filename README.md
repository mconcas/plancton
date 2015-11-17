
```bash
~#> bash -x <(curl https://raw.githubusercontent.com/mconcas/plancton/master/install) <specific-tag>  
```

# Plancton: a docker&condor-based volunteer computing project.
[![Build Status](https://travis-ci.org/mconcas/plancton.svg?branch=master)](https://travis-ci.org/mconcas/plancton)

Plancton is a *daemonized* script written in Python.  
Its aim is to provide, manage and control a pool of docker containers
([HTCondor](https://research.cs.wisc.edu/htcondor/) worker nodes) on a given volunteer host.  
Thus, with just [docker-engine](https://www.docker.com/) and [Plancton](https://github.com/mconcas/plancton) as
prerequisite on the host machine, one can share his spare CPUs providing a fully-equipped, ready-to-use, docker
container to be added in a condor pool.

Plancton is based on the [docker-py](https://github.com/docker/docker-py) Python module for API requests and on
[PyYaml](http://pyyaml.org/wiki/PyYAMLDocumentation) for configurations parsing.

## Installation - User guide

### To be installed Plancton requires:
1. **python-pip**: you can find a guide [here](http://pip.readthedocs.org/en/stable/installing/)
if it's not provided by common package managers, say `apt`, `dnf/yum`, `pacman`.  
Please notice that without python-pip you will need to manually install the  `docker-py` module and the `pyyaml`
module.
2. **git**: you find a guide [here](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git).
3. **docker**: is provided by the installation script, wrapping around the
[official](https://docs.docker.com/installation/) installation procedure.

After that you are ready to run the automated build and run script.
As `root` run:

```bash
~#> bash -x <(curl https://raw.githubusercontent.com/mconcas/plancton/master/install) <specific-tag>  
```
where `<specific-tag>` is referred to a tag of a git repository your production environment (see
   the admin guide for further information).

## Administrator Notes
###Structure of the project
The aim of this project is to provide a batch farm service in the form of a 
[HTCondor](https://research.cs.wisc.edu/htcondor/) 
cluster. In particular Plancton has the precise task to continuously spawn worker-nodes in the form of linux-containers
([LXC](https://linuxcontainers.org/))
to be added to the condor pool.
This is achieved with a certain degree of flexibility and it's done paying attention to the use of the host by the 
owner, 
that, generally, is a priority in a voluntary computing optic.

###A Condor cluster based on LXCs
Software *containerization* is quite a common practice nowadays. Provide software without worrying about dependencies 
and compatibilities with the underlying (linux) OS is comfortable, for more if the software remains in an isolated 
environment. With few tweaks one can run even graphical complex tools, the only requisite is to run a relatively 
*up-to-date* kernel version.

The **quick deployability** with low overhead and the **ease of management** of LXCs permits a *graft and prune* 
approach, where LXC Condor worker-nodes are spawned where there are available resources and *pruned*, shutted down, 
whenever the total CPU overhead exceeds a set threshold on the volunteer host.
Thus the container comes up with a *pilot* as entry point that simply starts the condor services and adds the container 
to the condor pool. After a fixed time the pilot *suicides* calling the `condor-off` command. That is if meanwhile the 
negotiator assigned a job to that container, the `--peaceful` parameter, passed to the command by default, allows to 
regularly complete the job. Otherwise the container silently disappear and is reaped by Plancton.
Please keep in mind that this approach is convincing just because it's really quick and relatively low cost to spawn 
containers compared to VMs.
As previously said Plancton constantly diagnostics the CPU efficiency and with a configurable degree of *flexibility* 
can take the decision to shut down running containers if the user reclaims the usability of his host.
Incomplete and shut down jobs are re-queued by the condor `schedd` daemon automatically.

*Jobs are killed and not stopped/paused because the majority of jobs encountered so far are* **not checkpointable**.

###Setup and configuration
**Disclaimer:** a custom installation based on forks of the original repositories needs a little work of 
reconfiguration for automatic installation.  

Three main aspects have to be configured:
*   The *container* with its required software installed on can be derived from 
[here](https://github.com/mconcas/docks/blob/master/centos6/v1/Dockerfile). Notice that the dock repository has to 
be accessible by Docker following the standard procedure. Personally i warmly suggest to host it on 
[dockerhub](https://hub.docker.com/) and to make it in sync with your Dockerfile repository triggering autobuild.
In Plancton the docks repository URL is a configurable parameter, for now 
*coded* on the head of the [`install`](https://github.com/mconcas/plancton/blob/master/install) script.    

*   The [voluntary-config](https://github.com/mconcas/voluntary-config/tree/to-infn/dev) is an example of a branch that 
stores all the Plancton and Condor configuration files. There you should configure your condor config files 
(for example the head node to point: `CONDOR_HOST`), since every container will show up with those condor configurations.
Furthermore a default Plancton configuration is purposed.

*   Plancton behavior can be configured in [this](https://github.com/mconcas/voluntary-config/blob/to-infn/dev/config.yaml) file. 




## Rationale
Since Plancton is conceived as a part of a 
**[volunteer project](https://en.wikipedia.org/wiki/Volunteer_computing)**, we should consider a set of
constraints/limitations both in project, development and production phase.  
*   First of all we have to strongly keep in mind that even the installation could be done with administration
privileges, is good practice to run software with the lowest privileges as possible, perhaps relegating the
execution to a dedicated user (e.g. plancton) and the put the installation path in a easy-to-manage *optional*
locations (e.g. `/opt/plancton`).  
I won't fully justify here what a basic common sense can here explain in terms of security.  
On the other side, since we are not able to grant a strong isolation/integrity of processes (this is due both to
   docker limitations but more trivially because the host owner has full access to his machine, thus even running
   everything as `root` would not change anything) we cannot ensure processes to remain uncorrupted during their
   execution.  
Please notice that I'm not referring to an eventual manual shutdown of containers (this kind of things are what
   condor is made to manage at integrity level and Plancton for the reliability level) but more specifically to
   an intrusion directly attempting to corrupt/compromise a running container.  
Now, it's not straightforward to do that without interrupting the normal process runtime (containers spawn with
   a non-interactive entrypoint) but I guess a truly motivated person with a lot of spare time and resources
   could achieve that.
*   In this context we have to particularly pay attention to the resources usage. Plancton is thought to
dynamically fit available resources spawning containers (the overhead amount and initialization time is dramatically
   shorter compared to, *say*, VMs).  
   These come with a pilot executable script as entry point. This script patiently waits for a job to be assigned
   for a fixed time then shut down itself. After doing the garbage collection, Plancton can spawn other
   containers, if fitting, and so on.
Thus one can grant an harmonic use of resources.  
On the other hand in case the host owner reclaims *his* computing resources Plancton automatically detect it and
more or less (customizable) quickly is able to shut down its containers to release the *cpu-shares* of the host.
*   The choice to install the HTCondor services inside the worker-container instead directly on the host carries 
with it some consequences. First of all one limit the prerequieistes just to the `docker-engine` and some python 
modules.
Moreover all the compatibility issues caused by updates (both OSs/Condor) are faced at container build-time, which
relieves the user from all the eventually modifications. 
Thanks to Condor features like **sharedport** and **flocking** one can face complex network topologies *easily* keeping 
the condor configuration almost the same along the net. 

[Credits for the name to G.]
