#!/bin/bash

DEST=/opt/plancton/git
echo "-------------------"
echo "|Runscript called.|"
echo "-------------------"
[[ ! -e $DEST/.git ]] && git clone https://github.com/mconcas/plancton $DEST
cd $DEST
git clean -f -d
git clean -fX -d
git remote update -p
git fetch
git fetch --tags
git reset --hard origin/master

export PATH=$PATH:$DEST/bin
planctonctl start
