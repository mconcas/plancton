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

def robust(tries=5, delay=3, backoff=2):
    """ Decorator to catch requests.ConnectionError exceptions, fully customizable, its main aim is to
        manage racing conditions and a, for some reason, unresponsive docker daemon situations.
    """
    def robust_decorator(f):
        @wraps(f)
        def robust_call(self, *args, **kwargs):
            ltries, ldelay = tries, delay
            while ltries > 1:
                try:
                    return f(self, *args, **kwargs)
                except re.ConnectionError, e:
                    msg = "[%s], Failed to reach docker, Retrying in %d seconds..." % \
                        (f.__name__, ldelay)
                    self.logctl.warning(msg)
                    self.logctl.warning(e)
                    time.sleep(ldelay)
                    ltries -= 1
                    ldelay *= backoff
                except re.ReadTimeout, e:
                    msg = "[%s], Failed to reach docker, Retrying in %d seconds..." % \
                       (f.__name__, ldelay)
                    self.logctl.warning(msg)
                    self.logctl.warning(e)
                    time.sleep(ldelay)
                    ltries -= 1
                    ldelay *= backoff
                except de.APIError, e:
                    msg = "[%s], Failed to successfully evade API request, Retrying in %d seconds..." % \
                       (f.__name__, ldelay)
                    self.logctl.warning(msg)
                    self.logctl.warning(e)
                    time.sleep(ldelay)
                    ltries -= 1
                    ldelay *= backoff
                except Exception, e:
                    raise
            self.logctl.error('Couldn\'t make a [%s] request to the docker daemon... exiting.' %
                f.__name__)
            return f(self, *args, **kwargs)
        return robust_call
    return robust_decorator

