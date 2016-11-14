# -*- coding: utf-8 -*-
import docker, json, pprint, requests, yaml
import base64, string, time, os, random, errno
from functools import wraps
from yaml import YAMLError
from socket import gethostname
import logging, logging.handlers
from prettytable import PrettyTable
from datetime import datetime
from daemon import Daemon
from docker import Client
import docker.errors as de
import requests.exceptions as re
import sys
import streamer

def apparmor_enabled():
  try:
    return open("/sys/module/apparmor/parameters/enabled").read().strip() == "Y"
  except IOError:
    return False

def pid_exists(pid):
  try:
    os.kill(pid, 0)
  except OSError as e:
    if e.errno != errno.EPERM:
      return False
  return True

def cpu_count():
  return int(os.sysconf("SC_NPROCESSORS_ONLN"))

def cpu_times():
  return [ float(x) for x in open('/proc/uptime').read().split(' ') ]

def utc_time():
  return time.mktime(datetime.utcnow().timetuple())

# Wrap API calls and catch exceptions to provide "robustness"
def robust(tries=5, delay=3, backoff=2):
  def robust_decorator(f):
    @wraps(f)
    def robust_call(self, *args, **kwargs):
      ltries, ldelay = tries, delay
      while ltries > 1:
        try:
          return f(self, *args, **kwargs)
        except re.ConnectionError, e:
          msg = "[%s], Failed to reach docker, retrying in %d seconds." % \
              (f.__name__, ldelay)
          self.logctl.warning(msg)
          self.logctl.warning(e)
          time.sleep(ldelay)
          ltries -= 1
          ldelay *= backoff
        except re.ReadTimeout, e: # Unresponsive docker daemon
          msg = "[%s], Failed to reach docker, retrying in %d seconds." % (f.__name__, ldelay)
          self.logctl.warning(msg)
          self.logctl.warning(e)
          time.sleep(ldelay)
          ltries -= 1
          ldelay *= backoff
        except de.APIError, e:
          msg = "[%s], Failed to successfully evade API request, retrying in %d seconds" % (f.__name__, ldelay)
          self.logctl.warning(msg)
          self.logctl.warning(e)
          time.sleep(ldelay)
          ltries -= 1
          ldelay *= backoff
        except Exception, e:
          raise
      self.logctl.error("Call [%s] failed, after %d tentatives." % (f.__name__, tries))
      return f(self, *args, **kwargs)
    return robust_call
  return robust_decorator

