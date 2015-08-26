## @file daemon.py
#  Create a generic pythonic daemon.
#
#  Should work with Python 3 too.
#
#  Originally inspired by [this code](http://www.jejik.com/files/examples/daemon3x.py) but then
#  heavily improved.
# -*- coding: utf-8 -*-
import sys, os, time, atexit, signal
import logging, logging.handlers

## @class Daemon
#  Abstract pythonic daemon class.
#
#  **Usage:** subclass it and override the `run()` method. Use `start()` to start it in background,
#  `stop()` to terminate it. Class must be initialized by providing a pidfile path.
#
#  Example code:
#
#  ~~~{.py}
#  class MyClass(Daemon):
#    def run(self):
#      # do things here
#
#  prog = MyClass('mydaemon', '/tmp/myclass.pid')
#  prog.start()
#  ~~~
class Daemon(object):

    ## Constructor.
    #
    #  @param name    Arbitrary nickname for the daemon
    #  @param pidfile Full path to PID file. Path must exist
    def __init__(self, name, pidfile):
        ## Path to the file where to write the current PID
        self.pidFile = pidfile
        ## Daemon's nickname
        self.name = name
        ## PID of daemon
        self.pid = None
        ## Custom logger for control messages
        self.logctl = logging.getLogger( self.name )
        logctl_formatter = logging.Formatter(name + ': %(levelname)s: %(message)s')
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logctl_formatter)
        self.logctl.addHandler(stderr_handler)

        # Note that syslog_handler might be None (for instance if running inside a container)
        syslog_handler = self.getSyslogHandler()
        if syslog_handler is not None:
            syslog_handler.setFormatter(logctl_formatter)
            self.logctl.addHandler(syslog_handler)

        self.logctl.setLevel(logging.DEBUG)

    ## Gets a syslog handler on Linux and OS X.
    #
    #  @return A SysLogHandler, or None in case no syslog facility is found
    def getSyslogHandler(self, log_level=logging.DEBUG, formatter=None):
        syslog_address = None
        for a in [ '/var/run/syslog', '/dev/log' ]:
            if os.path.exists(a):
                syslog_address = a
                break

                if syslog_address:
                    syslog_handler = logging.handlers.SysLogHandler(address=syslog_address)
                    return syslog_handler

        return None

    ## Write PID to pidfile.
    def writePid(self):
        with open(self.pidFile, 'w') as pf:
            pf.write( str(self.pid) + '\n' )

    ## Read PID from pidfile.
    def readPid(self):
        try:
            with open(self.pidFile, 'r') as pf:
                self.pid = int( pf.read().strip() )
        except (IOError, ValueError):
            self.pid = None

    ## Delete pidfile.
    def delPid(self):
        if os.path.isfile(self.pidFile):
            os.remove(self.pidFile)

    ## Determines if a daemon with the current PID is running by sending a dummy signal.
    #
    #  @return True if running, False if not
    def isRunning(self):
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        return True

    ## Daemonize class. Uses the [Unix double-fork technique](http://stackoverflow.com/questions/88138
    #8/what-is-the-reason-for-performing-a-double-fork-when-creating-a-daemon).
    #
    #  @return Current PID when exiting from a parent, 0 when exiting from the child, a negative
    #          number when `fork()` fails
    def daemonize(self):
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



    ## Start the daemon. Daemon is sent to background then started.
    #
    #  @return True if the final status obtained is that the daemon is running: this means that True
    #          is returned even in the case where the daemon was already running. False is returned
    #          otherwise
    def start(self):
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

    ## Run the daemon not as a daemon.
    #
    #  @return The daemon's exit code (int) as returned from its run() function
    def startForeground(self):
        # Check for a pidfile to see if the daemon already runs
        self.readPid()
        if self.isRunning():
            self.logctl.info('already running with PID %d' % self.pid);
            return True
        self.trapExitSignals(self.exitHandlerReal)
        return self.run()

    ## Returns the status of the daemon. If daemon is running, its PID is printed.
    #
    #  @return True if daemon is running, False if not
    def status(self):
        self.readPid()
        if self.isRunning():
            self.logctl.info('running with PID %d' % self.pid)
            return True
        else:
            self.logctl.info('not running')
            return False

    ## Stop the daemon.
    #
    #  An attempt to kill the daemon is performed for 30 seconds sending **signal 15 (SIGTERM)**: if
    #  the daemon is implemented properly, it will perform its shutdown operations and it will exit
    #  gracefully.
    #
    #  If the daemon is still running after this termination attempt, **signal 9 (KILL)** is sent, and
    #  daemon is abruptly terminated.
    #
    #  Note that this attempt might fail as well.
    #
    #  @return True on success, where "success" means that the final status is that the daemon is not
    #          running: an example of success is when the daemon wasn't running and `stop()` is
    #          called. False is returned otherwise
    def stop(self):
        self.logctl.info('stopping')
        # Get the pid from the pidfile
        self.readPid()
        if not self.isRunning():
            self.logctl.info('not running')
            return True
        # Try killing the daemon process gracefully
        kill_count = 0
        kill_count_threshold = 30
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

        self.logctl.warning('force-killed')
        return True

    ## Maps exit signals to a function.
    def trapExitSignals(self, func):
        for s in [ signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT ]:
            signal.signal(s, func)

    ## Signal handler for exit signals that does nothing.
    #
    #  Necessary to prevent the real signal handler from being called more than once.
    def exitHandlerNoOp(self, signum, frame):
        pass

    ## Real exit handler.
    #
    #  Calls the `onexit()` function, that must be overridden by subclasses, and exits the program if
    #  it returns True. Exit signals are temporarily mapped to noop while handling one signal, and the
    #  mapping is restored in case exiting is cancelled.
    def exitHandlerReal(self, signum, frame):
        self.trapExitSignals(self.exitHandlerNoOp)
        if self.onexit():
            # Exit was confirmed
            sys.exit(0)
        else:
            # Exit was cancelled
            self.trapExitSignals(self.exitHandlerReal)

    ## Program's exit function, to be overridden by subclasses.
    #
    #  This function is called when an exit signal is caught: it should be used to implement cleanup
    #  functions.
    #
    #  @return When returning True, exiting continues, when returning False exiting is cancelled
    def onexit(self):
        return True

    ## Program's main loop, to be overridden by subclasses.
    #
    #  It will be called after the process has been daemonized by `start()`.
    #
    #  @return It should return an integer in the range 0-255
    def run(self):
        return 0