class Plancton(Daemon):
    __version__ = '0.3.2'
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

    def __init__(self, name, pidfile, logdir, confdir, socket_location='unix://var/run/docker.sock'):
        """ Constructor
             @param name                 Name of the Daemon
             @param pidfile              File where PID is written
             @param logdir               Directory with logfiles (rotated)
             @param socket_location      Unix socket exposed by docker
        """
        super(Plancton, self).__init__(name, pidfile)
        self._start_time = self._last_update_time = self._last_confup_time = time.time()
        self.uptime0,self.idletime0 = cpu_times()
        self._logdir = logdir
        self._confdir = confdir
        self.sockpath = socket_location
        self._num_cpus = cpu_count()
        self._hostname = gethostname().split('.')[0]
        self._cont_config = None  # container configuration (dict)
        self._container_prefix = "plancton-slave"
        self.docker_client = Client(base_url=self.sockpath, version='auto')
        self._int_st = {
          "cputhresh"         : 100,             # percentage of all CPUs allotted to Plancton
          "updateconfig"      : 60,              # frequency of config updates (s)
          "image_expiration"  : 43200,           # frequency of image updates (s)
          "morbidity"         : 30,              # main loop sleep (s)
          "rigidity"          : 10,              # kill containers after that many times over cputhresh
          "cpus_per_dock"     : 1,               # number of CPUs per container (non-integer)
          "max_docks"         : "ncpus - 2",     # expression to compute max number of containers
          "max_ttl"           : 43200,           # max ttl for a container (default: 12 hours)
          "docker_image"      : "busybox",       # Docker image: repository[:tag]
          "docker_cmd"        : "/bin/sleep 10", # command to run (string or list)
          "docker_privileged" : False,           # give super privileges to the container
          "binds"             : []               # list of bind mounts (all in read-only)
        }
        self._overhead_tol_counter = 0

    # Get only own running containers, youngest container first if reverse=True.
    def _filtered_list(self, name, reverse=True):
      self.logctl.debug("Fetching list of Plancton running containers")
      jlist = self.container_list(all=True)
      fjlist = [ d for d in jlist if (d["Names"][0][1:].startswith(name) and d["Status"].startswith("Up")) ]
      srtlist = sorted(fjlist, key=lambda k: k["Created"], reverse=reverse)
      return srtlist

    def _setup_log_files(self):
        """ Setup use of logfiles, rotated and deleted periodically.
            @return Nothing is returned.
        """
        if not os.path.isdir(self._logdir):
            os.mkdir(self._logdir, 0700)
        else:
            os.chmod(self._logdir, 0700)
        format = '%(asctime)s %(name)s %(levelname)s [%(module)s.%(funcName)s] %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S'
        log_file_handler = logging.handlers.RotatingFileHandler(self._logdir + '/plancton.log',
            mode='a', maxBytes=1000000, backupCount=5)
        log_file_handler.setFormatter(logging.Formatter(format, datefmt))
        log_file_handler.doRollover()
        self.logctl.setLevel(10)
        self.logctl.addHandler(log_file_handler)

    def _read_conf(self):
      try:
        conf = yaml.safe_load(open(self._confdir+"/config.yaml").read())
      except (IOError, YAMLError) as e:
        self.logctl.error("%s/config.yaml could not be read, using previous one: %s" % (self._confdir, e))
        return
      for k in self._int_st:
        self._int_st[k] = conf.get(k, self._int_st[k])

      for k,v in { "soft": 120, "medium": 60, "hard": 30 }.items():
        if self._int_st["morbidity"] == k:
          self._int_st["morbidity"] = v

      for k,v in { "soft": 10, "medium": 5, "hard": 1 }.items():
        if self._int_st["rigidity"] == k:
          self._int_st["rigidity"] = v

      ncpus = cpu_count()
      self._int_st["max_docks"] = int(eval(str(self._int_st["max_docks"])))

      if not isinstance(self._int_st["docker_cmd"], list):
        self._int_st["docker_cmd"] = self._int_st["docker_cmd"].split(" ")

      self.logctl.debug("Configuration:\n%s" % json.dumps(self._int_st, indent=2))

    def _set_cpu_efficiency(self):
        """ Get CPU efficiency percentage. Efficiency is calculated subtracting idletime per cpu from
            uptime.
            @return zero in case of a negative efficiency.
        """
        curruptime,curridletime = cpu_times()
        deltaup = curruptime - self.uptime0
        deltaidle = curridletime - self.idletime0
        eff = float((deltaup*self._num_cpus - deltaidle)*100) / float(deltaup*self._num_cpus)
        self.uptime0 = curruptime
        self.idletime0 = curridletime
        self.efficiency = eff if eff > 0 else 0.0

    # Kill running containers exceeding a given CPU threshold.
    def _overhead_control(self):
      max_used_cpu = 100 * self._int_st["cpus_per_dock"] * min(self._count_containers(), self._int_st["max_docks"]) / cpu_count()
      real_cputhresh = max(self._int_st["cputhresh"], max_used_cpu)
      self.logctl.debug("Considering a threshold of %.2f%% of all CPUs", real_cputhresh)
      if self.efficiency > real_cputhresh:
        self._overhead_tol_counter = self._overhead_tol_counter+1
        self.logctl.warning("CPU threshold trespassed for %d consecutive time(s) out of " % \
                            (self._overhead_tol_counter, self._int_st["rigidity"]))
        if self._overhead_tol_counter >= self._int_st['rigidity']:
          cont_list = self._filtered_list(name=self._container_prefix)
          if cont_list:
            self.logctl.debug("Attempting to remove container: %s" % cont_list[0]['Id'])
            try:
              self.container_remove(cont_list[0]['Id'], force=True)
            except Exception as e:
              self.logctl.error("Cannot remove %s: %s", cont_list[0]["Id"], e)
            else:
              self.logctl.info('Container %s removed successfully' % cont_list[0]['Id'])
          else:
            self.logctl.debug('No workers found, nothing to do')
            self._overhead_tol_counter = 0
      else:
        self._overhead_tol_counter = 0

    # Create a container. Returns the container ID on success, None otherwise.
    def _create_container(self):
      uuid = ''.join(random.SystemRandom().choice(string.digits + string.ascii_lowercase) for _ in range(6))
      cname = self._container_prefix + '-' + uuid
      c = { "Cmd"        : self._int_st["docker_cmd"],
            "Image"      : self._int_st["docker_image"],
            "Hostname"   : "plancton-%s-%s" % (self._hostname, uuid),
            "HostConfig" : { "CpuShares"   : self._int_st["cpus_per_dock"]*1024/cpu_count(),
                             "NetworkMode" : "bridge",
                             "SecurityOpt" : ["apparmor:docker-allow-ptrace"] if apparmor_enabled() else [],
                             "Binds"       : [ x+":ro,Z" for x in self._int_st["binds"] ],
                             "Privileged"  : self._int_st["docker_privileged"] }
          }
      #"Binds": self._container_bind_list
      self.logctl.debug("Container definition for %s:\n%s" % (cname, json.dumps(c, indent=2)))
      try:
        return self.container_create_from_conf(jsonconf=c, name=cname)
      except Exception as e:
        self.logctl.error("Cannot create container: %s", e)
        return None

    # Start a created container. Perform a PID inspection, return it if the container is really
    # running.
    def _start_container(self, container):
      self.logctl.debug("Starting %s" % str(container['Id']))
      try:
        self.container_start(id=container['Id'])
      except Exception as e:
        self.logctl.error(e)
        return None

      try:
        jj = self.container_inspect(id=container['Id'])
      except Exception as e:
        self.logctl.error(e)
        return None

      if pid_exists(jj['State']['Pid']):
        self.logctl.info('Spawned %s (main PID: %s)' % (str(container['Id'])[:12], jj['State']['Pid']))
        return jj['State']['Pid']
      else:
        self.logctl.error('No active process found for %s with PID %s.' % (container['Id'], jj['State']['Pid']))
        return None

    def _dump_container_list(self):
      status_table = PrettyTable(['n\'', 'docker hash', 'status', 'docker name', 'pid'])
      try:
        clist = self.container_list(all=True)
      except Exception as e:
        self.logctl.error("Couldn't get container list: %s", e)
      num = 0
      for c in clist:
        if c['Names'][0][1:].startswith(self._container_prefix):
          num = num+1
          shortid = c['Id'][:12]
          status = " active " if c['Status'].startswith("Up") else "inactive"
          name = c['Names'][0][1:]
          pid = self.container_inspect(id=c['Id'])['State'].get('Pid', 0)
          pid = " --- " if pid == 0 else str(pid)
          status_table.add_row([num, shortid, status, name, pid])
      self.logctl.info('Container list:\n' + str(status_table))

    def _count_containers(self):
      try:
        clist = self.container_list(all=False)
      except Exception as e:
        self.logctl.error("Couldn't get containers list, defaulting running value to zero: %s", e)
        return 0
      return len([ x for x in clist if x["Status"].startswith("Up") ])

    # Clean up dead or stale containers.
    def _control_containers(self):
      try:
        clist = self.container_list(all=True)
      except Exception as e:
        self.logctl.error("Couldn't get containers list: %s", e)
        return

      for i in clist:
        if not i['Names'][0][1:].startswith(self._container_prefix):
          self.logctl.debug("Ignoring container %s", i["Names"][0])
          continue

        to_remove = False

        ## TTL threshold block
        if i['Status'].startswith("Up"):
          try:
            insdata = self.container_inspect(i['Id'])
          except Exception as e:
            self.logctl.error("Couldn't get container information! %s", e)
          else:
            statobj = datetime.strptime(insdata['State']['StartedAt'][:19], "%Y-%m-%dT%H:%M:%S")
            if (utc_time() - time.mktime(statobj.timetuple())) > self._int_st["max_ttl"]:
              self.logctl.info('Killing %s since it exceeded the max TTL', i['Id'])
              to_remove = True
            else:
              self.logctl.debug("Container %s is below its maximum TTL, leaving it alone", i["Id"])
        else:
          self.logctl.info("Killing %s as it has a bad status (%s)", i["Id"], i["Status"])
          to_remove = True

        if to_remove:
          try:
            self.container_remove(id=i['Id'], force=True)
          except Exception as e:
            self.logctl.warning('It was not possible to remove container with id %s: %s', i['Id'], e)
          else:
            self.logctl.info('Removed container %s', i['Id'])

    def onexit(self):
        self.logctl.info('Graceful termination requested: will exit gracefully soon...')
        self._do_main_loop = False
        return True

    def init(self):
        self.logctl.info('---- plancton daemon v%s ----' % self.__version__)
        self._setup_log_files()
        self._read_conf()
        self.docker_pull(*self._int_st["docker_image"].split(":", 1))
        self._control_containers()
        self._do_main_loop = True

    def main_loop(self):
        """ Daemon's main loop.
            Performs an image pull/update at startup and every 'delta' seconds.
            The APIs guarantee by their own that if the image is up-to-date it wouldn't
            be re-downloaded, this way I want to reduce the requests number, though.
            Moreover once the control is set one can schedule more features, like update-checks
            for the cfg file, for the daemon itself and so on.
            @return Nothing
        """
        self._set_cpu_efficiency()
        now = time.time()
        delta_config = now - self._last_confup_time
        delta_update = now - self._last_update_time
        self._overhead_control()
        if delta_update >= int(self._int_st['image_expiration']):
          self._pull_image()
          self._last_update_time = time.time()
        if delta_config >= int(self._int_st['updateconfig']):
          self._read_conf()
          self._last_confup_time = time.time()
        running = self._count_containers()
        self.logctl.debug('CPU efficiency: %.2f%%' % self.efficiency)
        self.logctl.debug('CPU available:  %.2f%%' % self.idle)
        fitting_docks = int(self.idle*0.95*self._num_cpus/(self._int_st["cpus_per_dock"]*100))
        self.logctl.debug('Potentially fitting containers based on CPU utilisation: %d', fitting_docks)
        launchable_containers = min(fitting_docks, int(self._int_st["max_docks"]-running))
        self.logctl.info('Will launch %d new container(s)' % launchable_containers)
        for _ in range(launchable_containers):
          self._start_container(self._create_container())
        self._control_containers()
        self._last_update_time = time.time()
        self._dump_container_list()

    # Main daemon function. Return is in the range 0-255.
    def run(self):
      self.init()
      while self._do_main_loop:
        count = 0
        self.main_loop()
        while self._do_main_loop and count < self._int_st["morbidity"]:
          time.sleep(1)
          count = count+1
      self.logctl.info("Exiting gracefully!")
      return 0
