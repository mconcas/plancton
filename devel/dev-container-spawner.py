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
    print 'Couldn\'t find the requests_unixsocket module. '
    + 'Please check that it is installed or, '
    + 'at least, in the working directory. '
    + str(e)

    os.exit(-1)

class ContainerManager(object):
    __version__ = '0.0.1'

    def __init__(self):
        self._name = 'ContainerManager'
        self._logger = logging.getLogger(self._name)

        ## Can be overridden by manual configuration.
        self._logpath = '/tmp/dev-container-manager.log'
        self._session = requests_unixsocket.Session()
        self._container_registry = []

    ## _setup_logger: sets up logs.
    #
    # @return nothing
    def _setup_logger(self):
        format = '%(asctime)s %(name)s %(levelname)s ' + \
            '[%(module)s.%(funcName)s] %(message)s'
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
        #  backup files.

        self._logger.info('Loading cfg from: ' + file_path)
        self._config = ConfigObj(file_path)
        mancfg = 'Manager Configuration'
        logpath = self._config[mancfg]['LogPath']
        if not logpath == self._logpath:
            print 'Overriding default configuration, setting logfile to:' + logpath
        self._logpath = logpath
        self._socket_path = self._config[mancfg]['SocketPath']
        self._container_conf_file = self._config[mancfg]['ContainerConf']

        ## For now hardcoded, since it cannot be safely (re)created, preventing a wrong
        #  behaviour for this script.
        self._container_registry_file = '/tmp/dev-container-manager.reg'

        # If any, recover from file.
        self._update_registry_from_file()

    ## _list_container: lists existing containers and their own statuses.
    #
    #  NEW:
    #  Now it also performs an ID check with the internally stored list, substantially
    #  making container-manager blind to "not owned" containers.
    #  Thus is provided a sort of isolation.
    #
    #  @return the list length.
    def _list_container(self):
        """
        This function wraps some requests, prints container list in the log file
        and returns the number of running _owned_ containers.
        """
        try:
            self._logger.debug('Trying to attach to : http+unix://'
                + self._socket_path + '/containers/json?all=1')
            self._logger.info('Checking container list...')
            request = self._session.get('http+unix://' + self._socket_path
                + '/containers/json?all=1')
            request.raise_for_status()
            self._jsoncontlist = json.loads(request.content)

            contnum = len(self._jsoncontlist) # Total containers number.
            self._logger.debug('Found %r container(s) in the whole Docker database.' \
                % contnum)
            self._logger.info('Listing below...')

            ## Since the APIs return all the containers in the list, even the ones ran
            #  by other users, we must provide, at some point, a kind of isolation.
            #  Thus we can work safely using our local list of containers' Ids.
            owned_containers = 0
            for i in range(0, contnum):
                status = ''
                id = str(self._jsoncontlist[i]['Id'])
                if id in self._container_registry:
                    owner = 'spawner-agent'
                    owned_containers+=1
                    status = self._jsoncontlist[i]['Status']
                    status if status else 'Created'
                    self._logger.info('\t => ID: %s => OWNER: %s => STATUS: %s ' \
                        % (id, owner, status))
                else:
                    owner = 'others'
                    status = self._jsoncontlist[i]['Status']
                    status if status else 'Created'
                    self._logger.debug('\t => ID: %s => OWNER: %s => STATUS: %s ' \
                        % (id, owner, status))

            self._logger.info('Found %r "owned" container(s).' % owned_containers)

            return owned_containers

        except requests.exceptions.ConnectionError:
            self._logger.debug('Connection Error: couldn\'t find a proper socket. '
                + ' Is the Docker daemon running correctly?'
                + ' Check if /var/run/docker.sock exists.' )

            os.exit(-1)

        except requests.exceptions.HTTPError as e:
            print 'HTTPError raised, please check ' + self._logpath + \
            ' for further informations.'
            self._logger.debug('Bad answer from server. \n')
            self._logger.error(e)

            os.exit(-1)

    ## _registry_file_update: performs an update of the backup registry file.
    #
    # @return nothing
    def _registry_file_update(self):

        flags = os.O_CREAT | os.O_EXCL | os.O_RDONLY
        try:
            file_handle = os.open(self._container_registry_file, flags, 0600)
        except OSError as e:
            if e.errno == errno.EEXIST:  # Failed as the file already exists.
                self._logger.info('Found existent registry file: '
                    + self._container_registry_file)

                ## Checking right permissions.
                reg_stats = os.stat(self._container_registry_file)
                if not bool((reg_stats[ST_MODE] & S_IRWXG) or (reg_stats[ST_MODE] \
                    & S_IRWXO)):
                    with open(self._container_registry_file) as registry:

                        ## Feed the registry.
                        for line in self._container_registry:
                            registry.write(line + '\n')
                else:
                    logging.error('Wrong permissions on %s, ignoring it.' \
                        % self._container_registry_file)

            else:  # Something unexpected went wrong so reraise the exception.
                self._logger.debug('Failed to get a file descriptor for %s, '
                    + ' nor existing .reg file. Traceback follows.' \
                    % self._container_registry_file)
                self._logger.error(e)

                os.exit(-1)

    ## _update_registry_from_file: performs an update from the backup registry file.
    #
    # @return nothing
    def _update_registry_from_file(self):
        #### =============================================================================
        # Thanks to: stackoverflow.com/questions/10978869 for the advice.
        # Trying to avoid race condition, excluding symlinks.
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
            file_handle = os.open(self._container_registry_file, flags, 0600)
        except OSError as e:
            if e.errno == errno.EEXIST:  # Failed as the file already exists.
                self._logger.info('Found existent registry file: '
                    + self._container_registry_file)

                ## Checking right permissions.
                reg_stats = os.stat(self._container_registry_file)
                if not bool((reg_stats[ST_MODE] & S_IRWXG) or (reg_stats[ST_MODE] \
                    & S_IRWXO)):

                    with open(self._container_registry_file) as registry:

                        ## Feed the registry.
                        for line in registry:
                            self._container_registry.append(line.rstrip('\n'))
                else:
                    logging.error('Wrong permissions on %s, ignoring it.' \
                        % self._container_registry_file)

            else:  # Something unexpected went wrong so reraise the exception.
                self._logger.debug('Failed to get a file descriptor for %s, '
                    + ' nor existing .reg file. Traceback follows.' \
                    % self._container_registry_file)
                self._logger.error(e)

                os.exit(-1)

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

                    # with open(self._container_registry_file, 'a') as registry:
                    #    registry.write(c_id + '\n')
                    self._registry_file_update()

                    return c_id

                except requests.exceptions.ConnectionError as e:
                    self._logger.debug('Conn. Error: couldn\'t find a proper socket.'
                        + ' Is the Docker daemon running correctly?'
                        + ' Check if /var/run/docker.sock exists.' )
                    self._logger.error(e)

                    os.exit(-1)

                except requests.exceptions.HTTPError as e:
                    self._logger.debug('Bad answer from server.')
                    self._logger.error(e)

                    os.exit(-1)

        except ValueError as e:
            self._logger.debug('JSON ValueError: parse error. Please check the ' \
                + 'consistency of your %s file.' % self._container_conf_file)
            self._logger.error(e)

            os.exit(-1)

        except IOError as e:
            self._logger.debug('IOError: %s file not found.' % self._container_conf_file)
            self._logger.error(e)

            os.exit(-1)

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

            os.exit(-1)

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
                                + self._socket_path + '/containers/' + id)
                            request.raise_for_status()

                            self._logger.info('Deleted: %s since its status was: %s' \
                                % ( listjson[i]['Id'][0:12], listjson[i]['Status']))

                            tmp_reg = self._container_registry
                            tmp_reg = [x for x in tmp_reg if x != id]
                            self._container_registry = tmp_reg

                            self._registry_file_update()
                            
                            ## Synchronize the backup file.
                            # tmp_list = []
                            # with open(self._container_registry_file, 'r') as registry:
                            #     tmp_list = registry.readlines()
                            # with open(self._container_registry_file, 'w') as registry:
                            #     for line in tmp_list:
                            #         if line != id:
                            #             registry.write(line)

                        except requests.exceptions.HTTPError as e:
                            self._logger.debug('HTTPError: ' + request.content)
                            self._logger.error(e)

                            os.exit(-1)

        except requests.exceptions.ConnectionError:
            self._logger.error('Connection Err: can\'t find a proper socket.' \
                + ' Is the docker daemon running correctly?' \
                + ' Check if /var/run/docker.sock exists.' )

            os.exit(-1)

        except requests.exceptions.HTTPError as e:
            self._logger.debug('Bad answer from server.')
            self._logger.error(e)

            os.exit(-1)


    def _container_respawner(self):
        pass





manager = ContainerManager()
manager._setup_logger()
manager._setup_from_conf('conf/manager.conf')
manager._list_container()
ID = manager._build_container()
manager._start_container(ID)
manager._container_reaper()
