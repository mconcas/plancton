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
import time




class Plancton(Daemon):

    ## Current version of plancton
    __version__ = '0.0.1'

    ## Constructor.
    #
    #  @param name        Daemon name
    #  @param pidfile     File where PID is written
    #  @param logdir      Directory with logfiles (rotated)
    #  @param socket      Unix socket exposed by docker
    #  @param url         GitHub conf repository, we are currently using GitHub API

    def __init__(self, name, pidfile, logdir, socket='unix://var/run/docker.sock',
            url='https://api.github.com/repos/mconcas/plancton/contents/conf/worker_centos6.json'):

        super(Plancton, self).__init__(name, pidfile)
        self._logdir = logdir
        self.sockpath = socket
        self.cfg_url = url

        self._start_time = self._last_update_time = time.time()

        # up-to-date flag.
        self._list_up_to_date = False

        # Requests session needed by GitHub APIs.
        self._https_session = requests.Session()

        # docker client
        self.docker_client = Client(base_url=self.sockpath)

        # json container settings
        self._cont_config = None

        # Internal status dictionary.
        self._int_st= {

            'system'     : {},
            'daemon'     : {},
            'containers' : {}

        }
        # Internal dict lock
        self._int_lock_state

        self._containers = {}


    def safexec(self, func):
        def lockwrap(*args, **kwargs):
            if self._int_lock_state:
                return func(*args, **kwargs)
            else:
                time.sleep(1)
                return lockwrap(*args, **kwargs)
        return lockwrap
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
    #  @return True if the request sucess, False otherwise.
    def GetOnlineConf(self):

        try:
            httpsreq = self._https_session.get(self.cfg_url)
            enccfg = json.loads(httpsreq.content)['content']
            if enccfg:
                self._cont_config = json.loads(base64.b64decode(enccfg))
            else:
                self.logctl.warning('Empty cfg. string, skipping online initialization...')
            httpsreq.raise_for_status()

            return True

        except Exception as e:
            self.logctl.error('Failed to obtain online configuration, skipping...')
            self.logctl.error(e)

            return False

    ## Just a dummy check if the docker deamon is running.
    #
    #  @return True if the request sucess, False otherwise.
    def GetSetupInfo(self):

        try:
            self._int_st['system'] = self.docker_client.info()

            return True

        except requests.exceptions.ConnectionError as e:
            self.logctl.error('Connection Error: couldn\'t find a proper socket to attach. '
                + ' Is the docker daemon running? Check if /var/run/docker.sock exists.')
            self.logctl.error(e)

            return False

    ## Fetch a specified image from a trusted registry getting image:tag from repo,tagname args or
    #  as a default, from the conf. json; if the current image is already up-to-date it continues.
    #
    #  @return True if the request sucess, False otherwise.
    def PullImage(self, repo=None, tagname='latest'):

        if not repo:
            repository, tag = str(self._cont_config['Image']).split(':')
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
    def CreateContainerWithName(self, jconfig, cname_prefix):

        try:
            cname = cname_prefix
                + '-'
                + ''.join(random.SystemRandom().choice( string.ascii_uppercase
                + string.digits
                + string.ascii_lowercase) for _ in range(6))

            self.logctl.debug('Creating container with name %s. ' % cname_prefix)
            tmpcont = self.docker_client.create_container_from_config( jconfig )

            self.logctl.debug('Registering it to my internal dictionary. ')
            self._int_st['containers'][tmpcont['Id']]['name'] = 'cname'
            self._int_st['containers'][tmpcont['Id']]['status'] = 'created'

            return tmpcont

        except Exception as e:
            self.logctl.error('Couldn\'t create such container! ')
            self.logctl.error(e)

            return False

    ## Start a created container. Perform a pid inspection and return it if the container is
    #  actually running. The «container» argument is a dictionary with an 'Id' : 'somecoolhash'
    #  key : value couple.
    #
    # @return
    def StartContainer(self, container):

        try:
            self.docker_client.start(container=container['Id'])
            self.logctl.debug('Starting container with id: %s' % str(container['Id']))

            # make an inspect call to obtain container pid, in order to ease the monitoring.
            cont_dict = self.docker_client.inspect_container(container['Id'])
            cpid = cont_dict['State']['Pid']
            self._containers[container['Id']] = cpid
            self.logctl.debug('\t > %s < is running with pid : %s' % (container['Id'], cpid))

            return cpid

        except Exception as e:
            self.logctl.error(e)



    def CheckRunningContainers(self, name='plancton-slave-'):
        # First start or empty internal state.
        if not self._int_st:
            self.logctl.debug('Empty internal dict found, performing a check...')
            jdata = self.docker_client.containers(all=True)

            # Loop over all found containers and filter the owned.
            for i in range(0, len(jdata)):
                try:
                    if name in str(jdata[i]['Names']):
                        # Ok, it's a container of mines.
                        # Check running state.
                        self._int_st['containers'][jdata[i]['Id']] = { 'name' : }

    def ListContainers(self, quiet=False, name='plancton-slave-'):
        try:
            if not quiet:
                self.logctl.debug('Checking container list...')
            jdata = self.docker_client.containers(all=True)
            if not quiet:
                self.logctl.debug('Found %r container(s) in the whole Docker pool.' % len(jdata))
            self._owned_containers = 0
            if not quiet:
                self.logctl.info('Listing existent containers:')
            for i in range(0, len(jdata)):
                try:
                    if name in str(jdata[i]['Names']):
                        self._owned_containers+=1
                        owner = 'plancton-agent'
                    else:
                        owner = 'others        '
                    status = jdata[i]['Status']
                    if not status:
                        status = 'Created (Not Running)'
                    if not quiet:
                        self.logctl.info('\t [ ID: %s ] [ OWNER: %s ] [ STATUS: %s ] ' \
                            % (jdata[i]['Id'][:12], owner, status))
                except Exception as e:
                        self.logctl.error(e)

            return self._owned_containers
        except Exception as e:
            self.logctl.error(e)

    def DeployContainer(self, cname='plancton-slave', pull=True):
        self.StartContainer(self.CreateContainerWithName(cname, self._cont_config))
        self._list_up_to_date = False

    def ControlContainers(self, dominion='plancton-slave', ttl_thresh_secs=12*60*60):
        jdata = self.docker_client.containers(all=True)
        for i in range(0, len(jdata)):
            status = jdata[i]['Status']
            id = jdata[i]['Id']
            if not status or 'Exited' in str(status):
                if dominion in str(jdata[i]['Names']):
                    ## Exited and owned.
                    try:
                        self.docker_client.remove_container(id)
                        self.logctl.debug('Removed %s successfully.' % id)
                    except Exception as e:
                        self.logctl.error(e)

            else: ## Running container.
                try:
                    jdata2 = self.docker_client.inspect_container(id)
                    startedat = jdata2['State']['StartedAt']
                    statobj = datetime.strptime(str(startedat)[:19], "%Y-%m-%dT%H:%M:%S")

                    ## (**) This merely sets a workaround to an issue with Docker's time.
                    delta = time.time() - time.mktime(statobj.timetuple()) - 7200 ## (**)
                    if delta > ttl_thresh_secs:
                        try:
                            self.logctl.info('Killing %s since it exceeded the ttl_thresh' % id )
                            self.docker_client.remove_container(id, force=True)
                        except Exception as e:
                            self.logctl.error(e)

                except Exception as e:
                    self.logctl.error(e)

    def JumpShip(self, dominion='plancton-slave'):
        self.logctl.warning('Every man for himself, abandon ship!')
        jdata = self.docker_client.containers(all=True)
        for i in range(0, len(jdata)):
            if dominion in str(jdata[i]['Names']):
                id = jdata[i]['Id']
                try:
                    self.docker_client.remove_container(id, force=True)
                    self.logctl.warning('\t > container id: %s out! ' % id )
                except Exception as e:
                    self.logctl.error(e)

    ## Action to perform when some exit signal is received.
    #
    #  @return When returning True, exiting continues, when returning False exiting is cancelled
    def onexit(self):
        self.logctl.info('Termination requested: we will exit gracefully soon...')
        self._do_main_loop = False
        try:
            self.JumpShip()
            self.logctl.info('Exited gracefully, see you soon.')
            return True
        except Exception as e:
            self.logctl.error(e)
            return False


    ##  Daemon's main loop.
    #   Perfroms an image pull/update at startup and every 'delta' seconds.
    #   The APIs guarantee by their own that if the image is up-to-date it wouldn't
    #   be re-downloaded, this way I want to reduce the requests number, though.
    #   Moreover once the control is set one can schedule more features, like update-checks
    #   for the cfg file, for the daemon itself and so on.
    #   @return Nothing
    def main_loop(self,w_containers_thresh=5,update_every=3600):
        self.ControlContainers()
        delta = time.time() - self._last_update_time
        if (delta >= update_every):
            self.PullImage()
            self.GetOnlineConf()
            self._last_update_time = time.time()
        # If statement just to avoid continuously spamming into logfile. Thus only when an actual
        # modification is performed it report a new list.
        if not self._list_up_to_date:
            self.ListContainers(quiet=False)
            self._list_up_to_date = True
        else:
            self.ListContainers(quiet=True)
            self._list_up_to_date = True

        if self._owned_containers <= w_containers_thresh:
            self.DeployContainer()

    ##  Daemon's main function.
    #
    #  @return Exit code of the daemon: keep it in the range 0-255
    def run(self):
        self.SetupLogFiles()
        self.logctl.info('Running plancton v%s' % self.__version__)
        self.GetOnlineConf()
        self._do_main_loop = True
        # At startup download online configuration.
        self.PullImage()
        self.GetSetupInfo()
        while self._do_main_loop:
            self.main_loop()

        self.logctl.info('Exiting gracefully!')
        return 0
