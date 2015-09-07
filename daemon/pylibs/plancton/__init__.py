# -*- coding: utf-8 -*-

import requests
import docker
import json
import os, time
import logging, logging.handlers
import base64
import string
import random
import psutil
import pprint
from datetime import datetime
from docker import Client
from daemon import Daemon

class Plancton(Daemon):

    ## Current version of plancton
    __version__ = '0.0.2'

    # Base policies dictionary
    # self._policies = {}

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
        self._start_time = self._last_update_time = self._last_confup_time = time.time()

        self._logdir = logdir
        self.sockpath = socket
        self.cfg_url = url

        # Start time
        self._start_time = self._last_update_time = time.time()

        # Requests session needed by GitHub APIs.
        self._https_session = requests.Session()

        # docker client
        self.docker_client = Client(base_url=self.sockpath)

        # json container settings
        self._cont_config = None

        # Internal status dictionary.
        self._int_st = {
                        'system'     : {},
                        'daemon'     : {
                                        'version'        : self.__version__,
                                        'updateinterval' : 300,
                                        'updateconfig'   : 3600,
                                        'currentpolicy'   : {
                                                            'name' : 'default',
                                                            'maxcontainerno' : 8
                                                            }
                                        },
                        'containers' : {}
                        }

        #  flag to force a control
        self._updateflag = True

        #  Blacklist
        self.blacklist = []

    def _set_policy(self, name=''):
        pass


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
            cname = cname_prefix \
                + '-' \
                + ''.join(random.SystemRandom().choice( string.ascii_uppercase \
                + string.digits \
                + string.ascii_lowercase) for _ in range(6))

            self.logctl.debug('Creating container with name %s. ' % cname)
            tmpcont = self.docker_client.create_container_from_config(jconfig, name=cname)

        except Exception as e:
            self.logctl.error('Couldn\'t create the container! ')
            self.logctl.error(e)

            return None

        else:
            self.logctl.debug('Registering it to internal dictionary. ')
            self._int_st['containers'][str(tmpcont['Id'])] = { 'name' : cname }
            self._int_st['containers'][str(tmpcont['Id'])]['status'] = 'created'
            # self._int_st['containers'][str(tmpcont['Id'])]['id'] = str(tmpcont['Id'])

            return tmpcont

    ## Start a created container. Perform a pid inspection and return it if the container is
    #  actually running. The «container» argument is a dictionary with an 'Id' : 'somecoolhash'
    #  key : value couple.
    #
    # @return pid of container if it's successfully found running after the start. None otherwise.
    def StartContainer(self, container):

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

            # Shall I wait for the process to spawn? «while not psutil.pid_exists(pid):» ..?
            if psutil.pid_exists(jj['State']['Pid']):
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
    def DeployContainer(self, cname='plancton-slave'):

        self.StartContainer(self.CreateContainerWithName(cname_prefix=cname, \
            jconfig=self._cont_config))


    ## Called once at startup. Fetch for running containers with a name matching the slaves' one.
    #  This is just in case the daemon was terminated abnormally (i.e. it couln't be possible to
    #  wipe out the slave containers).
    #  Read this a failover method.
    #
    # @return True if found owned containers. False otherwise.
    def RestoreContainerList(self, name='plancton-slave'):

        if not self._int_st['containers']:
            ret = False
            # Startup or empty internal state.
            self.logctl.debug('Empty container list, fetching status...')
            jdata = self.docker_client.containers(all=True)

            # Loop over all found containers and filter the owned.
            for i in range(0, len(jdata)):
                if name in str(jdata[i]['Names']):
                    ret = True
                    # Ok, it's a container of mines.
                    # Check running state.
                    # Create the line in the dict.
                    self._int_st['containers'][str(jdata[i]['Id'])] = \
                        { 'name' : str(jdata[i]['Names'][0].replace('/','')) }
                    # self._int_st['containers'][str(jdata[i]['Id'])]['id'] = str(jdata[i]['Id'])
                    status = jdata[i]['Status']
                    if not status:
                        self._int_st['containers'][str(jdata[i]['Id'])]['status'] = 'created'

                    elif 'Up' in status:
                        self._int_st['containers'][str(jdata[i]['Id'])]['status'] = 'running'

                        # Inspect to get pid and uptime.
                        try:
                            jj = self.docker_client.inspect_container(jdata[i]['Id'])
                            self._int_st['containers'][jdata[i]['Id']]['pid'] = jj['State']['Pid']
                            self._int_st['containers'][jdata[i]['Id']]['startedat'] = \
                                str(jj['State']['StartedAt'])

                        except Exception as e:
                            slef.logctl.error(e)

                    elif 'Exited' in status:
                        self._int_st['containers'][jdata[i]['Id']]['status'] = 'exited'
                    else:
                        self._int_st['containers'][jdata[i]['Id']]['status'] =  status

            return ret

    ## PPrinter for internal status. Debug purpose only.
    #
    # @return nothing
    def DumpStatus(self):

        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(self._int_st)

    ## Self-esplicative: refresh internal data quering the status of the containers.
    #
    #  @return nothing
    def RefreshRunningAwareness(self):
        for i,j in self._int_st['containers'].iteritems():
            if 'running' in j.values():
                try:
                    jdata = self.docker_client.inspect_container(i)
                except Exception as e:
                    self.logctl.error(e)
                else:
                    if not jdata['State']['Pid']:
                        # gone
                        j.pop('pid', None)
                        j['status'] = 'exited'

    ## Log some informations about the self-consciousness of the containers.
    #
    # @return True if non empty dictionary. False Otherwise.
    def PrintInfo(self):
        self.logctl.info('-------------------------------------------------------------------')
        self.logctl.info('| n° |  container id  |  status   |         name          |  pid  |')
        self.logctl.info('-------------------------------------------------------------------')
        for i,j in self._int_st['containers'].iteritems():
            self.logctl.info( '| %s  |  %s  |  %s  | %s | %s  |' \
                % (self._int_st['containers'].keys().index(i) + 1, i[:12],
                j['status'], j['name'], j['pid']))
        self.logctl.info('-------------------------------------------------------------------')

    ## This is the CController it gets rid of exceeded ttl or exited/created (read 'not started')
    #  containers.
    #
    # @return number of active containers.
    def ControlContainers(self, ttl_thresh_secs=12*60*60):

        for i,j in self._int_st['containers'].iteritems():
            if 'running' in str(self._int_st['containers'][i]['status']):
                # running.
                statobj = datetime.strptime(self._int_st['containers'][i]['startedat'][:19],
                    "%Y-%m-%dT%H:%M:%S")
                ##  (**) This merely sets a workaround to an issue with Docker's time.
                #  on my test docker installation time inside the container is two hour early.
                delta = time.time() - time.mktime(statobj.timetuple()) - 7200 ## (**)

                if delta > ttl_thresh_secs:
                    # Time exceeded
                    self.logctl.info('Killing %s since it exceeded the ttl_thresh' \
                        % i )
                    try:
                        self.docker_client.remove_container(i, force=True)
                    except Exception as e:
                        self.logctl.error(e)
                    else:
                        self.logctl.debug('Removed %s successfully.' % i)
                        self.blacklist.append(i)

            else:
                # not running: exited/created
                try:
                    self.docker_client.remove_container(i)
                except Exception as e:
                    self.logctl.debug(i)
                    self.logctl.error(e)
                else:
                    self.blacklist.append(i)
                    self.logctl.debug('Removed %s successfully.' % i)

        # Update internal status.
        for i in self.blacklist:
            if i in self._int_st['containers']:
                self._int_st['containers'].pop(i,None)

        self.blacklist = []

        return len(self._int_st['containers']) # running number returned

    ## Gracefully exiting, plancton kills all the owned containers.
    #
    # @return Nothing
    def JumpShip(self, dominion='plancton-slave'):
        self.logctl.warning('Every man for himself, abandon ship!')
        jdata = self.docker_client.containers(all=True)
        for i in range(0, len(jdata)):
            if dominion in str(jdata[i]['Names']):
                id = jdata[i]['Id']
                try:
                    self.docker_client.remove_container(id, force=True)
                except Exception as e:
                    self.logctl.error(e)
                    return False
                else:
                    self.logctl.warning('\t > container id: %s out! ' % id )
                    self.blacklist.append(i)

        # Update internal status.
        for i in self.blacklist:
            if i in self._int_st['containers']:
                self._int_st['containers'].pop(i,None)

        self.blacklist = []
        return True

    ## Action to perform when some exit signal is received.
    #
    #  @return True if success.
    def onexit(self):
        self.logctl.info('Termination requested: we will exit gracefully soon...')
        self._do_main_loop = False
        if self.JumpShip():
            self.logctl.info('Exited gracefully, see you soon.')
            return True

        return False

    ##  Daemon's main loop.
    #   Perfroms an image pull/update at startup and every 'delta' seconds.
    #   The APIs guarantee by their own that if the image is up-to-date it wouldn't
    #   be re-downloaded, this way I want to reduce the requests number, though.
    #   Moreover once the control is set one can schedule more features, like update-checks
    #   for the cfg file, for the daemon itself and so on.
    #
    #   @return Nothing
    def main_loop(self):

        whattimeisit = time.time()
        delta_1 = whattimeisit - self._last_confup_time
        delta_2 = whattimeisit - self._last_update_time

        if (delta_1 >= int(self._int_st['daemon']['updateconfig'])):
            self.PullImage()
            self.GetOnlineConf()
            self._last_confup_time = time.time()

        if (delta_2 >= int(self._int_st['daemon']['updateinterval'])) or self._updateflag:
            self.RefreshRunningAwareness()

            # Clean and count running containers
            running = self.ControlContainers()

            while int(self._int_st['daemon']['currentpolicy']['maxcontainerno']) > int(running):
                self.DeployContainer()
                running = running + 1

            self._last_update_time = time.time()
            self.ControlContainers()
            self._updateflag = False
            self.PrintInfo()



    ##  Daemon's main function.
    #
    #  @return Exit code of the daemon: keep it in the range 0-255
    def run(self):
        self.SetupLogFiles()
        self.logctl.info('---- plancton daemon v%s ----' % self.__version__)
        self.RestoreContainerList()
        self.GetOnlineConf()
        self.PullImage()
        self.ControlContainers()
        self.GetSetupInfo()
        self._do_main_loop = True

        while self._do_main_loop:
            self.main_loop()
            for i in range(60): # awesome
                time.sleep(1)

        self.logctl.info('Exiting gracefully!')

        return 0
