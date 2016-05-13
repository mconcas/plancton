# -*- coding: utf-8 -*-
import base64
import docker
import errno
from functools import wraps
import json
import logging, logging.handlers
import os
import pprint
from prettytable import PrettyTable
import random
import requests
import string
import socket
import time
from datetime import datetime
from daemon import Daemon
from docker import Client
import docker.errors as de
import requests.exceptions as re
import yaml

def _apparmor_enabled():
    if os.path.isfile('/sys/module/apparmor/parameters/enabled'):
       with open('/sys/module/apparmor/parameters/enabled', 'r') as f:
          return True if "Y" in f.read() else False
    else: return False
      
def _min(val1, val2):
   return val1 if (val1<val2) else val2

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
    return sum([ 1 for x in open('/proc/cpuinfo') if "bogomips" in x ])

def _cpu_times():
    return [ float(x) for x in open('/proc/uptime').read().split(' ') ]

def _utc_time():
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
    def image_pull(self, repository, tag):
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
        """ Start time in UTC """
        self._start_time = self._last_update_time = self._last_confup_time = _utc_time()
        """ Get cputimes for resource monitoring """
        self.uptime0,self.idletime0 = _cpu_times()
        self._logdir = logdir
        self._confdir = confdir
        self.sockpath = socket_location
        """ CPU numbers """
        self._num_cpus = _cpu_count()
        """ Hostname """
        self._hostname = socket.gethostname().split('.')[0] 
        """ Requests session """
        self._https_session = requests.Session()
        """ JSON container settings """
        self._cont_config = None
        """ docker client """
        self.docker_client = Client(base_url=self.sockpath, version='auto')
        """ Internal status dictionary """
        self._int_st = {
            'system'     : {},
            'daemon'     : {
                'version'        : self.__version__,
                'updateinterval' : 65,
                'updateconfig'   : 3600,
                'rigidity'       : 10,
                'morbidity'      : 30
            },
            'configuration' : {},
            }
        """ Overhead tolerance """
        self._overhead_tol_counter = 0
    
    def _filtered_list(self, name, reverse=True):
        """ Get a list filtering only the running containers found.
            @return a list, or None
        """
        self.logctl.debug('<...Fetching container list...>')
        jlist = self.container_list(all=True)
        self.logctl.debug('<...Filtering container list...>')
        if jlist:
            fjlist = [d for d in jlist if (name in str(d['Names']) and 'Up' in str(d['Status'])) ]
            if fjlist:            
                self.logctl.debug('<...Sorting container list...>')
                srtlist = sorted(fjlist, key=lambda k: k['Created'], reverse=reverse)
                return srtlist
            else:
                self.logctl.debug('<...Empty PLANCTON container list found, nothing to do...>')
        else:
            self.logctl.debug('<...Empty DOCKER container list found, nothing to do...>')
        return None
    
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
        """ Read configuration from file.
            @return Nothing is returned.
        """
        conf = {}
        try:
            with open(self._confdir+"/config.yaml") as fp:
                conf = yaml.safe_load(fp.read())
            self.logctl.debug(conf)
        except Exception as e:
            self.logctl.error("Cannot read configuration file %s/config.yaml: %s" %     
                (self._confdir, e))
        self._pilot_entrypoint = conf.get("pilot_entrypoint", "/bin/bash")
        self._pilot_dock = conf.get("pilot_dock", "centos:centos6")
        self._cpus_per_dock = float(conf.get("cpus_per_dock", 1))
        ncpus = _cpu_count()
        self._max_docks = int(eval(str(conf.get("max_docks", "ncpus - 2"))))
        self._cpu_shares = self._cpus_per_dock*1024/ncpus
        privileged_ops = conf.get("pilot_privileged", False)
        condor_conf_dict = conf.get("dock_condor_conf", {})
        job_wrapper_path = conf.get("parrot_wrapper_path", {})
        self._container_bind_list = [ 
            condor_conf_dict['condor_common_conf'] + ':/etc/condor/config.d/10-common.config',
            condor_conf_dict['condor_worknode_conf'] + ':/etc/condor/config.d/00-worker.config',
            condor_conf_dict['condor_base_conf'] + ':/etc/condor/condor_config',
            job_wrapper_path + ':/etc/condor/parrot_job_wrapper.sh' ]

        self._int_st['daemon']['maxcontainers'] = self._max_docks
        self._int_st['daemon']['cputhresh'] = conf.get("cputhresh", "75")

        """ How  much the daemon will wait before updating the status of containers """
        upint = conf.get("morbidity", "medium")
        if 'soft' in upint:
           self._int_st['daemon']['morbidity'] = 120
        elif 'medium' in upint:
           self._int_st['daemon']['morbidity'] = 60
        elif 'hard' in upint:
           self._int_st['daemon']['morbidity'] = 30
        else:
           self.logctl.warning('Setting morbidity to default: hard')
           self._int_st['daemon']['morbidity'] = 30
        rigid = str(conf.get("rigidity", "soft"))
        if 'soft' in rigid:
           self._int_st['daemon']['rigidity'] = 10
        elif 'medium' in rigid:
           self._int_st['daemon']['rigidity'] = 5
        elif 'hard' in rigid:
           self._int_st['daemon']['rigidity'] = 1
        else:
           """ This should not occur """
           self.logctl.warning("Setting rigidity to default: soft.")
           self._int_st['daemon']['rigidity'] = 10
        
        self._int_st['configuration'] = {'Cmd': [ self._pilot_entrypoint ],
                                         'Image': self._pilot_dock,
                                         'HostConfig': { 'CpuShares': int(self._cpu_shares),
                                                         'NetworkMode':'bridge',
                                                         'Privileged': privileged_ops,
                                                         'Binds': self._container_bind_list
                                                       }
                                        }
        if _apparmor_enabled():
           self._int_st['configuration']['HostConfig']['SecurityOpt'] = ['apparmor:docker-allow-ptrace']
           
        self.logctl.debug(self._int_st)

    def _set_cpu_efficiency(self):
        """ Get CPU efficiency percentage. Efficiency is calculated subtracting idletime per cpu from
            uptime.
            @return zero in case of a negative efficiency.
        """
        curruptime,curridletime = _cpu_times()
        deltaup = curruptime - self.uptime0
        deltaidle = curridletime - self.idletime0
        eff = float((deltaup*self._num_cpus - deltaidle)*100) / float(deltaup*self._num_cpus)
        self.uptime0 = curruptime
        self.idletime0 = curridletime
        self.efficiency = eff if eff > 0 else 0.0

    def _overhead_control(self, name='plancton-slave', cputhreshold=70):
        """ Take decision on running containers, following config. policies.
            Please notice that there are two counters: one for the number of trial to do reaching the
            daemon, one (rigidity) for the number of loops to wait to effectively start to kill running
            containers.
           @return nothing.
        """
        if self.efficiency > cputhreshold:
            self._overhead_tol_counter = self._overhead_tol_counter+1
            self.logctl.warning('<...Latest %d measurement(s) shows a threshold exceeding...>' % \
                self._overhead_tol_counter)
            if self._overhead_tol_counter >= self._int_st['daemon']['rigidity']:
                cont_list = self._filtered_list(name=name)
                if cont_list:
                    self.logctl.debug('<...Attempting to remove container: %s...>' % \
                        cont_list[0]['Id'])
                    try:
                        self.container_remove(cont_list[0]['Id'], force=True)
                    except Exception as e:
                        self.logctl.error('<...Can\'t remove %s due to an error...>' % \
                            cont_list[0]['Id'])
                        self.logctl.error(e)
                    else:
                        self.logctl.debug('<...Removed %s successfully...>' % \
                            cont_list[0]['Id'])
                        self._overhead_tol_counter=0
                        return
                else:
                    self.logctl.debug('<...No active worker nodes found, nothing to do...>')
                    self._overhead_tol_counter=0
        else:
            self._overhead_tol_counter=0
        return

    def _get_setup_info(self):
        """ Just a dummy check if the docker deamon is running.
            @return True if the request sucess, False otherwise.
        """
        try:
            self._int_st['system'] = self.docker_info()
            return True
        except Exception, e:
            self.logctl.error('<...Failed to get docker setup info...>')
            return False

    def _pull_image(self, repo=None, tagname='latest'):
        """ Fetch a specified image from a trusted registry. Get image:tag from repo,tagname given args
            If it finds an utd image simply continue.
            @return True if the request succeeds, False otherwise.
        """
        if not repo:
            repo, tagname = self._pilot_dock.split(':')
        try:
            self.logctl.info('<...Pulling repository:tag %s:%s...>' % (repo, tagname))
            self.image_pull(repository=repo, tag=tagname)
            return True
        except Exception as e:
            self.logctl.error('<...Can\'t pull container image...> %s', e)
            return False

    def _create_container_by_name(self, cname_prefix=''):
        """ Create a container from a given image. Created containers must be started.
            @return the container id if the request sucess, None if an exception is raised.
        """
        container_unique_hash = ''.join(random.SystemRandom().choice( string.ascii_uppercase \
            + string.digits + string.ascii_lowercase) for _ in range(6))
        cname = cname_prefix + '-' + container_unique_hash
        self._int_st['configuration']['Hostname'] = 'plancton' + '-' + self._hostname + '-' + container_unique_hash
        self.logctl.debug(self._int_st['configuration'])
        self.logctl.debug('<...Creating container => %s...> ' % cname)
        try:
            tmpcont = self.container_create_from_conf(jsonconf=json.loads(json.dumps( 
            self._int_st['configuration'])), name=cname)
            return tmpcont
        except Exception as e:
            self.logctl.error('<...Couldn\'t create such a container => %s...>', e)
            return None 

    def _start_container(self, container):
        """ Start a created container. Perform a pid inspection and return it if the container is
            actually running. The container argument is a dictionary with an 'Id' : 'hash'
            key : value couple.
            @return pid of container if it's successfully found running after the start. None otherwise.
        """ 
        self.logctl.debug('<...Starting => %s...>' % str(container['Id']))
        try:
            self.container_start(id=container['Id'])
        except Exception as e:
            self.logctl.error(e)

        else:
            """ Make an inspect call to obtain container pid, in order to ease the monitoring.
                Get pid. 
            """
            try:
                jj = self.container_inspect(id=container['Id'])
            except Exception as e:
                self.logctl.error(e)
            else:
                if _pid_exists(jj['State']['Pid']):
                    self.logctl.info('<...Spawned => %s => PID: %s...>' % (str(container['Id'])[:12], \
                        jj['State']['Pid']))
                    return jj['State']['Pid']
                else:
                    self.logctl.error('No active process found for %s with pid: %s.'
                        % (container['Id'], jj['State']['Pid']))
                    return None

    def _deploy_container(self, cname='plancton-slave'):
        """ Deploy a container.
            @return Nothing 
        """
        self._start_container(self._create_container_by_name(cname_prefix=cname))
        return

    def _dump_container_list(self, cname='plancton-slave'):
        """ Log some informations about running containers.
            @return nothing.
        """
        status_table = PrettyTable(['n\'', 'docker hash', 'status', 'docker name', 'pid'])

        try:
            clist = self.container_list(all=True)
        except Exception as e:
            self.logctl.error('<...Couldn\'t get container list! %s...>', e)
        else:
            for i in range(0,len(clist)):
            	if cname in str(clist[i]['Names'][0].replace('/','')):
                    num = i+1
                    shortid = str(clist[i]['Id'])[:12]
                    status = ''
                    if 'Up' in str(clist[i]['Status']):
                        status = ' active '
                    else:
                        status = 'inactive'
                    name = str(clist[i]['Names'][0].replace('/',''))
                    pid = self.container_inspect(id=str(clist[i]['Id']))['State'].get('Pid', ' --- ')
                    if pid is 0:
                        pid = ' --- '
                    else:
                        pid = str(pid)
                    status_table.add_row([num, shortid, status, name, pid ])

        self.logctl.info('\n' + str(status_table))
        return


    def _count_containers(self, name='plancton-slave'):
        """ Count the number of tagged containers.
            @return that number.
        """
        running = 0
        try:
            clist = self.container_list(all=False)
        except Exception as e:
            self.logctl.error('<...Couldn\'t get containers list, defaultin running value to zero. \n %s...>', e)
            pass
        else:
            for i in clist:
                if 'Up' in str(i['Status']) and name in str(i['Names']):
                    running = running+1
        return running

    def _control_containers(self, name='plancton-slave', ttl_thresh_secs=12*60*60):
        """ Get rid of exceeded ttl or exited or created containers.
            @return nothing.
        """
        try:
            clist = self.container_list(all=True)
            """ safe assumption, in case of failure it won't start to spawn containers indefinitely """
        except Exception as e:
            self.logctl.error('<...Couldn\'t get containers list! %s...>', e)
        else:
            for i in clist:
            	if name in str(i['Names']):
            		## TTL threshold block
	                if 'Up' in str(i['Status']):
	                    try:
	                        insdata = self.container_inspect(i['Id'])
	                    except Exception as e:
	                        self.logctl.error('<...Couldn\'t get container informations! %s...>', e)
	                    else:
	                        statobj = datetime.strptime(insdata['State']['StartedAt'][:19], "%Y-%m-%dT%H:%M:%S")
	                        if (_utc_time() - time.mktime(statobj.timetuple())) > ttl_thresh_secs:
	                            self.logctl.info('<...Killing %s since it exceeded the ttl_thr...>' % i['Id'])
	                            try:
	                                self.container_remove(id=i['Id'], force=True)
	                            except Exception as e:
	                                """ It may happen that this command goes in racing condition with 
	                                    manual container deletion, since this is not a big deal, 
	                                    I opted for a more permissive approach.
	                                    That is not to critically stop the daemon, but simply 
	                                    wait for the next garbage collection.
					                """
	                                self.logctl.warning('<...It couldn\'t be possible to remove container with id: %s passing anyway...>' % i['Id'])
	                                self.logctl.error(e)
	                                pass
	                            else:
	                                self.logctl.info('<...Removed => %s ...>' % i['Id'])
	                ## Cleanup Exited block
	                else:
	                    try:
	                        self.logctl.debug("<...Removing => %s...>" % i['Id'])
	                        self.container_remove(id=i['Id'], force=True)
	                    except Exception as e:
	                        self.logctl.warning('<...It couldn\'t be possible to remove container with id: %s passing anyway...>' % i['Id'])
	                        self.logctl.error(e)
	                        pass
	                    else:
	                        self.logctl.info('<...Removed => %s ...>' % i['Id'])
 
    def _clean_up(self, name='plancton-slave'):
        """ Kill all the tagged (running too) containers.
            @return True if all containers are successfully wiped out, False otherwise.
        """
        ret = True
        self.logctl.warning('cleaning up all tagged containers, this may take a while...')
        try:
            clist = self.container_list(all=True)
        except Exception as e:
            self.logctl.error('<...Couldn\'t get containers list! %s...>', e)
            ret = False
            return ret
        else:
            for i in clist:
                if name in str(i['Names']):
                    try:
                        self.container_remove(id=str(i['Id']), force=True)
                    except Exception as e:
                        self.logctl.error('<...Couldn\'t remove container: %s...>' % str(i['Id']))
                        self.logctl.error(e)
                        ret = False
                    else:
                        self.logctl.info('<... => container id: %s out! ...>' % str(i['Id']))
        return ret

    def onexit(self):
        """ Action to perform when some exit signal is received.
            @return True if success.
        """
        self.logctl.info('Graceful termination requested: will exit gracefully soon...')
        self._do_main_loop = False
        
        return True
        
    def init(self):
        self._setup_log_files()
        self._read_conf()
        self.logctl.info('---- plancton daemon v%s ----' % self.__version__)
        self._pull_image()
        self._control_containers()
        self._get_setup_info()
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
        whattimeisit = _utc_time()
        delta_1 = whattimeisit - self._last_confup_time
        delta_2 = whattimeisit - self._last_update_time
        self._overhead_control()
        if (delta_1 >= int(self._int_st['daemon']['updateconfig'])):
            self._pull_image()
            self._last_confup_time = _utc_time()
        running = self._count_containers()
        self.logctl.debug('<...CPU efficiency: %.2f%%...>' % self.efficiency)
        self.logctl.debug('<...CPU available:  %.2f%%...>' % self.idle)
        self.logctl.debug('<...Potentially fitting docks: %d...>' % int(self.idle*0.95*self._num_cpus/(self._cpus_per_dock*100)))
        launchable_containers = _min(int(self.idle*0.95*self._num_cpus/(self._cpus_per_dock*100)), int(self._max_docks-running)) 
        self.logctl.debug('<...Launchable docks: %d...>' % launchable_containers)
        for i in range(launchable_containers):
           self._deploy_container()
        self._control_containers()
        self._last_update_time = _utc_time()
        self._dump_container_list()

    def run(self):
        """ Daemon's main function.
            @return Exit code of the daemon: keep it in the range 0-255
        """
        self.init()
        while self._do_main_loop:
            count = 0
            self.main_loop()
            while self._do_main_loop and count < self._int_st['daemon']['morbidity']:
                time.sleep(1)
                count = count+1

        self.logctl.info('Exiting gracefully!')

        return 0
