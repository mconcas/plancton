#!/bin/python
# -*- coding: utf-8 -*-

import os, sys, time
import errno
import json
import logging, logging.handlers
import requests_unixsocket, requests, requests.exceptions as reqexc
import base64
from datetime import datetime

class ContainerPoolManager(object):
    __version__ = '0.0.2'

    def __init__(self, logpath='/tmp/container-pool-manager.log'):
        self._name = 'CPMngr' ## ContainerPoolManager truncated for a lighter logging.
        self._logger = logging.getLogger(self._name)

        ## Can be overridden by manual configuration.
        self._logpath = logpath

        ## *requests sessions
        self._unix_session = requests_unixsocket.Session()
        self._https_session = requests.Session()

        ## JSON container configuration
        self._json_cconfig = []

    ## _logger_setup: sets up logs.
    #
    def _logger_setup(self):
        format = '%(asctime)s %(name)s %(levelname)s ' \
            + '[%(module)s.%(funcName)s] %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S'
        log_file_handler = logging.handlers.RotatingFileHandler(self._logpath, mode='a',
            maxBytes=1000000, backupCount=5)
        log_file_handler.setFormatter(logging.Formatter(format, datefmt))
        log_file_handler.doRollover()
        self._logger.setLevel(10)
        self._logger.addHandler(log_file_handler)

    ## _setup_from_conf: sets up from configfile.
    #
    def _initialize(self):
        '''
        Function usually called at the startup.
        It downloads the configurations via HTTPS from a specified URL.
        '''

        try:
            self._logger.info('Downloading container configuration from GitHub...')

            ## Change this url to load custom configuration.
            #  ref=string The name of the commit/branch/tag.
            #  Default: the repository’s default branch (usually master)
            #  (https://developer.github.com/v3/repos/contents/)
            httpsrequest = self._https_session.get('https://api.github.com/repos/'
                + 'mconcas/plancton/contents/conf/worker_centos6.json?ref=master')
            httpsrequest.raise_for_status()

            encodedcfg = json.loads(httpsrequest.content)['content']
            if encodedcfg:
                self._logger.debug('Found cfg 64-based string...')
                self._json_cconfig = json.loads(base64.b64decode(encodedcfg))
            else:
                self._logger.warning('Empty cfg string, skipping conf initialization...')

        except Exception as e:
            self._logger.error(e)

        ## Docker unix-socket path, usually located in /var/run
        #  It likely won't be moved.
        self._socket_path = '%2Fvar%2Frun%2Fdocker.sock'

    ## _list_containers: lists existing containers and their own statuses.
    # 
    #  List the running containers having a specified label, that is stored by
    #  docker during the build phase.
    def _list_containers(self, quiet=False, taglabel='worker-node'):
        """
        Function that wraps some requests, prints container list to a log file
        and returns the number of running containers found with a specific tag.
        """

        try:
            self._logger.info('Checking container list...')
            unixrequest = self._unix_session.get('http+unix://' + self._socket_path
                + '/containers/json?all=1')
            unixrequest.raise_for_status()
            self._jscontlist = json.loads(unixrequest.content)

            self._contnum = len(self._jscontlist) # Total containers number.
            self._logger.debug('Found %r container(s) in the whole Docker database.' \
                % self._contnum)

            self._owned_containers = 0
            if not quiet:
                self._logger.info('Listing below...')
            for i in range(0, self._contnum):
                try:
                    contlabel = self._jscontlist[i]['Labels']['container.label']
                    if self._jscontlist[i]['Labels']['container.label'] == taglabel:
                        owner = 'spawner-agent'
                        self._owned_containers+=1
                    else:
                        owner = 'others       '

                ## It's likely that user-runned containers do not have the tag label...
                except KeyError:
                    owner = 'others       '

                status = self._jscontlist[i]['Status']
                if not status:
                    status = 'Created (Not Running)'
                if not quiet:
                    self._logger.info('\t [ ID: %s ] [ OWNER: %s ] [ STATUS: %s ] ' \
                        % (self._jscontlist[i]['Id'], owner, status))

            self._logger.info('Found %r owned container(s).' % self._owned_containers)

            return self._owned_containers

        except reqexc.ConnectionError:
            self._logger.debug('Connection Error: couldn\'t find a proper socket. '
                + ' Is the Docker daemon running correctly?'
                + ' Check if /var/run/docker.sock exists.' )

        except reqexc.HTTPError as e:
            print 'HTTPError raised, please check ' + self._logpath + \
            ' for further informations.'
            self._logger.debug('Bad response from server. \n')
            self._logger.error(e)

    ## Create a container following from a json cfg file.
    #

    def _build_container(self):
        """
        This function creates a container. It returns, in case of success, the id.
        """
        headers = {'content-type':'application/json', 'Accept':'text/plain'}
        try:
            try:
                self._logger.info('Pulling image: ' + self._json_cconfig['Image'] )
                unixrequest = self._unix_session.post('http+unix://'
                    + self._socket_path + '/images/create?fromImage='
                    + self._json_cconfig['Image'])
                unixrequest.raise_for_status()

            except Exception as e:
                self._logger.error(e)

            unixrequest = self._unix_session.post('http+unix://' \
                + self._socket_path + '/containers/create',      \
                data=json.dumps(self._json_cconfig), headers=headers)

            unixrequest.raise_for_status()
            self._logger.debug('Successfully created: ' \
                + str(unixrequest.content).rstrip('\n'))

            c_id = str(json.loads(unixrequest.content)['Id'])

            return c_id

        except reqexc.ConnectionError as e:
            self._logger.debug('Conn. Error: couldn\'t find a proper socket.'
                + ' Is the Docker daemon running correctly?'
                + ' Check if /var/run/docker.sock exists.' )
            self._logger.error(e)

        except reqexc.HTTPError as e:
            self._logger.warning(e)
            # self._logger.info('Local image %s is not present or outdated, repulling...' \
                # % self._json_cconfig['Image'])

        except ValueError as e:
            self._logger.debug('JSON ValueError: parser error.')
            self._logger.error(e)

    ## Start a created container.
    #

    def _start_container(self, id):
        """
        This function wraps some requests and starts a container by getting its ID.
        """

        self._initialize()

        try:
            unixrequest = self._unix_session.post('http+unix://' + self._socket_path \
                + '/containers/' + id + '/start')
            unixrequest.raise_for_status()
            self._list_containers()

        except reqexc.HTTPError as e:
            self._logger.debug('HTTPError: ' + unixrequest.content)
            self._logger.error(e)

    ## Garbage collector.
    #
    def _container_cleaner(self, ttl_threshold=12*60*60):
        """
        Wipes out all the Created and Exited containers.
        If a container has been «RUNNING» for more than 12h (overrideable value) it
        would be cleared as well.
        """

        self._list_containers(True) # Update self._jscontlist
        for i in range(0, len(self._jscontlist)):
            status = self._jscontlist[i]['Status']
            id = self._jscontlist[i]['Id']
            if not status or 'Exited' in status:
                try:
                    contlabel = self._jscontlist[i]['Labels']['container.label']
                    if contlabel == 'worker-node':
                        ## Exited and owned.
                        try:
                            unixrequest = self._unix_session.delete('http+unix://' \
                                + self._socket_path + '/containers/' + id)
                            unixrequest.raise_for_status()
                            self._logger.debug('Removed ' + id + ' successfully.')
                        except reqexc.HTTPError as e:
                            self._logger.debug('HTTPError: ' + unixrequest.content)
                            self._logger.error(e)
                            self._logger.warning('Couldn\'t delete: ' + id)
                except:
                    pass
            else: ## Running container.
                try:
                    unixrequest = self._unix_session.get('http+unix://' \
                        + self._socket_path + '/containers/' + id + '/json')
                    startedat = json.loads(unixrequest.content)['State']['StartedAt']
                    statobj = datetime.strptime(str(startedat)[:-11], "%Y-%m-%dT%H:%M:%S")
                    ## (**) This merely sets a workaround to an issue with Docker's time.
                    delta = time.time() - time.mktime(statobj.timetuple()) - 7200 ## (**)

                    if delta > ttl_threshold:
                        try:
                            self._logger.info('Killing %s: exceeded the ttl_thresh' % id )
                            unixrequest = self._unix_session.post('http+unix://' \
                                + self._socket_path + '/containers/' + id + '/kill')
                        except Exception as e:
                            self._logger.error(e)

                except Exception as e:
                    self._logger.error(e)


#=========================== dummy function ==============================================
def dummy_container_respawner(cont_num):
    manager = ContainerPoolManager()
    manager._logger_setup()
    manager._initialize()
    while(1):
        owning = manager._list_containers()
        if (owning < cont_num):
            ID = manager._build_container()
            manager._start_container(ID)
        manager._container_cleaner()

dummy_container_respawner(3)