class Plancton(Daemon):
  __version__ = '0.5.3.2'
  @robust()
  def container_list(self, all=True):
    return self.docker_client.containers(all=all)
  @robust()
  def container_remove(self, id, force):
    return self.docker_client.remove_container(container=id, force=force)
  @robust()
  def docker_info(self):
    return self.docker_client.info
  @robust()
  def docker_pull(self, repository, tag="latest"):
    self.logctl.debug("Pulling: repo %s tag %s" % (repository, tag))
    return self.docker_client.pull(repository=repository, tag=tag)
  @robust()
  def container_create_from_conf(self, jsonconf, name):
    return self.docker_client.create_container_from_config(config=jsonconf, name=name)
  @robust()
  def container_inspect(self, id):
    return self.docker_client.inspect_container(container=id)
  @robust()
  def container_start(self, id):
    return self.docker_client.start(container=id)
  @property
  def idle(self):
   return float(100 - self.efficiency)

  # Set daemon name, pidfile, log directory and location of docker socket.
  def __init__(self, name, pidfile, logdir, confdir, socket_location='unix://var/run/docker.sock'):
    super(Plancton, self).__init__(name, pidfile)
    self._start_time = self._last_update_time = self._last_confup_time = time.time()
    self._last_kill_time = 0
    self._overhead_first_time = 0
    self.uptime0,self.idletime0 = cpu_times()
    self._logdir = logdir
    self._confdir = confdir
    self.sockpath = socket_location
    self._num_cpus = cpu_count()
    self._hostname = gethostname().split('.')[0]
    self._cont_config = None  # container configuration (dict)
    self._container_prefix = "plancton-slave"
    self.docker_client = Client(base_url=self.sockpath, version='auto')
    self.conf = {
      "influxdb_address"  : "localhost:8086",    # hostname and port of the InfluxDB interface: host:port
      "database_name"     : "plancton-monitor",  # name of the database to feed
      "database_schema"   : "http",              # schema to adopt (http/https)
      "updateconfig"      : 60,                  # frequency of config updates (s)
      "image_expiration"  : 43200,               # frequency of image updates (s)
      "main_sleep"        : 30,                  # main loop sleep (s)
      "grace_kill"        : 120,                 # kill containers after that many s over CPU threshold
      "grace_spawn"       : 60,                  # spawn containers that many seconds after last kill
      "cpus_per_dock"     : 1,                   # number of CPUs per container (non-integer)
      "max_docks"         : "ncpus - 2",         # expression to compute max number of containers
      "max_ttl"           : 43200,               # max ttl for a container (default: 12 hours)
      "docker_image"      : "busybox",           # Docker image: repository[:tag]
      "docker_cmd"        : "/bin/sleep 60",     # command to run (string or list)
      "docker_privileged" : False,               # give super privileges to the container
      "max_dock_mem"      : 2000000000,          # maximum RAM memory per container (in bytes)
      "max_dock_swap"     : 0,                   # maximum swap per container (in bytes)
      "binds"             : [],                  # list of bind mounts (all in read-only)
      "devices"           : [],                  # list of exposed devices
      "capabilities"      : [],                  # list of added capabilities (e.g. SYS_ADMIN)
      "security_opts"     : []                   # list of security options (e.g. apparmor profile)
    }
    r = self.streamer = streamer.Streamer(host=self.conf["influxdb_address"].split(':')[0], port=self.conf["influxdb_address"].split(':')[1], \
      schema=self.conf["database_schema"])

  # Get only own running containers, youngest container first if reverse=True.
  def _filtered_list(self, name, reverse=True):
    self.logctl.debug("Fetching list of Plancton running containers")
    jlist = self.container_list(all=True)
    fjlist = [ d for d in jlist if (d.get("Names", [""])[0][1:].startswith(name) and d["Status"].startswith("Up")) ]
    srtlist = sorted(fjlist, key=lambda k: k["Created"], reverse=reverse)
    return srtlist

  # Set up rotating logging system. Logfiles in: `self._logdir`.
  def _setup_log_files(self):
    if not os.path.isdir(self._logdir):
      os.mkdir(self._logdir, 0700)
    else:
      os.chmod(self._logdir, 0700)
    format = '%(asctime)s %(name)s %(levelname)s [%(module)s.%(funcName)s] %(message)s'
    datefmt = '%Y-%m-%d %H:%M:%S'
    log_file_handler = logging.handlers.RotatingFileHandler(self._logdir + '/plancton.log',
      mode='a', maxBytes=10000000, backupCount=50)
    log_file_handler.setFormatter(logging.Formatter(format, datefmt))
    log_file_handler.doRollover()
    self.logctl.setLevel(10)
    self.logctl.addHandler(log_file_handler)

  # Parse configuration file `config.yaml` and change default value if specified.
  def _read_conf(self):
    try:
      conf = yaml.safe_load(open(self._confdir+"/config.yaml").read())
    except (IOError, YAMLError) as e:
      self.logctl.error("%s/config.yaml could not be read, using previous one: %s" % (self._confdir, e))
      conf = {}
    if conf is None:
      conf = {}
    for k in self.conf:
      self.conf[k] = conf.get(k, self.conf[k])
    ncpus = cpu_count()
    self.conf["max_docks"] = int(eval(str(self.conf["max_docks"])))
    if not isinstance(self.conf["docker_cmd"], list):
      self.conf["docker_cmd"] = self.conf["docker_cmd"].split(" ")
    self.logctl.debug("Configuration:\n%s" % json.dumps(self.conf, indent=2))

  # Efficiency is calculated subtracting idletime per cpu from uptime.
  def _set_cpu_efficiency(self):
    curruptime,curridletime = cpu_times()
    deltaup = curruptime - self.uptime0
    deltaidle = curridletime - self.idletime0
    eff = float((deltaup*self._num_cpus - deltaidle)*100) / float(deltaup*self._num_cpus)
    self.uptime0 = curruptime
    self.idletime0 = curridletime
    self.efficiency = eff if eff > 0 else 0.0

  # Kill running containers exceeding a given CPU threshold.
  def _overhead_control(self):
    max_containers_cpu = 100 * self.conf["cpus_per_dock"] * min(self._count_containers(), self.conf["max_docks"]) / cpu_count()
    if max_containers_cpu and self.efficiency > max_containers_cpu+10.:
      if self._overhead_first_time == 0:
        self._overhead_first_time = time.time()
      now = time.time()
      self.logctl.warning("Above CPU threshold of %.2f%% for %d/%d s" % (max_containers_cpu, now-self._overhead_first_time, self.conf["grace_kill"]))
      if now-self._overhead_first_time > self.conf["grace_kill"]:
        cont_list = self._filtered_list(name=self._container_prefix)
        if cont_list:
          self.logctl.debug("Killing container %s" % cont_list[0]["Id"])
          try:
            self.container_remove(cont_list[0]["Id"], force=True)
            self._last_kill_time = time.time()
          except Exception as e:
            self.logctl.error("Cannot remove %s: %s", cont_list[0]["Id"], e)
          else:
            self.logctl.info('Container %s removed successfully' % cont_list[0]["Id"])
        else:
          self.logctl.debug('No workers found, nothing to do')
          self._overhead_first_time = 0
    else:
      self._overhead_first_time = 0

  # Create a container. Returns the container ID on success, None otherwise.
  def _create_container(self):
    uuid = ''.join(random.SystemRandom().choice(string.digits + string.ascii_lowercase) for _ in range(6))
    cname = self._container_prefix + '-' + uuid
    c = { "Cmd"        : self.conf["docker_cmd"],
          "Image"      : self.conf["docker_image"],
          "Hostname"   : "plancton-%s-%s" % (self._hostname[:40], uuid),
          "HostConfig" : { "CpuQuota"    : int(self.conf["cpus_per_dock"]*100000.),
                           "CpuPeriod"   : 100000,
                           "NetworkMode" : "bridge",
                           "SecurityOpt" : self.conf["security_opts"] if apparmor_enabled() else [],
                           "Binds"       : [ x+":ro,Z" for x in self.conf["binds"] ],
                           "Memory"      : self.conf["max_dock_mem"],
                           "MemorySwap"  : self.conf["max_dock_mem"] + self.conf["max_dock_swap"],
                           "Privileged"  : self.conf["docker_privileged"],
                           "Devices"     : [ dict(zip([ "PathOnHost", "PathInContainer",
                                                        "CgroupPermissions" ], x.split(":", 2)))
                                             for x in self.conf["devices"] ],
                           "CapAdd"      : self.conf["capabilities"]
                         }
        }
    self.logctl.debug("Container definition for %s:\n%s" % (cname, json.dumps(c, indent=2)))
    try:
      return self.container_create_from_conf(jsonconf=c, name=cname)
    except Exception as e:
      self.logctl.error("Cannot create container: %s", e)
      return None

  # Start a created container. Return PID it if the container is actually running.
  def _start_container(self, container):
    if container is None:
      return None
    self.logctl.debug("Starting %s" % str(container["Id"]))
    try:
      self.container_start(id=container["Id"])
    except Exception as e:
      self.logctl.error(e)
      return None
    try:
      jj = self.container_inspect(id=container["Id"])
    except Exception as e:
      self.logctl.error(e)
      return None
    if pid_exists(jj['State']['Pid']):
      self.logctl.info('Spawned %s (main PID: %s)' % (str(container["Id"])[:12], jj["State"]["Pid"]))
      return jj['State']['Pid']
    else:
      self.logctl.error('No active process found for %s with PID %s.' % (container["Id"], jj['State']['Pid']))
      return None

  # Pretty print the statuses of controlled containers.
  def _dump_container_list(self):
    status_table = PrettyTable(['n\'', 'docker hash', 'status', 'docker name', '   pid   '])
    status_table.align["   pid   "] = "r"
    try:
      clist = self.container_list(all=True)
    except Exception as e:
      self.logctl.error("Couldn't get container list: %s", e)
    num = 0
    for c in clist:
      if c.get("Names", [""])[0][1:].startswith(self._container_prefix):
        num = num+1
        shortid = c['Id'][:12]
        status = c['Status'].lower()
        name = c['Names'][0][1:]
        pid = self.container_inspect(id=c['Id'])['State'].get('Pid', 0)
        pid = "" if pid == 0 else str(pid)
        status_table.add_row([num, shortid, status, name, pid])
    self.logctl.info('Container list:\n' + str(status_table))

  # Return the number of running containers.
  def _count_containers(self):
    try:
      clist = self.container_list(all=False)
    except Exception as e:
      self.logctl.error("Couldn't get containers list, defaulting running value to zero: %s", e)
      return 0
    return len([ x for x in clist if x.get("Status", "").startswith("Up")
                   and x.get("Names", [""])[0][1:].startswith(self._container_prefix) ])

  # Clean up dead or stale containers.
  def _control_containers(self):
    try:
      clist = self.container_list(all=True)
    except Exception as e:
      self.logctl.error("Couldn't get containers list: %s", e)
      return
    for i in clist:
      cont_uptime = 0.
      if not i.get("Names", [""])[0][1:].startswith(self._container_prefix):
        self.logctl.debug("Ignoring container %s", i.get("Names", [""])[0][1:])
        continue
      to_remove = False
      # TTL threshold block
      if "running" in i['State']:
        try:
          insdata = self.container_inspect(i["Id"])
        except Exception as e:
          self.logctl.error("Couldn't get container information! %s", e)
        else:
          statobj = datetime.strptime(insdata['State']['StartedAt'][:19], "%Y-%m-%dT%H:%M:%S")
          cont_uptime = utc_time() - time.mktime(statobj.timetuple())
          if cont_uptime > self.conf["max_ttl"]:
            self.logctl.info('Killing %s since it exceeded the max TTL', i['Id'])
            self.streamer.write_pt(db=self.conf["database_name"], name="container", \
                tag_dict={"hostname": self._hostname, "started": True}, field_dict={"uptime": cont_uptime})
            to_remove = True
          else:
            self.logctl.debug("Container %s is below its maximum TTL, leaving it alone", i["Id"])
      else:
        self.logctl.info("Killing %s as it has a bad status (%s)", i["Id"], i["Status"])
        if "exited" in i["State"]:
          try:
            insdata = self.container_inspect(i["Id"])
          except Exception as e:
            self.logctl.error("Couldn't get container information! %s", e)
          else:
            statobj_start = datetime.strptime(insdata['State']['StartedAt'][:19], "%Y-%m-%dT%H:%M:%S")
            statobj_end = datetime.strptime(insdata['State']['FinishedAt'][:19], "%Y-%m-%dT%H:%M:%S")
            cont_uptime = time.mktime(statobj_end.timetuple()) - time.mktime(statobj_start.timetuple())
            self.streamer.write_pt(db=self.conf["database_name"], name="container", \
              tag_dict={"hostname": self._hostname, "started": True}, field_dict={"uptime": cont_uptime})
        if "created" in i["State"]:
          self.streamer.write_pt(db=self.conf["database_name"], name="container", \
            tag_dict={"hostname": self._hostname, "started": False}, field_dict={"uptime": 0})
        to_remove = True

      if to_remove:
        try:
          self.container_remove(id=i['Id'], force=True)
        except Exception as e:
          self.logctl.warning('It was not possible to remove container with id %s: %s', i['Id'], e)
        else:
          self.logctl.info("Removed container %s", i["Id"])

  def onexit(self):
    self.logctl.info('Graceful termination requested: will exit gracefully soon.')
    self._do_main_loop = False
    return True

  def init(self):
    self.logctl.info('---- plancton daemon v%s ----' % self.__version__)
    self._setup_log_files()
    self._read_conf()
    self.logctl.debug("Attempting to create an InfluxDB database \"%s\": at %s:%s" % \
      (self.conf["database_name"], self.conf["influxdb_address"].split(':')[0], self.conf["influxdb_address"].split(':')[1]))
    self.streamer.create_db(self.conf["database_name"])
    self.docker_pull(*self.conf["docker_image"].split(":", 1))
    self._control_containers()
    self._do_main_loop = True

  # Main loop, do comparison between uptime and thresholds sets for updates.
  def main_loop(self):
    self._set_cpu_efficiency()
    now = time.time()
    delta_config = now - self._last_confup_time
    delta_update = now - self._last_update_time
    self._overhead_control()
    prev_img = self.conf["docker_image"]
    if delta_config >= int(self.conf['updateconfig']):
      self._read_conf()
      self._last_confup_time = time.time()
    if prev_img != self.conf["docker_image"] or delta_update >= int(self.conf['image_expiration']):
      self.docker_pull(*self.conf["docker_image"].split(":", 1))
      self._last_update_time = time.time()
    running = self._count_containers()
    self.logctl.debug('CPU used: %.2f%%, available: %.2f%%' % (self.efficiency, self.idle))
    r = self.streamer.write_pt(db=self.conf["database_name"], name="measurement", tag_dict={"hostname": self._hostname}, \
       field_dict={"cpu_eff": self.efficiency, "containers": running})
    self.logctl.debug("Sending data to db \"%s\": %s" % (self.conf["database_name"], r))
    fitting_docks = int(self.idle*0.95*self._num_cpus/(self.conf["cpus_per_dock"]*100))
    launchable_containers = min(fitting_docks, max(self.conf["max_docks"]-running, 0))
    self.logctl.debug('Potentially fitting containers based on CPU utilisation: %d', fitting_docks)
    if now-self._last_kill_time > self.conf["grace_spawn"]:
      self.logctl.info('Will launch %d new container(s)' % launchable_containers)
      for _ in range(launchable_containers):
        self._start_container(self._create_container())
    elif launchable_containers > 0:
      self.logctl.info("Not launching %d containers: too little time elapsed after last kill" % launchable_containers)
    self._control_containers()
    self._last_update_time = time.time()
    self._dump_container_list()

  # Main daemon function. Return is in the range 0-255.
  def run(self):
    self.init()
    while self._do_main_loop:
      count = 0
      self.main_loop()
      self.logctl.debug("Sleeping %d seconds..." % self.conf["main_sleep"])
      while self._do_main_loop and count < self.conf["main_sleep"]:
        time.sleep(1)
        count = count+1
    self.logctl.info("Exiting gracefully.")
    return 0
