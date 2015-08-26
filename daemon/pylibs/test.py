#!/bin/python

# -*- coding: utf-8 -*-

from plancton import Plancton

pl = Plancton('plancton','/tmp/plancton.pid','/tmp/plancton')
pl.SetupLogFiles()
pl.GetOnlineConf()
pl.GetSetupInfo()
# n = pl.ListContainers()
# if n <= 4:
    # pl.DeployContainer()
#
# pl.ControlContainers()
pl.start()
print 'fatto.'
