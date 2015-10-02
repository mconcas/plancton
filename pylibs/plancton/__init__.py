# -*- coding: utf-8 -*-
import base64
import docker
import errno
import json
import logging, logging.handlers
import os
import pprint
import random
import requests
import string
import time
from datetime import datetime
from daemon import Daemon
from docker import Client
import requests.exceptions
import yaml


# Unefficent Utility functions
def _pid_exists(pid):
    try:
        os.kill(pid,0)
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        elif e.errno == errno.EPERM:
            return True
    else:
        return True

def _cpu_count():
    return sum([ 1 for x in open('/proc/cpuinfo') if "bogomips" in x ]) #yolo

def _cpu_times():
    return [ float(x) for x in open('/proc/uptime').read().split(' ') ]

def _utc_time():
    return time.mktime(datetime.utcnow().timetuple())

class Plancton(Daemon):

    ## Current version of plancton
    __version__ = '0.3.2'

    ## Constructor.
    #
    #  @param name        Daemon name
    #  @param pidfile     File where PID is written
    #  @param logdir      Directory with logfiles (rotated)
    #  @param socket      Unix socket exposed by docker
    #  @param url         GitHub conf repository, we are currently using GitHub API
    def __init__(self, name, pidfile, logdir, confdir, socket='unix://var/run/docker.sock'):
        super(Plancton, self).__init__(name, pidfile)

        # Start time in UTC
        self._start_time = self._last_update_time = self._last_confup_time = _utc_time()
        # Get cputimes for resource monitoring.
        self.uptime0,self.idletime0 = _cpu_times()
        self._tolerance_counter=5
        self._logdir = logdir
        self._confdir = confdir
        self.sockpath = socket
        # CPU numbers.
        self._num_cpus = _cpu_count()
        # Requests session.
        self._https_session = requests.Session()
        # JSON container settings
        self._cont_config = None
        # docker client
        self.docker_client = Client(base_url=self.sockpath)
        # Internal status dictionary.
        self._int_st = {
            'system'     : {},
            'daemon'     : {
                'version'        : self.__version__,
                'updateinterval' : 65,
                'updateconfig'   : 3600,
                },
            'configuration' : {},
            'containers' : {}
            }
        #  flag to force a control
        self._updateflag = True
        # Overhead tolerance
        self._overhead_tol_counter = 0

    ## Setup use of logfiles, rotated and deleted periodically.
    #
    #  @return Nothing is returned.
    def _setup_log_files(self):
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
    
    ## Read configuration from file
    #
    #  @return Nothing is returned.
    def _read_conf(self):
        conf = {}
        try:
            with open(self._confdir+"/config.yaml") as fp:
                conf = yaml.safe_load(fp.read())
            self.logctl.debug(conf)
        except Exception as e:
            self.logctl.error("Cannot read configuration file %s/config.yaml: %s" % (self._confdir, e))
        self._pilot_entrypoint = conf.get("pilot_entrypoint", "/bin/bash")
        self._pilot_dock = conf.get("pilot_dock", "centos:centos6")
        self._cpus_per_dock = float(conf.get("cpus_per_dock", 1))
        ncpus = _cpu_count()
        self._max_docks = int(eval(str(conf.get("max_docks", "ncpus - 2"))))
        self._cpu_shares = self._cpus_per_dock*1024/ncpus
        self._condor_conf_list = conf.get("dock_condor_conf", [])
       	
        self.logctl.debug("Docker container: %s" % self._pilot_dock)
        self.logctl.debug("Container entrypoint: %s" % self._pilot_entrypoint)
        self.logctl.debug("CPUs per container: %f" % self._cpus_per_dock)
        self.logctl.debug("Max number of containers: %d" % self._max_docks)
        self.logctl.debug("Condor config dictionary: \n %s" % self._condor_conf_list)
        
        self._int_st['daemon']['maxcontainers'] = self._max_docks
        self._int_st['configuration'] = { 'Cmd': [ self._pilot_entrypoint ],
                                          'Image': self._pilot_dock,
                                          'HostConfig': { 'CpuShares': int(self._cpu_shares),
                                                          'NetworkMode':'bridge',
                                                          'Binds': self._condor_conf_list
                                                        }
                                        } 
        self.logctl.debug(self._int_st)

    def _uptime(self):
        return _utc_time() - self._start_time

    ## Get CPU efficiency percentage. Efficiency is calculated subtracting idletime per cpu to
    #  uptime.
    #
    #  @return zero in case of a negative efficiency.
    def _set_cpu_efficiency(self):
        curruptime,curridletime = _cpu_times()
        deltaup = curruptime - self.uptime0
        deltaidle = curridletime - self.idletime0
        eff = float((deltaup*self._num_cpus - deltaidle)*100) / float(deltaup*self._num_cpus)
        self.uptime0 = curruptime
        self.idletime0 = curridletime
        self.efficiency = eff if eff > 0 else 0.0

    def _overhead_control(self, loopthr=1, cputhreshold=70):
        self._refresh_internal_list()
        if self.efficiency > cputhreshold and self._control_containers() > 0:
            self.logctl.warning('CPU overhead exceeded the threshold at the last measurement.')
            self._overhead_tol_counter = self._overhead_tol_counter+1
            if self._overhead_tol_counter >= loopthr:
                unsrtgreylist = []
                for i,j in self._int_st['containers'].iteritems():
                    if 'running' in str(self._int_st['containers'][i]['status']):
                        startedat = time.mktime(datetime.strptime(
                            self._int_st['containers'][i]['startedat'][:19], \
                            "%Y-%m-%dT%H:%M:%S").timetuple())
                        age = startedat - _utc_time()
                        unsrtgreylist.append({ 'id' : self._int_st['containers'][i], 'age' : age })
                srtgreylist = sorted(unsrtgreylist, key=lambda k: k['age'])
                try:
                    self.docker_client.remove_container(i, force=True)
                except Exception as e:
                    self.logctl.error(e)
                else:
                    self.logctl.debug('Sacrified %s successfully.' % i)
            else:
                self._overhead_tol_counter=0

    ## Just a dummy check if the docker deamon is running.
    #
    #  @return True if the request sucess, False otherwise.
    def _get_setup_info(self):
        try:
            self._int_st['system'] = self.docker_client.info()

            return True
        except requests.exceptions.ConnectionError as e:
            self.logctl.error('Connection Error: couldn\'t find a proper socket to attach. '
                + ' Is the docker daemon running? Check if /var/run/docker.sock exists.')
            self.logctl.error(e)

            return False

    ## Fetch a specified image from a trusted registry getting image:tag from repo,tagname args or
    #  as a default, from the conf. json; if the current image is already up-to-date it continues.
    #
    #  @return True if the request sucess, False otherwise.
    def _pull_image(self, repo=None, tagname='latest'):
        if not repo:
            repository, tag = self._pilot_dock.split(':')
        else:
            repository = repo
            tag = tagname
        try:
            self.logctl.info('Pulling repository:tag %s:%s' % (repository, tag))
            self.docker_client.pull(repository,tag)

            return True
        except Exception as e:
            self.logctl.error(e)

            return False

    ## Create a container from a given image, pull it eventually. Created containers need to be
    #  started.
    #
    ## @return the container id if the request sucess, None if an exception is raised.
    def _create_container_by_name(self, jconfig, cname_prefix):
        try:
            cname = cname_prefix \
                + '-' \
                + ''.join(random.SystemRandom().choice( string.ascii_uppercase \
                + string.digits \
                + string.ascii_lowercase) for _ in range(6))

            self.logctl.debug('Creating container with name %s. ' % cname)
            self.logctl.debug('JSON DUMP: %s' % json.loads(json.dumps(self._int_st['configuration'])))
            tmpcont = self.docker_client.create_container_from_config(json.loads(json.dumps(self._int_st['configuration'])), name=cname)
        except Exception as e:
            self.logctl.error('Couldn\'t create the container! %s', e)

            return None
        else:
            self.logctl.debug('Registering it to internal dictionary. ')
            self._int_st['containers'][str(tmpcont['Id'])] = { 'name' : cname }
            self._int_st['containers'][str(tmpcont['Id'])]['status'] = 'created'

            return tmpcont

    ## Start a created container. Perform a pid inspection and return it if the container is
    #  actually running. The «container» argument is a dictionary with an 'Id' : 'hash'
    #  key : value couple.
    #
    # @return pid of container if it's successfully found running after the start. None otherwise.
    def _start_container(self, container):
        try:
            self.logctl.debug('Starting container with id: %s' % str(container['Id']))
            self.docker_client.start(container = container['Id'])
        except Exception as e:
            self.logctl.error(e)
        else:
            # make an inspect call to obtain container pid, in order to ease the monitoring.
            # Get pid.
            try:
                jj = self.docker_client.inspect_container(container['Id'])
            except Exception as e:
                self.logctl.error(e)
            if _pid_exists(jj['State']['Pid']):
                self.logctl.info('Spawned container %s with PID %s' % (str(container['Id'])[:12], \
                    jj['State']['Pid']))
                self._int_st['containers'][container['Id']]['status'] = 'running'
                self._int_st['containers'][container['Id']]['pid'] = str(jj['State']['Pid'])
                self._int_st['containers'][container['Id']]['startedat'] = \
                    str(jj['State']['StartedAt'])

                return jj['State']['StartedAt']

            else:
                self.logctl.error('Not running process found for %s with pid: %s.'
                    % (container['Id'], jj['State']['Pid']))

                return None

    ## Deploy a container.
    #
    # @return Nothing
    def _deploy_container(self, cname='plancton-slave'):
        self._start_container(self._create_container_by_name(cname_prefix=cname, \
            jconfig=self._cont_config))

    ## Update internal status.
    #
    # @return True if found owned containers. False otherwise.
    def _refresh_internal_list(self, name='plancton-slave', quiet=True):
        ret = False
        if not quiet:
            self.logctl.debug('Updating internal list, fetching containers...')
        try:
            jdata = self.docker_client.containers(all=True)
        except requests.exceptions.ConnectionError as e:
            if self._tolerance_counter > 0:
                self.logctl.warning('Couldn\'t reach docker daemon. Retrying in a minute...')
                self._tolerance_counter = self._tolerance_counter-1
                time.sleep(60)
                self._refresh_internal_list(name)
            else:
                self.logctl.error('Failed to update internal status.')
                return ret
        except Exception as e:
            self.logctl.error(e)
        else:
            self._int_st['containers'] = {}
            for i in range(0, len(jdata)):
                if name in str(jdata[i]['Names']):
                    ret = True
                    self._int_st['containers'][str(jdata[i]['Id'])] = \
                        { 'name' : str(jdata[i]['Names'][0].replace('/','')) }
                    status = jdata[i]['Status']
                    if not status:
                        self._int_st['containers'][str(jdata[i]['Id'])]['status'] = 'created'
                    elif 'Up' in status:
                        self._int_st['containers'][str(jdata[i]['Id'])]['status'] = 'running'
                        try:
                            jj = self.docker_client.inspect_container(jdata[i]['Id'])
                            # get pid
                            self._int_st['containers'][jdata[i]['Id']]['pid'] = jj['State']['Pid']
                            # get brithday
                            self._int_st['containers'][jdata[i]['Id']]['startedat'] = \
                                str(jj['State']['StartedAt'])
                        except Exception as e:
                            self.logctl.error(e)
                    elif 'Exited' in status:
                        self._int_st['containers'][jdata[i]['Id']]['status'] = 'exited'
                    elif 'Dead':
                        self._int_st['containers'][jdata[i]['Id']]['status'] =  'dead'

            return ret

    ## PPrinter for internal status. Debug purpose only.
    #
    # @return nothing
    def _dump_status(self):
        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(self._int_st)

    ## Log some informations about the self-consciousness of the containers.
    #
    # @return True if non empty dictionary. False Otherwise.
    def _print_info(self):
        self.logctl.info('-------------------------------------------------------------------')
        self.logctl.info('| n° |  container id  |  status   |         name          |  pid  |')
        self.logctl.info('-------------------------------------------------------------------')
        for i,j in self._int_st['containers'].iteritems():
            self.logctl.info( '| %s  |  %s  |  %s  | %s | %s  |' \
                % (self._int_st['containers'].keys().index(i) + 1, i[:12], j['status'], 
                j['name'], j['pid']))
        self.logctl.info('-------------------------------------------------------------------')

    ## This is the CController it gets rid of exceeded ttl or exited/created (read 'not started')
    #  containers.
    #
    # @return number of active containers.
    def _control_containers(self, name='plancton-slave', ttl_thresh_secs=12*60*60):
        self._refresh_internal_list(name)
        for i,j in self._int_st['containers'].iteritems():
            if 'running' in str(self._int_st['containers'][i]['status']):
                statobj = datetime.strptime(self._int_st['containers'][i]['startedat'][:19],
                    "%Y-%m-%dT%H:%M:%S")
                delta = _utc_time() - time.mktime(statobj.timetuple())
                if delta > ttl_thresh_secs:
                    self.logctl.info('Killing %s since it exceeded the ttl_thresh' % i )
                    try:
                        self.docker_client.remove_container(i, force=True)
                    except Exception as e:
                        self.logctl.error(e)
                    else:
                        self.logctl.debug('Removed %s successfully.' % i)
            else:
                try:
                    self.docker_client.remove_container(i, force=True)
                except Exception as e:
                    self.logctl.error(e)
                else:
                    self.logctl.debug('Removed %s successfully.' % i)
            self._refresh_internal_list(name)

        return len(self._int_st['containers'])

    ## Gracefully exiting, plancton kills all the owned containers.
    #
    # @return True if all containers are correctly deleted, False otherwise.
    def _jump_ship(self, name='plancton-slave'):
        ret = True
        self.logctl.warning('Every man for himself, abandon ship!')
        jdata = self.docker_client.containers(all=True)
        for i in range(0, len(jdata)):
            if name in str(jdata[i]['Names']):
                id = jdata[i]['Id']
                try:
                    self.docker_client.remove_container(id, force=True)
                except Exception as e:
                    self.logctl.error('Couldn\'t remove container: %s' % id)
                    self.logctl.error(e)
                    ret = False
                else:
                    self.logctl.info('\t > container id: %s out! ' % id )

        return ret

    ## Action to perform when some exit signal is received.
    #
    #  @return True if success.
    def onexit(self):
        self.logctl.info('Termination requested: we will exit gracefully soon...')
        self._do_main_loop = False
        if self._jump_ship():
            self.logctl.info('Exited gracefully, see you soon.')
            return True

        return False

    def init(self):
        self._setup_log_files()
        self._read_conf()
        self.logctl.info('---- plancton daemon v%s ----' % self.__version__)
        self._refresh_internal_list()
        self._pull_image()
        self._control_containers()
        self._get_setup_info()
        self._do_main_loop = True

    ##  Daemon's main loop.
    #   Perfroms an image pull/update at startup and every 'delta' seconds.
    #   The APIs guarantee by their own that if the image is up-to-date it wouldn't
    #   be re-downloaded, this way I want to reduce the requests number, though.
    #   Moreover once the control is set one can schedule more features, like update-checks
    #   for the cfg file, for the daemon itself and so on.
    #
    #   @return Nothing
    def main_loop(self):
        self._set_cpu_efficiency()
        whattimeisit = _utc_time()
        delta_1 = whattimeisit - self._last_confup_time
        delta_2 = whattimeisit - self._last_update_time
        self._overhead_control()
        if (delta_1 >= int(self._int_st['daemon']['updateconfig'])):
            self._pull_image()
            self._last_confup_time = _utc_time()
        self._refresh_internal_list()
        running = self._control_containers()
        self.logctl.debug('CPU efficiency: %.2f%%' % self.efficiency)
        while (self._max_docks) > int(running) and self.efficiency < 75.0:
            self._deploy_container()
            running = running+1
        self._last_update_time = _utc_time()
        self._control_containers()
        self._updateflag = False
        self._print_info()

    ##  Daemon's main function.
    #
    #  @return Exit code of the daemon: keep it in the range 0-255
    def run(self):
        self.init()
        while self._do_main_loop:
            count = 0
            self.main_loop()
            while self._do_main_loop and count < 30:
                time.sleep(1)
                count = count+1

        self.logctl.info('Exiting gracefully!')

        return 0
