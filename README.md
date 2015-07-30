# plancton
## Python script that keeps alive a specified number of running containers on the docker host.
Prerequisites:
  * requests_unixsocket:
  
           pip install --user requests_unixsocket


It provides:
  * Api (v1.19) used to interact with the Docker daemon.
  * Failback and failover system: it prevents from ghost exited containers to remain untreatened or abandoned, moreover it can      load a backup from a .reg file only if it has right name and right permissions.
  * Delimited area of competence: it would never try to interact with user-owned other containers.
  * Configuration: loads manager.conf and container.json for easy-to-use configuration.
  * User permissions (at the moment)
