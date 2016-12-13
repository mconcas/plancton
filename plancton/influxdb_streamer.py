# -*- coding: utf-8 -*-
import os, requests, logging
from datetime import datetime

# Class used to stream data to an InfluxDB database.
# Exceptions and logging are supposed to be managed by the calling module.
# Plancton must handle every exception.

class InfluxDBStreamer():
  __version__ = "0.1"
  def __init__(self, baseurl, database):
    self.baseurl = baseurl
    self.database = database
    self.logctl = logging.getLogger("influxdb_streamer")
    self.db_is_created = False
    self._headers_query = {"Content-type": "application/json", "Accept": "text/plain"}
    self._headers_write = {"Content-type": "application/octet-stream", "Accept": "text/plain"}

  def create_db(self):
    try:
      self.logctl.debug("Creating database: %s" % self.database)
      r = requests.get(self.baseurl + "/query",
                       headers=self._headers_query,
                       params={ "q": "CREATE DATABASE \"%s\"" % self.database,
                                "db": self.database })
      self.logctl.debug("Creating database %s returned %d" % (self.database, r.status_code))
      r.raise_for_status()
      self.db_is_created = True
    except requests.exceptions.RequestException as e:
      self.logctl.error("Error creating database: %s" % e)
      self.db_is_created = False
    return self.db_is_created

  def __call__(self, series, tags, fields):
    if not self.db_is_created:
      if not self.create_db():
        return False
    # Line protocol: https://docs.influxdata.com/influxdb/v1.0/write_protocols/line_protocol_tutorial/
    fields = dict(map(lambda (k,v): (k, '"%s"'%v if isinstance(v, basestring) else v), fields.iteritems()))
    data_string = series + "," +                                              \
                  ",".join(["%s=%s" % (x,tags[x]) for x in tags]) + " " +     \
                  ",".join(["%s=%s" % (x,fields[x]) for x in fields]) + " " + \
                  str(int((datetime.utcnow()-datetime.utcfromtimestamp(0)).total_seconds()*1000000000))
    self.logctl.debug("Sending line to database %s: %s" % (self.database, data_string))

    try:
      r = requests.post(self.baseurl+"/write",
                        headers=self._headers_write,
                        params={ "db": self.database },
                        data=data_string.encode("utf-8"))
      self.logctl.debug("Sending data returned %d" % r.status_code)
      r.raise_for_status()
      return True
    except requests.exceptions.RequestException as e:
      self.logctl.error("Error sending data: %s" % e)
      self.db_is_created = False
      return False
