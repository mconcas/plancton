#!/usr/bin/env bash

Daemondir="/opt/plancton"
Rundir="$Daemondir/run"
Basename=`basename $0`
Daemonuser="plancton"

function super() {
   su -c "$*"
}
function welcome() {
   echo
   echo "<< Plancton Installer script >>"
   echo
}
function testcmd() {
   Location=`command -v $1`
   if [ $? -eq 0 ]; then
      echo -e "$1 --> \"$Location\""
      return 0
   else
      echo "$1 --> MISSES"
      return 1
   fi
}
function testreq() {
   # check prereqs, test with testcmd().
   echo "Testing requisites. "
   for i in $@; do
      testcmd $i
   done
}

function main() {
   welcome
   testreq git pip docker
   super "useradd -d $Daemondir -g docker $Daemonuser"
   python -c "import docker" || super "pip install docker-py"
   su -c $Daemonuser "echo -e '@reboot $Rundir/run.sh' \| crontab - "
}

main
