#!/usr/bin/python
# -*- coding: utf-8 -*-

import logging, json, time
# log_file='/tmp/container-spawner.log'


try:
    import requests_unixsocket, requests.exceptions
except ImportError, e:
    logging.error('Couldn\'t find the requests_unixsocket module. '
    + 'Please check that it is installed or, '
    + 'at least, in the working directory. '
    + str(e))

    exit(-1)


def container_list_counter(socket_url, session):
    """
    This function wraps some requests and return the number of running
    containers
    """
    try:
        request = session.get(socket_url + 'containers/json?all=1')
        request.raise_for_status()
        logging.info('[C_COUNTER]: Checking container list...')
        list_json = json.loads(request.content)
        logging.info('[C_COUNTER]: Found %r containers in the list.' \
            % len(list_json))
        for i in range(0, len(list_json)):
            if not list_json[i]['Status']:
                status = 'Created'
            else:
                status = list_json[i]['Status']

            logging.info('\t |- Id: ' + list_json[i]['Id'] + ' |- Status: ' \
            + str(status))

        return len(list_json)

    except requests.exceptions.ConnectionError:
        logging.error('Conn. Error: couldn\'t find a proper socket. '
            + ' Is the Docker daemon running correctly?'
            + ' Check if /var/run/docker.sock exists.' )

        exit(-1)

    except requests.exceptions.HTTPError as e:
        print 'HTTPError raised, please check ' + filename + \
        ' for further informations.'
        logging.error('Bad answer from server. \n')
        logging.error('<EXCEPTION DUMP> ' + str(e) + '\n')

        exit(-1)

def container_builder(socket_url, session, jsoncfg_file):
    """
    This function should create a container. It returns, if success,
    the container id.
    """
    try:
        with open(jsoncfg_file) as data_file:
            data = json.load(data_file)
        #    logging.debug('Trying to create a container with JSON data:' \
        #        + str(data))
            headers = {'content-type':'application/json', 'Accept':'text/plain'}
            try:
                request = session.post(socket_url + 'containers/create', \
                data = json.dumps(data), headers=headers)
                request.raise_for_status()
                logging.debug('Successfully created: ' + str(request.content))

                return json.loads(request.content)['Id']

            except requests.exceptions.ConnectionError:
                print 'Connection exception raised, check ' + filename + \
                ' for further informations.'
                logging.error('Conn. Error: couldn\'t find a proper socket.'
                    + ' Is the docker daemon running correctly?'
                    + ' Check if /var/run/docker.sock exists.' )

                exit(-1)

            except requests.exceptions.HTTPError as e:
                print 'HTTPError raised, please check ' + filename + \
                ' for further informations.'
                logging.error('Bad answer from server. \n')
                logging.error('<EXCEPTION DUMP> ' + str(e) + '\n')

                exit(-1)

    except ValueError as e:
        logging.error('JSON ValueError: parse error. Please check the '
            + 'consistency of your ' + jsoncfg_file + ' file.')
        logging.error('<EXCEPTION DUMP> ' + str(e) + '\n')

        exit(-1)

    except IOError as e:
        logging.error('IOError: ' + jsoncfg_file + ' file not found.')
        logging.error('<EXCEPTION DUMP> ' + str(e) + '\n')

        exit(-1)

def container_starter(socket_url, session, id):
    """
    This function wraps some requests and starts a container by getting its id.
    """
    try:
        request = session.post(socket_url + 'containers/' + id + '/start')
        request.raise_for_status()
    except requests.exceptions.HTTPError as e:
        logging.error('<EXCEPTION DUMP> ' + str(e) + '\n')
        logging.error('HTTPError: ' + request.content)

        exit(-1)

def container_reaper(socket_url, session):
    """
    Wipes out all the Created and Exited containers.
    """
    try:
        request = session.get(socket_url + 'containers/json?all=1')
        request.raise_for_status()
        logging.info('[C_REAPER]: Checking container list...')
        list_json = json.loads(request.content)
        for i in range(0, len(list_json)):
            if not list_json[i]['Status'] or 'Exited' in list_json[i]['Status']:
                try:
                    request = session.delete(socket_url + 'containers/' + \
                        list_json[i]['Id'])
                    logging.info('[C_REAPER]: Deleted: ' + \
                        list_json[i]['Id'][0:12] + ' since its status was: ' + \
                            list_json[i]['Status'])
                except requests.exceptions.HTTPError as e:
                    logging.error('[C_REAPER]: <EXCEPTION DUMP> ' + str(e) +\
                        '\n')
                    logging.error('[C_REAPER]: HTTPError: ' + request.content)

                    exit(-1)

    except requests.exceptions.ConnectionError:
        logging.error('[C_REAPER]: Connection Err: can\'t find a proper socket.'
            + ' Is the docker daemon running correctly?'
            + ' Check if /var/run/docker.sock exists.' )

        exit(-1)

    except requests.exceptions.HTTPError as e:
        logging.error('[C_REAPER]: Bad answer from server. \n')
        logging.error('[C_REAPER]: <EXCEPTION DUMP> ' + str(e) + '\n')

        exit(-1)



# container_list_counter(socket_prefix, session)
# ID = container_builder(socket_prefix, session, 'worker_centos6.json')
# container_starter(socket_prefix, session, ID)
# container_reaper(socket_prefix, session)

def container_spawner(n_container=1,
    socket_prefix='http+unix://%2Fvar%2Frun%2Fdocker.sock/',
    cfg_file='conf/worker_centos6.json',
    log_file='/tmp/container-spawner.log'):
        """
        Wrapper-fat-burrito
        """
        logging.basicConfig(filename=log_file, level=logging.DEBUG)
        session = requests_unixsocket.Session()
        while(1):
            count = container_list_counter(socket_prefix, session)
            if count is 0:
                ID = container_builder(socket_prefix, session, cfg_file)
                container_starter(socket_prefix, session, ID)
            else:
                ## Try to wipe out.
                container_reaper(socket_prefix, session)


container_spawner()
