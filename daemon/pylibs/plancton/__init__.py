# -*- coding: utf-8 -*-

import requests
import docker
import json
from docker import Client
from daemon import Daemon
from datetime import datetime
import os, time
import logging, logging.handlers
import base64
import string
import random




class Plancton(Daemon):

    ## Current version of plancton
    __version__ = '0.0.1'

    ## Constructor.
    #
    #  @param name        Daemon name
    #  @param pidfile     File where PID is written
    #  @param logdir      Directory with logfiles (rotated)
    #  @param sock_path   Unix socket exposed by docker

    def __init__(self, name, pidfile, logdir, sock_path='unix://var/run/docker.sock'):
        super(Plancton, self).__init__(name, pidfile)
        self._logdir = logdir

        ## requests session needed by GitHub APIs.
        self._https_session = requests.Session()
        self._conf_container_js = None
        self._sockpath = sock_path


    ## Setup use of logfiles, rotated and deleted periodically.
    #
    #  @return Nothing is returned
    def SetupLogFiles(self):
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


    ## Downloads the configuration file of a working container.
    #
    #  @return Nothing is returned
    def GetOnlineConf(self,
        cfg_url='https://api.github.com/repos/mconcas/plancton/contents/conf/worker_centos6.json'):

        # docker client
        self._client = Client(base_url=self._sockpath)
        try:
            httpsrequest = self._https_session.get(cfg_url)

            # Get a base64 string containing the container json configuration.
            # If the string is non-empty decode the json content.
            enccfg = json.loads(httpsrequest.content)['content']
            if enccfg:
                self._conf_container_js = json.loads(base64.b64decode(enccfg))
            else:
                self.logctl.warning('Empty cfg. string, skipping online initialization...')
            httpsrequest.raise_for_status()

        except Exception as e:

            self.logctl.error('Failed to obtain configuration online, skipping...')
            self.logctl.error(e)

    def GetSetupInfo(self):
        # This is just a dummy check if the docker deamon is running.
        # System+Docker-setup informations are stored.
        try:
            self._setupinfo = self._client.info()
        except requests.exceptions.ConnectionError as e:
            self.logctl.error('Connection Error: couldn\'t find a proper socket to attach. '
                + ' Is the docker daemon running? Check if /var/run/docker.sock exists.')
            self.logctl.error(e)

    def CreateContainer(self, cname_prefix, pull_img=True):
        if self._conf_container_js:
            if pull_img:
                repository, tag = str(self._conf_container_js['Image']).split(':')
                self.logctl.info('Pulling img: %s, tag: %s' % (repository, tag))
                self._client.pull(repository,tag)
            try:
                self.logctl.debug('Creating container from img...')
                cname = cname_prefix+'-'+''.join(random.SystemRandom().choice(string.ascii_uppercase + \
                    string.digits + string.ascii_lowercase) for _ in range(6))
                return self._client.create_container_from_config(self._conf_container_js,\
                    cname)
            except Exception as e:
                self.logctl.error('Couldn\'t be able to create the container...')
                self.logctl.error(e)

    def StartContainer(self, container):
        id = container['Id']
        try:
            self._client.start(container=id)
            self.logctl.debug('Starting container with id: %s' % str(id))
        except Exception as e:
            self.logctl.error(e)

    def ListContainers(self, quiet=False, name='plancton-slave-'):
        try:
            self.logctl.info('Checking container list...')
            jdata = self._client.containers(all=True)
            self.logctl.debug('Found %r container(s) in the whole Docker pool.' % len(jdata))
            self._owned_containers = 0
            if not quiet:
                self.logctl.info('Listing below:')
                for i in range(0, len(jdata)):
                    try:
                        # If the name contains the 'name'
                        if name in str(jdata[i]['Names']):
                            self._owned_containers+=1
                            owner = 'plancton-agent'
                        else:
                            owner = 'others        '
                        status = jdata[i]['Status']
                        if not status:
                            status = 'Created (Not Running)'
                        self.logctl.info('\t [ ID: %s ] [ OWNER: %s ] [ STATUS: %s ] ' \
                            % (jdata[i]['Id'], owner, status))
                    except Exception as e:
                        self.logctl.error(e)

                self.logctl.info('Found %r owned container(s).' % self._owned_containers)
            return self._owned_containers
        except Exception as e:
            self.logctl.error(e)

    def DeployContainer(self, cname='plancton-slave', pull=True):
        self.StartContainer(self.CreateContainer(cname, pull))

    def ControlContainers(self, dominion='plancton-slave', ttl_thresh_secs=12*60*60):
        jdata = self._client.containers(all=True)
        for i in range(0, len(jdata)):
            status = jdata[i]['Status']
            id = jdata[i]['Id']
            if not status or 'Exited' in str(status):
                if dominion in str(jdata[i]['Names']):
                    ## Exited and owned.
                    try:
                        self._client.remove_container(id)
                        self.logctl.debug('Removed %s successfully.' % id)
                    except Exception as e:
                        self.logctl.error(e)

            else: ## Running container.
                try:
                    jdata2 = self._client.inspect_container(id)
                    startedat = jdata2['State']['StartedAt']
                    statobj = datetime.strptime(str(startedat)[:-11], "%Y-%m-%dT%H:%M:%S")
                    ## (**) This merely sets a workaround to an issue with Docker's time.
                    delta = time.time() - time.mktime(statobj.timetuple()) - 7200 ## (**)
                    if delta > ttl_thresh_secs:
                        try:
                            self.logctl.info('Killing %s since it exceeded the ttl_thresh' % id )
                            self._client.remove_container(id, force=True)
                        except Exception as e:
                            self.logctl.error(e)

                except Exception as e:
                    self.logctl.error(e)

    def JumpShip(self, dominion='plancton-slave'):
        self.logctl.warning('Every man for himself, abandon ship!')
        jdata = self._client.containers(all=True)
        for i in range(0, len(jdata)):
            if dominion in str(jdata[i]['Names']):
                id = jdata[i]['Id']
                try:
                    self._client.remove_container(id, force=True)
                    self.logctl.warning('\t [ ID: %s out! ' % id )
                except Exception as e:
                    self.logctl.error(e)



    def run(self):
        self.logctl.info('run called...')
        while(1):
            owning = self.ListContainers()
            if (owning < 5):
                self.DeployContainer()
            self.ControlContainers()






    ## Action to perform when some exit signal is received.
    #
    #  @return When returning True, exiting continues, when returning False exiting is cancelled
    def onexit(self):
        self.logctl.info('Termination requested: we will exit gracefully soon...')
        self._do_main_loop = False
        self.JumpShip()
        self.logctl.info('Plancton daemon exited gracefully. See you soon!')

        return True
