# -*- coding: utf-8 -*-
import os
import requests
from datetime import datetime

# Class used to stream data to an InfluxDB database.
# Exceptions and logging are supposed to be managed by the calling mudule.
# Plancton must handle every exception.

class Streamer():
   __version__ = "0.1"
   def __init__(self, host="localhost", port="8086", schema="http"):
      self._host = host
      self._port = port
      self._schema = schema
      self._base_url = "{0}://{1}:{2}".format(self._schema, self._host, self._port)
      self._head_4query = {"Content-type": "application/json", "Accept": "text/plain"}
      self._head_4write = {"Content-type": "application/octet-stream", "Accept": "text/plain"}

   def create_db(self, db="plancton-monitor"):
      parameters = { "q":"CREATE DATABASE \"%s\"" % db, "db": db }
      return requests.get(self._base_url + "/query", headers=self._head_4query, params=parameters)

   def write_pt(self, db="plancton-monitor", name="point", tag_dict={}, field_dict={}):
      # See standard at:  https://docs.influxdata.com/influxdb/v1.0/write_protocols/line_protocol_tutorial/
      data_string = name+","+"".join("%s=%s," % (key,value) for key,value in tag_dict.iteritems())[:-1] + \
         " " + "".join("%s=%s," % (key,value) for key,value in field_dict.iteritems())[:-1] + " " + \
         str(((datetime.utcnow()-datetime.utcfromtimestamp(0)).total_seconds()*1000000000).__int__())
      parameters = {"db": db}
      # print "[DEBUG]: data string is : %s" % data_string
      return requests.post(self._base_url + "/write", headers=self._head_4write, params=parameters, data=data_string.encode("utf-8"))
