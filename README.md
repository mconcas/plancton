# Plancton: a docker&condor-based volunteer computing project.

Plancton is a *daemonized* script written in Python.  
Its aim is to provide, manage and control a pool of docker containers
([HTCondor](https://research.cs.wisc.edu/htcondor/) worker nodes) on a given volunteer host.  
Thus, with just [docker-engine](https://www.docker.com/) and [Plancton](https://github.com/mconcas/plancton) as
prerequisite on the host machine, one can share his spare CPUs providing a fully-equipped, ready-to-use, docker
container to be added in a condor pool.

Plancton is based on the [docker-py](https://github.com/docker/docker-py) Python module for API requests and on
[PyYaml](http://pyyaml.org/wiki/PyYAMLDocumentation) for configurations parsing.

## Installation - User guide

### To be installed, Plancton requires:
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

## Rationale
Since Plancton thought as a part of a
**[volunteer project](https://en.wikipedia.org/wiki/Volunteer_computing)**, we should consider a set of
constraints/limitations both in project, development and production phase.  
*   First of all we have to strongly keep in mind that even the installation could be done with administration
privileges is good practice to run software with the lowest privileges as possible, perhaps relegating the
execution to a dedicated user (e.g. plancton) and the put the installation path in a easy-to-manage *optional*
locations (e.g. `/opt/plancton`).  
I won't fully justify here what a basic common sense can here imply in terms of security.  
On the other side, since we are not able to grant a strong isolation/integrity of processes (this is due both to
   docker limitations but more trivially because the host owner has full access to his machine, thus even running
   everything as `root` would not change anything) we cannot ensure processes to remain untouched during their
   execution.  
Please notice that I'm not referring to an eventual manual shutdown of containers (this kind of things are what
   condor is made to manage at integrity level and Plancton for the reliability level) but more specifically to
   an intrusion directly attempting to corrupt/compromise a running container.  
Now, it's not straightforward to do that without interrupting the normal process runtime (containers spawn with
   a non-interactive entrypoint) but I guess a truly motivated person with a lot of spare time and resources
   could achieve that.
*   In this context we have to particularly pay attention to the resources usage. Plancton is thought to
dynamically fit available resources spawning containers (the overhead amount and initialization time is ridiculous
   compared to, *say*, VMs).  
   These come with a pilot executable script as entry point. This script patiently waits for a job to be assigned
   for a fixed time then shut down itself. After doing the garbage collection, Plancton can spawn other
   containers, if fitting, and so on.
Thus one can grant an harmonic use of resources.  
On the other hand in case the host owner reclaims *his* computing resources Plancton automatically detect it and
more or less (customizable) quickly is able to shut down its containers to release the *cpu-shares* of the host.

[Thanks to G. for providing Pl. a name]
