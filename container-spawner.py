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

class ContainerManager(object):
    __version__ = '0.0.2'

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

        ## ATM this is hardcoded.
        self._container_registry_file = '/tmp/container-manager.reg'

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
                id = str(self._jsoncontlist[i]['Id'])
                if id in self._container_registry:
                    owner = 'spawner-agent'
                    owned_containers+=1
                else:
                    owner = 'others       '
                status = self._jsoncontlist[i]['Status']
                if not status:
                    status = 'Created (Not Running)'
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

    ## _registry_file_update: performs an update of the backup registry file.
    #
    # @return nothing
    def _registry_file_update(self):

        flags = os.O_CREAT | os.O_EXCL | os.O_RDONLY
        try:
            file_handle = os.open(self._container_registry_file, flags, 0600)
        except OSError as e:
            if e.errno == errno.EEXIST:  # Failed as the file already exists.
                self._logger.info('Updating registry file: '
                    + self._container_registry_file)

                ## Checking right permissions.
                reg_stats = os.stat(self._container_registry_file)
                if not bool((reg_stats[ST_MODE] & S_IRWXG) or (reg_stats[ST_MODE] \
                    & S_IRWXO)):
                    with open(self._container_registry_file, 'w') as registry:

                        ## Feed the registry.
                        for line in self._container_registry:
                            registry.write(line + '\n')
                else:
                    self._logger.warning('Wrong permissions on %s, not updating...' \
                        % self._container_registry_file)

            else:  # Something unexpected went wrong so reraise the exception.
                self._logger.debug('Failed to get a file descriptor for %s. '
                    + ' Traceback follows.' \
                    % self._container_registry_file)
                self._logger.error(e)

                sys.exit(-1)

    ## _update_registry_from_file: performs an update from the backup registry file.
    #
    # @return nothing
    def _update_registry_from_file(self):
        ####==============================================================================
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
        ####==============================================================================

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

                    with open(self._container_registry_file, 'r') as registry:

                        ## Feed the registry.
                        for line in registry:
                            self._container_registry.append(line.rstrip('\n'))
                else:
                    self._logger.warning('Wrong permissions on %s, ignoring it.' \
                        % self._container_registry_file)

            else:  # Something unexpected went wrong so reraise the exception.
                self._logger.debug('Failed to get a file descriptor for %s. '
                    + ' Traceback follows.' \
                    % self._container_registry_file)
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
                ## self._logger.info(str(data))
                headers = {'content-type':'application/json', 'Accept':'text/plain'}
                try:
                    request = self._session.post('http+unix://' + self._socket_path \
                        + '/containers/create', data=json.dumps(data), headers=headers)
                    request.raise_for_status()
                    self._logger.debug('Successfully created: ' + str(request.content))
                    c_id = str(json.loads(request.content)['Id'])

                    if not c_id in self._container_registry:
                        self._logger.debug('Appending %s to inner list.' \
                            % c_id[0:12])
                        self._container_registry.append(c_id)

                    ## Before returning is a good choice to backup the registry to the
                    #  .reg file. Since a container creation is not done with an high
                    #  frequency this should not cause overhead issues.
                    self._registry_file_update()

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

        self._logger.info('Reading from local registry list...')
        # listjson = json.loads(request.content)
        # for i in range(0, len(listjson)):
        for id in self._container_registry:
            try:
                request = self._session.get('http+unix://' + self._socket_path
                    + '/containers/' + id[0:12] + '/json')
                request.raise_for_status()

                jsonctnt = json.loads(request.content)
                if not jsonctnt['State']['Running']:
                     try:
                         request = self._session.delete('http+unix://' \
                             + self._socket_path + '/containers/' + id)
                         request.raise_for_status()

                         self._logger.info('Deleted: %s, its running status was: %s' \
                             % ( id, jsonctnt['State']['Running']))

                         tmp_reg = self._container_registry
                         tmp_reg = [x for x in tmp_reg if x != id]
                         self._container_registry = tmp_reg
                         
                     except requests.exceptions.HTTPError as e:
                         self._logger.debug('HTTPError: ' + request.content)
                         self._logger.error(e)
                         self._logger.warning('It couldn\'t be possibile to delete '
                            + ' container: ' + id )

            except requests.exceptions.ConnectionError:
                self._logger.error('Connection Err: can\'t find a proper socket. \
                    Is the docker daemon running correctly? \
                    Check if /var/run/docker.sock exists.' )

                self._logger.error('CAUTION: REGISTRY NOT UPDATED! Is recomended to \
                    manually delete your containers and the registry itself.')

                sys.exit(-1)

            except requests.exceptions.HTTPError as e:
                self._logger.warning(e)
                self._logger.info('Bad answer from server, probably this id doesn\'t'
                    + 'exist anymore, removing from list.')

                tmp_reg = self._container_registry
                tmp_reg = [x for x in tmp_reg if x != id]
                self._container_registry = tmp_reg

        self._registry_file_update()

#=========================================================================================
def container_respawner(cont_num):
    manager = ContainerManager()
    manager._setup_logger()
    manager._setup_from_conf('conf/manager.conf')
    while(1):
        time.sleep(10)
        owning = manager._list_container()

        if (owning < cont_num):
            ID = manager._build_container()
            manager._start_container(ID)

        manager._container_reaper()

container_respawner(3)
