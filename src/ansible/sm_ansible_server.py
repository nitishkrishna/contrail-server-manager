#!/usr/bin/env python

import os
import sys
import json
import Queue
import paste
import urllib
import bottle
import urllib2
import argparse
import threading
import ConfigParser
from bottle import Bottle, route
from sm_ansible_playbook import ContrailAnsiblePlayBook
from sm_ansible_utils import send_REST_request
from collections import defaultdict

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from server_mgr_logger import ServerMgrlogger as ServerMgrlogger

_DEF_SMGR_BASE_DIR = '/etc/contrail_smgr/'
_DEF_ANSIBLE_SRVR_CFG_FILE = _DEF_SMGR_BASE_DIR + 'sm-config.ini'
_WEB_HOST = '127.0.0.1'
_ANSIBLE_SRVR_PORT = 9003
_ANSIBLE_REGISTRY = '0.0.0.0:5100'
_ANSIBLE_REGISTRY_INSECURE = True
_ANSIBLE_PLAYBOOK = '/opt/contrail/server_manager/ansible/playbooks/contrail-ansible/site.yml'
_DOCKER_INSTALL_METHOD = 'package'
_DOCKER_PACKAGE_NAME = 'docker-engine'

class Joiner(threading.Thread):
    def __init__(self, q):
        super(Joiner, self).__init__()
        self._smgr_log = ServerMgrlogger()
        self.queue = q

    def run(self):
        while True:
            child = self.queue.get()
            self._smgr_log.log(self._smgr_log.INFO, "Joining a process")
            print "joining a process"
            if child == None:
                return
            child.join()
            self._smgr_log.log(self._smgr_log.INFO, "Process Done")
            print "process done"

class SMAnsibleServer():
    '''
    Use bottle to provide REST interface for the server manager.
    '''
    def __init__(self, args_str=None):
        try:
            self._smgr_log = ServerMgrlogger()
        except:
            print "Error Creating logger object"

        self._smgr_log.log(self._smgr_log.INFO, "Starting SM Ansible Server")
        self.host_run_results = defaultdict(dict)
        if not args_str:
            args_str = sys.argv[1:]
        self._parse_args(args_str)
        self.joinq  = Queue.Queue()
        self.joiner = Joiner(self.joinq)
        self.joiner.start()
        self._smgr_log.log(self._smgr_log.INFO,  'Initializing Bottle App')
        self.app = bottle.app()
        bottle.route('/start_provision', 'POST', self.start_provision)
        bottle.route('/run_playbook', 'POST', self.start_playbook)
        bottle.route('/playbook_status', 'PUT', self.playbook_status)

    def _parse_args(self, args_str):
        '''
        Eg. python sm_ansible_server.py --config_file serverMgr.cfg
                                         --listen_port 8082
        '''
        # Source any specified config/ini file
        # Turn off help, so we print all options in response to -h
        conf_parser = argparse.ArgumentParser(add_help=False)

        conf_parser.add_argument(
            "-c", "--config_file",
            help="Specify config file with the parameter values.",
            metavar="FILE")
        args, remaining_argv = conf_parser.parse_known_args(args_str)

        serverCfg = {
            'docker_install_method': _DOCKER_INSTALL_METHOD,
            'docker_package_name': _DOCKER_PACKAGE_NAME,
            'ansible_srvr_ip': _WEB_HOST,
            'ansible_srvr_port': _ANSIBLE_SRVR_PORT,
            'docker_insecure_registries': _ANSIBLE_REGISTRY,
            'docker_registry': _ANSIBLE_REGISTRY,
            'docker_registry_insecure': _ANSIBLE_REGISTRY_INSECURE,
            'ansible_playbook': _ANSIBLE_PLAYBOOK
        }

        if args.config_file:
            config_file = args.config_file
        else:
            config_file = _DEF_ANSIBLE_SRVR_CFG_FILE
        config = ConfigParser.SafeConfigParser()
        config.read([args.config_file])
        self._smgr_config = config
        try:
            for key in dict(config.items("ANSIBLE-SERVER")).keys():
                #if key in serverCfg.keys():
                serverCfg[key] = dict(config.items("ANSIBLE-SERVER"))[key]


            self._smgr_log.log(self._smgr_log.DEBUG,
                               "Arguments read form config file %s" % serverCfg)
        except ConfigParser.NoSectionError:
            msg = "Server Manager doesn't have a configuration set."
            self._smgr_log.log(self._smgr_log.ERROR, msg)


        self._smgr_log.log(self._smgr_log.DEBUG, "Arguments read form config file %s" % serverCfg)
        # Override with CLI options
        # Don't surpress add_help here so it will handle -h
        parser = argparse.ArgumentParser(
            # Inherit options from config_parser
            # parents=[conf_parser],
            # print script description with -h/--help
            description=__doc__,
            # Don't mess with format of description
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.set_defaults(**serverCfg)

        parser.add_argument(
            "-i", "--ansible_srvr_ip",
            help="IP address to provide service on, default %s" % (_WEB_HOST))
        parser.add_argument(
            "-p", "--ansible_srvr_port",
            help="Port to provide service on, default %s" % (_ANSIBLE_SRVR_PORT))
        self._args = parser.parse_args(remaining_argv)
        self._args.config_file = args.config_file
    # end _parse_args

    def start_provision(self):
        self.host_run_results = defaultdict(dict)
        print "starting provision"
        return json.dumps({'status': 'Provision Started'})

    def start_playbook(self):
        print "starting playbook"
        entity = bottle.request.json
        pb     = ContrailAnsiblePlayBook(entity, self._args)
        pb.start()
        self.joinq.put(pb)
        # Return success. Actual status will be supplied when the pb thread
        # completes and the next status query is made
        bottle.response.headers['Content-Type'] = 'application/json'
        return json.dumps({'status': 'Provision in Progress'})

    def playbook_status(self):
        server_hostname = bottle.request.query['server_id']
        server_role     = bottle.request.query['role']
        server_status   = bottle.request.query['state']
        self.host_run_results[server_hostname][server_role] = server_status

        status_resp = {
                    "server_id" : server_hostname,
                    "state" : server_status
                    #"state" : self.host_run_results[server_hostname]
        }
        send_REST_request(self._args.ansible_srvr_ip,
                          "9002", "ansible_status",
                          urllib.urlencode(status_resp), method='PUT',
                          urlencode=True)
        return json.dumps(status_resp)


def main(args_str=None):
    '''
    Spawn the webserver for Ansible operations
    '''
    sm_pid = os.getpid()
    pid_file = \
    "/var/run/contrail-server-manager/contrail-server-manager-ansible.pid"
    dir = os.path.dirname(pid_file)
    if not os.path.exists(dir):
        os.mkdir(dir)
    f = open(pid_file, "w")
    f.write(str(sm_pid))
    f.close()

    try:
        srvr = SMAnsibleServer(args_str)
        bottle.run(app=srvr.app, host=srvr._args.ansible_srvr_ip,
            port=srvr._args.ansible_srvr_port, debug=True, server='paste')
    except Exception as e:
        print "Container server was not started successfully"



if __name__=="__main__":
    main()
