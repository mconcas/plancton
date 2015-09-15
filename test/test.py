#!/bin/python

# -*- coding: utf-8 -*-

from plancton import Plancton
import time

pl = Plancton('plancton','/tmp/plancton.pid','/tmp/plancton')
pl.start()


# self.ControlContainers()
# delta = time.time() - self._last_update_time
# if (delta >= update_every):
    # self.PullImage()
    # self.GetOnlineConf()
    # self._last_update_time = time.time()
# If statement just to avoid continuously spamming into logfile. Thus only when an actual
# modification is performed it report a new list.
# if not self._list_up_to_date:
    # self.ListContainers(quiet=False)
    # self._list_up_to_date = True
# else:
    # self.ListContainers(quiet=True)
    # self._list_up_to_date = True
#
# if self._owned_containers <= w_containers_thresh:
    # self.DeployContainer()
