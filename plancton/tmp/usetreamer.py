#!/usr/bin/env python
import streamer

str = streamer.Streamer()
dic_tag = {"tag": "value1", "tag2": 1234, "tag3": 6.1345}
dic_field = {"value": 0.65}
print str.create_db();
print str.write_pt(tag_dict=dic_tag, field_dict=dic_field)
