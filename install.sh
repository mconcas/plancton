#!/usr/bin/env bash

Daemondir="/opt/plancton"
Rundir="$Daemondir/git/run"
Basename=`basename $0`
Daemonuser="plancton"

function welcome() {
   echo
   echo "<< Plancton Installer script >>"
   echo
}
function testcmd() {
   Location=`command -v $1`
   if [ $? -eq 0 ]; then
      return 0
   else
      return 1
   fi
}
function testreq() {
   # check prereqs, test with testcmd().
   echo "Testing requisites..."
   for i in $@; do
      testcmd $i
   done
}

function super() {
   if [[ $(id -u) != "0" ]]; then
      if sudo -h > /dev/null 2>&1; then
         sudo -sE "$@"
      else
         su -c "$*"
      fi
   fi
}

function main() {
   welcome
   testreq git pip docker
   echo "adding $Daemonuser..."
   super useradd -d $Daemondir -g docker $Daemonuser
   echo "installing docker-py if not present..."
   super python -c "import docker" || pip install docker-py
   echo "adding cronjob to plancton's crontab..."
   super bash -c 'echo "@reboot /opt/plancton/run/run.sh" > /tmp/tempcron'
   super crontab -u $Daemonuser /tmp/tempcron
   super rm /tmp/tempcron
   super rm -Rf $Daemondir
   super mkdir -p $Daemondir && chown -R $Daemonuser $Daemondir
   echo "cloning plancton files to $Daemondir..."
   super git clone https://github.com/mconcas/plancton $Daemondir/git

   super runuser $Daemonuser -l $Rundir/run.sh
}

main