#   ## Daemon's main loop: implements an event-based execution model.
#   def main_loop(self):
# 
#     check_time = time.time()
#     count = 0
#     tot = len( self.st['event_queue'] )
#     for evt in self.st['event_queue'][:]:
# 
#       # Extra params?
#       if 'params' in evt:
#         p = evt['params']
#       else:
#         p = []
# 
#       # Debug message
#       count += 1
#       self.logctl.debug('Event %d/%d in queue: action=%s when=%d (%d) params=%s' % \
#         (count, tot, evt['action'], evt['when'], check_time-evt['when'], p))
# 
#       if evt['when'] <= check_time:
#         r = None
#         self.st['event_queue'].remove(evt)
# 
#         # Actions
#         if evt['action'] == 'check_vms':
#           r = self.check_vms(*p)
#         elif evt['action'] == 'check_vm_errors':
#           r = self.check_vm_errors(*p)
#         elif evt['action'] == 'check_queue':
#           r = self.check_queue(*p)
#         elif evt['action'] == 'change_vms_allegedly_running':
#           r = self.change_vms_allegedly_running(*p)
#         elif evt['action'] == 'check_owned_instance':
#           r = self.check_owned_instance(*p)
# 
#         if r is not None:
#           self.st['event_queue'].append(r)
# 
# 
#   ## Daemon's main function.
#   #
#   #  @return Exit code of the daemon: keep it in the range 0-255
#   def run(self):
# 
#     self.SetupLogFiles()
#     self.logctl.info('Running plancton v%s' % self.__version__)
#     self._load_conf()
#     self.load_owned_instances()
#     self._load_batch_plugin()
#     self._init_ec2()
#     self._init_user_data()
# 
#     # Initial values for the internal state
#     self.st = {
#       'first_seen_above_threshold': -1,
#       'workers_status': {},
#       'vms_allegedly_running': 0,
#       'event_queue': [
#         {'action': 'check_vm_errors', 'when': 0},
#         {'action': 'check_vms',       'when': 0},
#         {'action': 'check_queue',     'when': 0}
#       ]
#     }
# 
#     # Schedule a sanity check for running instances as if they were just deployed
#     self.logctl.info('Scheduling sanity check for owned instances in %s seconds: %s' % \
#       (self.cf['elastiq']['estimated_vm_deploy_time_s'], self.owned_instances) )
#     time_sched = time.time() + self.cf['elastiq']['estimated_vm_deploy_time_s']
#     for inst in self.owned_instances:
#       self.st['event_queue'].append({
#         'action': 'check_owned_instance',
#         'when': time_sched,
#         'params': [ inst ]
#       })
# 
#     while self._do_main_loop:
#       self.main_loop()
#       self.logctl.debug('Sleeping %d seconds' % self.cf['elastiq']['sleep_s']);
#       time.sleep( self.cf[
