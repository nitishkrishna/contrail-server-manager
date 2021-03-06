import os
import sys
import pycurl
import ConfigParser
import json
from StringIO import StringIO

def send_REST_request(ip, port, endpoint, payload,
                      method='POST', urlencode=False):
    try:
        response = StringIO()
        headers = ["Content-Type:application/json"]
        url = "http://%s:%s/%s" %(ip, port, endpoint)
        conn = pycurl.Curl()
        if method == "PUT":
            conn.setopt(pycurl.CUSTOMREQUEST, method)
            if urlencode == False:
                first = True
                for k,v in payload.iteritems():
                    if first:
                        url = url + '?'
                        first = False
                    else:
                        url = url + '&'
                    url = url + ("%s=%s" % (k,v))
            else:
                url = url + '?' + payload
        print "Sending post request to %s" % url
        conn.setopt(pycurl.URL, url)
        conn.setopt(pycurl.HTTPHEADER, headers)
        conn.setopt(pycurl.POST, 1)
        if urlencode == False:
            conn.setopt(pycurl.POSTFIELDS, '%s'%json.dumps(payload))
        conn.setopt(pycurl.WRITEFUNCTION, response.write)
        conn.perform()
        return response.getvalue()
    except:
        return None

def create_inv_file(fname, dictionary):
    with open(fname, 'w') as invfile:
        for key, value in dictionary.items():
            if isinstance(value, str):
                invfile.write(key)
                invfile.write('\n')
                invfile.write(value)
                invfile.write('\n')
            if isinstance(value, list):
                continue


        for key, value in dictionary.items():
            if isinstance(value, str):
                continue

            if isinstance(value, list):
                invfile.write(key)
                invfile.write('\n')
                for x in value:
                    invfile.write(x)
                    invfile.write('\n')


'''
Recursively process the dictionary and create the INI format file
'''
def create_sections(config, dictionary, section=None):
    for key, value in dictionary.items():
        if isinstance(value, dict):
            create_sections(config, dictionary=value,
                                   section=key)
        else:
            try:
                config.set(section, key, value)
            except ConfigParser.NoSectionError:
                try:
                    config.add_section(section)
                    config.set(section, key, value)
                except ConfigParser.DuplicateSectionError:
                    print "Ignore DuplicateSectionError"

            except TypeError:
                try:
                    config.add_section(section)
                except ConfigParser.DuplicateSectionError:
                    print "ignore Duplicate Sections"


def create_conf_file(ini_file, dictionary={}):
    if not ini_file:
        return
    config = ConfigParser.SafeConfigParser()
    create_sections(config, dictionary)
    with open(ini_file, 'w') as configfile:
        config.write(configfile)


