# container-spawner
## Python script that keeps alive 'n' running containers on the running host.
It provides:
  * Failback and failover system: it prevents from ghost exited containers to remain untreatened or abandoned.
  * Delimited area of competence: it would never try to interact with user-owned other containers.
  * Configuration: loads manager.conf and container.json for easy-to-use configuration.
  * User permissions (at the moment)
