#!/usr/bin/env bash

export Daemondir="/opt/plancton"
export Rundir="$Daemondir/git/run"
export Basename=`basename $0`
export Daemonuser="plancton"

function welcome() {
   echo
   echo "<< Plancton Installer script >>"
   echo
}
function testcmd() {
   command -v "$@" > /dev/null 2>&1
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
   else
      bash -c "$*"
   fi
}

function main() {
   bash_c = 'bash -c'
   if [ "$USER" != 'root' ]; then
		if testcmd sudo; then
			bash_c='sudo -E sh -c'
		elif testcmd su; then
			bash_c='su -c'
		else
			cat >&2 <<-'EOF'
			Error: this installer needs the ability to run commands as root.
			No "sudo" nor "su" available to make this happen.
			EOF
			exit 1
		fi
	fi

   welcome
   testreq git pip docker
   echo "adding group docker..."
   $bash_c 'getent group docker || groupadd docker'
   echo "adding $Daemonuser..."
   $bash_c "useradd -d $Daemondir -g docker $Daemonuser"
   echo "installing docker-py if not present..."
   $bash_c 'python -c "import docker" || pip install docker-py'
   echo "adding cronjob to plancton's crontab..."
   $bash_c 'echo "@reboot /opt/plancton/run/run.sh" > /tmp/tempcron'
   $bash_c "crontab -u $Daemonuser /tmp/tempcron"
   $bash_c "rm /tmp/tempcron; rm -Rf $Daemondir; mkdir -p $Daemondir"
   echo "cloning plancton files to $Daemondir..."
   $bash_c "git clone https://github.com/mconcas/plancton $Daemondir/git"
   $bash_c "chown -R $Daemonuser $Daemondir"

   $bash_c "su $Daemonuser -c $Daemondir/git/run/run.sh"
}

# Entry point
main
unset Daemondir
unset Rundir
unset Basename
unset Daemonuser
