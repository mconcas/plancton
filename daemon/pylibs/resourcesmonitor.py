#! /bin/python

# -*- coding: utf-8 -*-

import psutil
import pprint

class Resourcesmonitor():
    def __init__(self):
        self._system_status = self.SystemStatus()

    def SystemStatus(self):
        systat = {}
        systat['Cores'] = {

            # number of logical and physical cores.
            'logical'  : psutil.cpu_count(logical=True),
            'physical' : psutil.cpu_count(logical=False)

            }

        systat['VMemory'] = {

            # RAM
            'total'     : psutil.virtual_memory()[0]

            }

        systat['Memory'] = {

            'swap' : psutil.swap_memory()[0],
            '/'    : {

                # Check the root directory where docker is supposed to store the images and the
                # containers.
                'total'     : psutil.disk_usage('/')[0],
                'available' : psutil.disk_usage('/')[1]

                }
            }

        return systat

rm = Resourcesmonitor()
# print rm._system_status
pp = pprint.PrettyPrinter(indent=4)
pp.pprint(rm._system_status)
