# -*- coding: utf-8 -*-

##
#  Create an abstract pythonic daemon.
#  Originally inspired by [this code](http://www.jejik.com/files/examples/daemon3x.py) but then
#  heavily improved.
##
import atexit
import logging, logging.handlers
import os
import signal
import sys
import time


class Daemon(object):
    """ Abstract pythonic daemon class.
        @param name    Arbitrary nickname for the daemon
        @param pidfile Full path to PID file. Path must exist
    """

    def __init__(self, name, pidfile):
        ## Path to the file where to write current PID
        self.pidFile = pidfile
        self.name = name
        ## PID of daemon
        self.pid = None

        ## Custom logger for control messages
        self.logctl = logging.getLogger( self.name )
        logctl_formatter = logging.Formatter(name + ': %(levelname)s: %(message)s')
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logctl_formatter)
        self.logctl.addHandler(stderr_handler)

        # syslog_handler might be None (for instance if running inside a container)
        syslog_handler = self.getSyslogHandler()
        if syslog_handler is not None:
            syslog_handler.setFormatter(logctl_formatter)
            self.logctl.addHandler(syslog_handler)

        self.logctl.setLevel(logging.DEBUG)

    def getSyslogHandler(self, log_level=logging.DEBUG, formatter=None):
        """ Gets a syslog handler on Linux and OS X.
            @return A SysLogHandler, or None in case no syslog facility is found
        """
        syslog_address = None
        for a in [ '/var/run/syslog', '/dev/log' ]:
            if os.path.exists(a):
                syslog_address = a
                break

                if syslog_address:
                    syslog_handler = logging.handlers.SysLogHandler(address=syslog_address)
                    return syslog_handler
        return None

    def writePid(self):
        """ Write PID to pidfile."""
        with open(self.pidFile, 'w') as pf:
            pf.write( str(self.pid) + '\n' )

    def readPid(self):
        """ Read PID from pidfile."""
        try:
            with open(self.pidFile, 'r') as pf:
                self.pid = int( pf.read().strip() )
        except (IOError, ValueError):
            self.pid = None

    def delPid(self):
        """ Delete pidfile."""
        if os.path.isfile(self.pidFile):
            os.remove(self.pidFile)

    def isRunning(self):
        """ Send 0 signal and check whether a process with that
            given pd is running.
        """
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        else:
            return True

    def daemonize(self):
        """ Daemonize method. Use the Unix double-fork technique
            @return Current PID when exiting from a parent, 0 when exiting from the child, a negative
            number when `fork()` fails.
        """
        try:
            pid = os.fork()
            if pid > 0:
                # exit from first parent: return execution to caller
                return pid
        except OSError as e:
            self.logctl.critical('first fork failed: %s' % e)
            return -1

        # decouple from parent environment
        os.chdir('/')
        os.setsid()
        os.umask(0)

        # do second fork
        try:
            pid = os.fork()
            if pid > 0:
                # exit from second parent
                sys.exit(0)
        except OSError as e:
            self.logctl.critical('second fork failed: %s' % e)
            sys.exit(1)

        # we are in the daemon: know our pid
        self.pid = os.getpid()

        # redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        si = open(os.devnull, 'r')
        so = open(os.devnull, 'a+')
        se = open(os.devnull, 'a+')

        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())

        # write pidfile and schedule deletion
        atexit.register(self.delPid)
        self.writePid()

        return 0

    def start(self):
        """ Start the daemon. Daemon is sent to background then started.
            @return True if the final status obtained is that the daemon is running: this means that True
            is returned even in the case where the daemon was already running. False is returned
            otherwise
        """
        self.logctl.info('starting')
        # Check for a pidfile to see if the daemon is already running.
        self.readPid()
        if self.isRunning():
            self.logctl.info('already running with PID %d' % self.pid);
            return True
        # Start the daemon
        pid = self.daemonize()
        if pid == 0:
            # child
            self.trapExitSignals(self.exitHandlerReal)
            try:
                self.run()
            except Exception as e:
                self.logctl.exception('An exception occurred: traceback follows')
                self.logctl.critical('Terminating abnormally...')
            return True  # never caught
        elif pid > 0:
            # main process
            time.sleep(2)
            return self.status()
        else:
            # error
            return False

    def status(self):
        """ Returns the status of the daemon. If daemon is running, its PID is printed.
            @return True if daemon is running, False if not
        """
        self.readPid()
        if self.isRunning():
            self.logctl.info('Running with PID %d' % self.pid)
            return True
        else:
            self.logctl.info('Not running')
            return False

    def stop(self):
        """ Stop the daemon.
            An attempt to kill the daemon is performed for 30 seconds sending **signal 15 (SIGTERM)**: if
            the daemon is implemented properly, it will perform its shutdown operations and it will exit
            gracefully.
            If the daemon is still running after this termination attempt, **signal 9 (KILL)** is sent, and
            daemon is abruptly terminated.
            Note that this attempt might fail as well.
            @return True on success, where "success" means that the final status is that the daemon is not
            running: an example of success is when the daemon wasn't running and `stop()` is
            called. False is returned otherwise.
        """

        self.logctl.info('stopping, this may take a while...')
        # Get the pid from the pidfile
        self.readPid()
        if not self.isRunning():
            self.logctl.info('not running')
            return True
        # Try killing the daemon process gracefully
        kill_count = 0
        kill_count_threshold = 60
        try:
            while kill_count < kill_count_threshold:
                os.kill(self.pid, signal.SIGTERM)
                time.sleep(1)
                kill_count = kill_count + 1

            # force-kill
            os.kill(self.pid, signal.SIGKILL)
            time.sleep(2)

        except OSError:
            self.logctl.info('exited gracefully')
            return True

        if self.isRunning():
            self.logctl.error('could not terminate')
            return False

        # It's likely this will never be printed.
        self.logctl.warning('force-killed')
        return True

    def trapExitSignals(self, func):
        """ Maps exit signals to a function. """
        for s in [ signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT ]:
            signal.signal(s, func)

    def exitHandlerNoOp(self, signum, frame):
        """ Signal handler for exit signals that does nothing.
            Necessary to prevent the real signal handler from being called more than once.
        """
        pass

    def exitHandlerReal(self, signum, frame):
        """ Real exit handler.
            Calls the `onexit()` function, that must be overridden by subclasses, and exits the program
            if it returns True. Exit signals are temporarily mapped to noop while handling one signal,
            and the mapping is restored in case exiting is cancelled.
        """
        self.trapExitSignals(self.exitHandlerNoOp)
        if self.onexit():
            # Exit was confirmed
            sys.exit(0)
        else:
            # Exit was cancelled
            self.trapExitSignals(self.exitHandlerReal)

    def onexit(self):
        """ Program's exit function, to be overridden by subclasses.
            This function is called when an exit signal is caught: it should be used to implement cleanup
            functions.
            @return When returning True, exiting continues, when returning False exiting is cancelled
        """
        return True

    def run(self):
        """ Program's main loop, to be overridden by subclasses.
            It will be called after the process has been daemonized by `start()`.
            @return It should return an integer in the range 0-255
        """
        return 0
