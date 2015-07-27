#!/bin/python
# -*- coding: utf-8 -*-

import os, sys
import errno
import json
import time
import logging, logging.handlers

from configobj import ConfigObj

try:
    import requests_unixsocket, requests.exceptions
except ImportError, e:
    print 'Couldn\'t find the requests_unixsocket module. '
    + 'Please check that it is installed or, '
    + 'at least, in the working directory. '
    + str(e)

    exit(-1)

class ContainerManager(object):
    __version__ = '0.0.1'

    def __init__(self):
        self._name = 'ContainerManager'
        self._logger = logging.getLogger(self._name)

        ## Can be overridden by manual configuration.
        self._logpath = '/tmp/dev-container-manager.log'
        self._session = requests_unixsocket.Session()
        self._container_registry = []

    ## Set up logs.
    #
    # @return nothing
    def _setup_logfile(self):
        format = '%(asctime)s %(name)s %(levelname)s ' + \
            '[%(module)s.%(funcName)s] %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S'
        log_file_handler = logging.handlers.RotatingFileHandler(self._logpath, mode='a',
            maxBytes=1000000, backupCount=0)
        log_file_handler.setFormatter(logging.Formatter(format, datefmt))
        log_file_handler.doRollover()
        self._logger.setLevel(10)
        self._logger.addHandler(log_file_handler)

    ## Set up from configfile.
    #
    # @return nothing
    def _load_conf(self, file_path):
        ## This function is called ONCE at the startup and load cinfigurations and
        #  backups.

        self._logger.info('Loading cfg from: ' + file_path)
        self._config = ConfigObj(file_path)
        mancfg = 'Manager Configuration'
        logpath = self._config[mancfg]['LogPath']
        if not logpath == self._logpath:
            print 'Overriding default configuration, setting logfile to:' + logpath
        self._logpath = logpath
        self._socket_path = self._config[mancfg]['SocketPath']
        self._container_conf_file = self._config[mancfg]['ContainerConf']
        self._container_registry_file = self._config[mancfg]['RegistryPath']



        #### =============================================================================
        # Thanks to: stackoverflow.com/questions/10978869 for the advice.
        # Trying to avoid race condition.
        # When these two flags are specified, symbolic links are not followed: if pathname
        # is a symbolic link, then open() fails regardless of where the symbolic
        # link points to.
        #
        # Just providing a safer failover recovery.
        # In this particular case we are just checking if a non empty ".reg" file is
        # already present in the proper directory.
        # If any try to load that list.
        #### =============================================================================

        flags = os.O_CREAT | os.O_EXCL | os.O_RDONLY
        try:
            file_handle = os.open(self._container_registry_file, flags)
        except OSError as e:
            if e.errno == errno.EEXIST:  # Failed as the file already exists.
                self._logger.info('Found existent registry file: ' \
                    + self._container_registry_file)

                with open(self._container_registry_file) as registry:
                    for line in registry:
                        self._container_registry.append(line.rstrip('\n'))
            else:  # Something unexpected went wrong so reraise the exception.
                self._logger.debug('Failed to get a file descriptor for %s, ' \
                    + ' nor existing .reg file. Traceback follows.' \
                    % self._container_registry_file)
                self._logger.error(e)

                exit(-1)

    ## Check for existing containers, independently from which are their statuses.
    #
    #  NEW:
    #  Now it also performs an ID check with the internally stored list, substantially
    #  making container-manager blind to "not owned" containers.
    #  Thus is provided a sort of isolation.
    #
    # @return the list length.
    def _list_container(self):
        """
        This function wraps some requests, print the container list in the log file
        and return the number of running containers.
        """
        try:
            self._logger.info('Trying to attach to : ' + 'http+unix://' \
                + self._socket_path + '/containers/json?all=1')
            request = self._session.get('http+unix://' + self._socket_path \
                + '/containers/json?all=1')
            request.raise_for_status()
            self._logger.info('Checking container list...')
            self._jsoncontlist = json.loads(request.content)
            contnum = len(self._jsoncontlist)
            self._logger.info('Found %r container(s) in the whole Docker database.' \
                % contnum)

            status = ''
            owned_counter = 0
            for i in range(0, contnum):
                id = str(self._jsoncontlist[i]['Id'])
                if id in self._container_registry:
                    owner = 'spawner'
                    owned_counter+=1
                    status = self._jsoncontlist[i]['Status']
                    status if status else 'Created'
                    # self._logger.debug('Found container(s) of mine, listing...')
                    self._logger.info('\t ~> ID: %s ~> STATUS: %s ~> OWNER: %s' \
                        % (id, status, owner))
                else:
                    owner = 'others'
                    status = self._jsoncontlist[i]['Status']
                    status if status else 'Created'
                    # self._logger.debug('Found container(s) not of mine, listing...')
                    self._logger.debug('\t ~> ID: %s ~> STATUS: %s ~> OWNER: %s' \
                        % (id, status, owner))

            return owned_counter

        except requests.exceptions.ConnectionError:
            self._logger.debug('Conn. Error: couldn\'t find a proper socket. '
                + ' Is the Docker daemon running correctly?'
                + ' Check if /var/run/docker.sock exists.' )

            exit(-1)

        except requests.exceptions.HTTPError as e:
            print 'HTTPError raised, please check ' + self._logpath + \
            ' for further informations.'
            self._logger.debug('Bad answer from server. \n')
            self._logger.error(e)

            exit(-1)

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

                    if not c_id in self._container_registry:
                        self._container_registry.append(c_id)

                    ## Before returning is a good choice to backup the registry to the
                    #  .reg file. Since a container creation is not done with an high
                    #  frequency this should not cause overhead issues.
                    with open(self._container_registry_file, 'a') as registry:
                        registry.write(c_id + '\n')

                    return c_id

                except requests.exceptions.ConnectionError as e:
                    self._logger.debug('Conn. Error: couldn\'t find a proper socket.'
                        + ' Is the Docker daemon running correctly?'
                        + ' Check if /var/run/docker.sock exists.' )
                    self._logger.error(e)

                    exit(-1)

                except requests.exceptions.HTTPError as e:
                    self._logger.debug('Bad answer from server.')
                    self._logger.error(e)

                    exit(-1)

        except ValueError as e:
            self._logger.debug('JSON ValueError: parse error. Please check the ' \
                + 'consistency of your %s file.' % self._container_conf_file)
            self._logger.error(e)

            exit(-1)

        except IOError as e:
            self._logger.debug('IOError: %s file not found.' % self._container_conf_file)
            self._logger.error(e)

            exit(-1)

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

            exit(-1)

    ## Clean up the container list.
    #
    # @return nothing.

    ## /!\    TO-DO: to provide isolation!   /!\ ##
    def _container_reaper(self):
        """
        Wipes out all the Created and Exited containers.
        """

        try:
            ## For now i choose to query a new list, and match it with the registry
            #  before reap out a container. In the future it could be that the reaper
            #  will get the sentences list directly from local list or file list.
            request = self._session.get('http+unix://' + self._socket_path \
                + '/containers/json?all=1')
            request.raise_for_status()
            self._logger.info('Checking container list...')
            listjson = json.loads(request.content)
            for i in range(0, len(listjson)):
                if not listjson[i]['Status'] or 'Exited' in listjson[i]['Status']:
                    id = str(listjson[i]['Id'])
                    if id in self._container_registry:
                        try:
                            request = self._session.delete('http+unix://' \
                                + self._socket_path + '/containers/' + listjson[i]['Id'])
                            request.raise_for_status()

                            self._logger.info('Deleted: %s since its status was: %s' \
                                % ( listjson[i]['Id'][0:12], listjson[i]['Status']))
                            ## Synchronize the backup file.
                            tmp_list = []
                            with open(self._container_registry_file, 'r') as registry:
                                tmp_list = registry.readlines()
                            with open(self._container_registry_file, 'w') as registry:
                                for line in tmp_list:
                                    if line != id:
                                        registry.write(line)

                        except requests.exceptions.HTTPError as e:
                            self._logger.debug('HTTPError: ' + request.content)
                            self._logger.error(e)

                            exit(-1)

        except requests.exceptions.ConnectionError:
            self._logger.error('Connection Err: can\'t find a proper socket.' \
                + ' Is the docker daemon running correctly?' \
                + ' Check if /var/run/docker.sock exists.' )

            exit(-1)

        except requests.exceptions.HTTPError as e:
            self._logger.debug('Bad answer from server.')
            self._logger.error(e)

            exit(-1)

    def _container_respawner(self):
        pass





manager = ContainerManager()
manager._setup_logfile()
manager._load_conf('conf/manager.conf')
manager._list_container()
ID = manager._build_container()
manager._start_container(ID)
manager._container_reaper()
