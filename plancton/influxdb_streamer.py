# -*- coding: utf-8 -*-
import os, requests, logging
from datetime import datetime

# Class used to stream data to an InfluxDB database.
# Exceptions and logging are supposed to be managed by the calling module.
# Plancton must handle every exception.

class InfluxDBStreamer():
  __version__ = "0.1"
  def __init__(self, baseurl, database):
    if baseurl.startswith("insecure_https:"):
      self.ssl_verify = False
      self.real_baseurl = baseurl[9:]
    else:
      self.ssl_verify = True
      self.real_baseurl = baseurl
    self.baseurl = baseurl
    self.database = database
    self.logctl = logging.getLogger("influxdb_streamer")
    self._headers_query = {"Content-type": "application/json", "Accept": "text/plain"}
    self._headers_write = {"Content-type": "application/octet-stream", "Accept": "text/plain"}

  def create_db(self):
    try:
      self.logctl.debug("Creating database: %s" % self.database)
      r = requests.get(self.real_baseurl + "/query",
                       headers=self._headers_query,
                       params={ "q": "CREATE DATABASE \"%s\"" % self.database,
                                "db": self.database },
                       timeout=5,
                       verify=self.ssl_verify)
      self.logctl.debug("Creating database %s returned %d" % (self.database, r.status_code))
      r.raise_for_status()
      return True
    except requests.exceptions.RequestException as e:
      self.logctl.error("Error creating database: %s" % e)
    return False

  def __call__(self, series, tags, fields):
    # Line protocol: https://docs.influxdata.com/influxdb/v1.0/write_protocols/line_protocol_tutorial/
    fields = dict(map(lambda (k,v): (k, '"%s"'%v if isinstance(v, basestring) else v), fields.iteritems()))
    data_string = series + "," +                                              \
                  ",".join(["%s=%s" % (x,tags[x]) for x in tags]) + " " +     \
                  ",".join(["%s=%s" % (x,fields[x]) for x in fields]) + " " + \
                  str(int((datetime.utcnow()-datetime.utcfromtimestamp(0)).total_seconds()*1000000000))
    self.logctl.debug("Sending line to database %s: %s" % (self.database, data_string))

    db_created = False
    while True:
      try:
        r = requests.post(self.real_baseurl+"/write",
                          headers=self._headers_write,
                          params={ "db": self.database },
                          data=data_string.encode("utf-8"),
                          timeout=5,
                          verify=self.ssl_verify)
        self.logctl.debug("Sending data returned %d" % r.status_code)
        r.raise_for_status()
        return True
      except requests.exceptions.RequestException as e:
        if db_created:
          self.logctl.error("Error sending data: %s" % e)
          return False
        else:
          self.logctl.debug("Error sending data: %s - trying to create database" % e)
          if not self.create_db():
            return False
          db_created = True

  def __hash__(self):
    return hash(self.baseurl + "#" + self.database)

  def __eq__(self, rh):
    return self.baseurl == rh.baseurl and self.database == rh.database
