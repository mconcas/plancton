# -*- coding: utf-8 -*-
import os
import requests
from datetime import datetime

# Class used to stream data to an InfluxDB database.
# Exceptions and logging are supposed to be managed by the calling module.
# Plancton must handle every exception.

class Streamer():
  __version__ = "0.1"
  def __init__(self, baseurl, database, logctl=None):
    self.baseurl = baseurl
    self.database = database
    self.logctl = logctl
    self.db_is_created = False
    self._headers_query = {"Content-type": "application/json", "Accept": "text/plain"}
    self._headers_write = {"Content-type": "application/octet-stream", "Accept": "text/plain"}

  def create_db(self):
    try:
      r = requests.get(self.baseurl + "/query",
                       headers=self._headers_query,
                       params={ "q": "CREATE DATABASE \"%s\"" % self.database,
                                "db": self.database })
      self.db_is_created = r.status_code >= 200 and r.status_code < 300
    except requests.exceptions.RequestException as e:
      if self.logctl: self.logctl.error("Error creating InfluxDB database: %s" % e)
      self.db_is_created = False
    return self.db_is_created

  def send(self, series, tags, fields):
    if not self.db_is_created:
      if not self.create_db():
        return False
    # Line protocol: https://docs.influxdata.com/influxdb/v1.0/write_protocols/line_protocol_tutorial/
    data_string = series + "," +                                              \
                  ",".join(["%s=%s" % (x,tags[x]) for x in tags]) + " " +     \
                  ",".join(["%s=%s" % (x,fields[x]) for x in fields]) + " " + \
                  str(int((datetime.utcnow()-datetime.utcfromtimestamp(0)).total_seconds()*1000000000))

    try:
      r = requests.post(self.baseurl+"/write",
                        headers=self._headers_write,
                        params={ "db": self.database },
                        data=data_string.encode("utf-8"))
      ok = r.status_code >= 200 and r.status_code < 300
    except requests.exceptions.RequestException:
      if self.logctl: self.logctl.error("Error sending data to InfluxDB: %s" % e)
      ok = False
      self.db_is_created = False
    return ok
