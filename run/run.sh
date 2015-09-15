#!/bin/bash

DEST=/opt/plancton/git
echo "-------------------"
echo "|Runscript called.|"
echo "-------------------"
[[ ! -e $DEST/.git ]] && git clone https://github.com/mconcas/plancton $DEST
cd $DEST

git reset --hard origin/master

export PATH=$PATH:$DEST/bin
export PYTHONPATH=$PYTHONPATH:$DEST/pylibs

planctonctl start
