#!/bin/bash

DEST=/opt/plancton/git
echo "-------------------"
echo "|Runscript called.|"
echo "-------------------"
function main() {
    if [ ! -e $DEST/.git ]; then 
        git clone https://github.com/mconcas/plancton $DEST
    fi
    cd $DEST
    git reset --hard origin/master
    export PATH=$PATH:$DEST/bin
    export PYTHONPATH=$PYTHONPATH:$DEST/pylibs
    planctonctl start
    
    return 0
}

main
