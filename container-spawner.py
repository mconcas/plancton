#!/bin/python
# -*- coding: utf-8 -*-

import os, sys
import errno
import json
import time
import logging, logging.handlers

from stat import *
from configobj import ConfigObj

try:
    import requests_unixsocket, requests.exceptions
except ImportError, e:
    print 'Couldn\'t find the requests_unixsocket module. Please check the prereqs...' \
        + str(e)

    sys.exit(-1)

class ContainerPoolManager(object):
    __version__ = '0.0.2'

    def __init__(self):
        self._name = 'ContainerManager'
        self._logger = logging.getLogger(self._name)

        ## Can be overridden by manual configuration.
        self._logpath = '/tmp/container-manager.log'
        self._session = requests_unixsocket.Session()
        # self._container_registry = []

    ## _setup_logger: sets up logs.
    #
    # @return nothing
    def _setup_logger(self):
        format = '%(asctime)s %(name)s %(levelname)s ' \
            + '[%(module)s.%(funcName)s] %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S'
        log_file_handler = logging.handlers.RotatingFileHandler(self._logpath, mode='a',
            maxBytes=1000000, backupCount=0)
        log_file_handler.setFormatter(logging.Formatter(format, datefmt))
        log_file_handler.doRollover()
        self._logger.setLevel(10)
        self._logger.addHandler(log_file_handler)

    ## _setup_from_conf: sets up from configfile.
    #
    # @return nothing
    def _setup_from_conf(self, file_path):
        ## This function is called ONCE at the startup, it loads configurations and
        #  try to restore the container list from a backup file.

        self._logger.info('Loading cfg from: ' + file_path)
        self._config = ConfigObj(file_path)
        mancfg = 'Manager Configuration'
        logpath = self._config[mancfg]['LogPath']
        if not logpath == self._logpath:
            self._logger.info('Overriding default conf. setting logfile to:' + logpath)
        self._logpath = logpath
        self._socket_path = self._config[mancfg]['SocketPath']
        self._container_conf_file = self._config[mancfg]['ContainerConf']

    ## _list_container: lists existing containers and their own statuses.
    #
    #  NEW:
    #  Now it also performs an ID check with the internally stored list, substantially
    #  making container-manager blind to "not owned" containers.
    #  Thus is provided a sort of isolation.
    #
    #  @return the list length.
    def _list_container(self, quiet=False):
        """
        This function wraps some requests, prints container list in the log file
        and returns the number of running _owned_ containers.
        """
        try:
            self._logger.info('Checking container list...')
            request = self._session.get('http+unix://' + self._socket_path
                + '/containers/json?all=1')
            request.raise_for_status()
            self._jsoncontlist = json.loads(request.content)

            self._contnum = len(self._jsoncontlist) # Total containers number.
            self._logger.debug('Found %r container(s) in the whole Docker database.' \
                % self._contnum)
            self._logger.info('Listing below...')

            ## Since the APIs return all the containers in the list, even the ones ran
            #  by other users, we must provide, at some point, a kind of isolation.
            #  NEW: isolation is now provided by read a special label: container.label
            #  and asking it corresponds to a specified value.

            owned_containers = 0

            for i in range(0, self._contnum):
                try:
                    contlabel = self._jsoncontlist[i]['Labels']['container.label']
                    if contlabel == 'worker-node':
                        owner = 'spawner-agent'
                        owned_containers+=1
                    else:
                        owner = 'others       '

                except KeyError:
                    owner = 'others       '

                status = self._jsoncontlist[i]['Status']
                if not status:
                    status = 'Created (Not Running)'
                if not quiet:
                    self._logger.info('\t [ ID: %s ] [ OWNER: %s ] [ STATUS: %s ] ' \
                        % (id, owner, status))

            self._logger.info('Found %r "owned" container(s).' % owned_containers)

            return owned_containers

        except requests.exceptions.ConnectionError:
            self._logger.debug('Connection Error: couldn\'t find a proper socket. '
                + ' Is the Docker daemon running correctly?'
                + ' Check if /var/run/docker.sock exists.' )

            sys.exit(-1)

        except requests.exceptions.HTTPError as e:
            print 'HTTPError raised, please check ' + self._logpath + \
            ' for further informations.'
            self._logger.debug('Bad answer from server. \n')
            self._logger.error(e)

            sys.exit(-1)

    ## Create a container following from a json cfg file.
    #
    # @return nothing.
    def _build_container(self):
        """
        This function creates a container. It returns, if success, the container id.
        Register the built container into a local registry (this is because
        isolation MUST be provided).
        """
        try:
            with open(self._container_conf_file) as data_file:
                data = json.load(data_file)
                headers = {'content-type':'application/json', 'Accept':'text/plain'}
                try:
                    request = self._session.post('http+unix://' + self._socket_path \
                        + '/containers/create', data=json.dumps(data), headers=headers)
                    request.raise_for_status()
                    self._logger.debug('Successfully created: ' + str(request.content))
                    c_id = str(json.loads(request.content)['Id'])

                    return c_id

                except requests.exceptions.ConnectionError as e:
                    self._logger.debug('Conn. Error: couldn\'t find a proper socket.'
                        + ' Is the Docker daemon running correctly?'
                        + ' Check if /var/run/docker.sock exists.' )
                    self._logger.error(e)

                    sys.exit(-1)

                except requests.exceptions.HTTPError as e:
                    self._logger.debug('Bad answer from server.')
                    self._logger.error(e)

                    sys.exit(-1)

        except ValueError as e:
            self._logger.debug('JSON ValueError: parse error. Please check the ' \
                + 'consistency of your %s file.' % self._container_conf_file)
            self._logger.error(e)

            sys.exit(-1)

        except IOError as e:
            self._logger.debug('IOError: %s file not found.' % self._container_conf_file)
            self._logger.error(e)

            sys.exit(-1)

    ## Start a created container.
    #
    # @return nothing
    def _start_container(self, id):
        """
        This function wraps some requests and starts a container by getting its ID.
        """
        try:
            request = self._session.post('http+unix://' + self._socket_path \
                + '/containers/' + id + '/start')
            request.raise_for_status()
            self._list_container()

        except requests.exceptions.HTTPError as e:
            self._logger.debug('HTTPError: ' + request.content)
            self._logger.error(e)

            sys.exit(-1)

    ## Garbage collector.
    #
    # @return nothing.
    def _container_reaper(self):
        """
        Wipes out all the Created and Exited containers.
        """

        self._list_container(True) # Update jsonlist
        # for id in self._container_registry:
        for i in range(0, len(self._jsoncontlist)):
            status = self._jsoncontlist[i]['Status']
            if not status or 'Exited' in status:
                id = self._jsoncontlist[i]['Id']
                try:
                    contlabel = self._jsoncontlist[i]['Labels']['container.label']
                    if contlabel == 'worker-node':
                        ## Exited and owned.
                        try:
                            request = self._session.delete('http+unix://' \
                                + self._socket_path + '/containers/' + id)
                            request.raise_for_status()
                        except requests.exceptions.HTTPError as e:
                            self._logger.debug('HTTPError: ' + request.content)
                            self._logger.error(e)
                            self._logger.warning('It couldn\'t be possibile to delete '
                                + ' container: ' + id)
                except:
                    pass

#=========================================================================================
def container_respawner(cont_num):
    manager = ContainerPoolManager()
    manager._setup_logger()
    manager._setup_from_conf('conf/manager.conf')
    while(1):
        owning = manager._list_container()

        if (owning < cont_num):
            ID = manager._build_container()
            manager._start_container(ID)

        manager._container_reaper()

container_respawner(3)
