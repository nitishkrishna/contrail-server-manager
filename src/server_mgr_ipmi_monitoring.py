import os
import time
import signal
import sys
import datetime
import syslog
import subprocess
from gevent import monkey
monkey.patch_all(thread=not 'unittest' in sys.modules)
import cStringIO
import re
import socket
import pdb
import server_mgr_db
from server_mgr_db import ServerMgrDb as db
from server_mgr_exception import ServerMgrException as ServerMgrException
from server_mgr_logger import ServerMgrlogger as ServerMgrlogger
from server_mgr_logger import ServerMgrTransactionlogger as ServerMgrTlog
from threading import Thread
import discoveryclient.client as client
from ipmistats.sandesh.ipmi.ttypes import *
from pysandesh.sandesh_base import *
from sandesh_common.vns.ttypes import Module, NodeType
from sandesh_common.vns.constants import ModuleNames, NodeTypeNames, \
    Module2NodeType, INSTANCE_ID_DEFAULT
from sandesh_common.vns.constants import *
from server_mgr_dev_env_monitoring import ServerMgrDevEnvMonitoring


class IpmiData:
    sensor = ''
    reading = ''
    status = ''


# Class ServerMgrIPMIMonitoring provides a monitoring object that runs as a thread
# when Server Manager starts/restarts. This thread continually polls all the servers
# that are stored in the Server Manager DB at any point. Before this polling can occur,
# Server Manager opens a Sandesh Connection to the Analytics node that hosts the
# Database to which the monitor pushes device environment information.
class ServerMgrIPMIMonitoring(ServerMgrDevEnvMonitoring):

    def __init__(self, val, frequency, serverdb, log, translog, analytics_ip=None):
        ''' Constructor '''
        ServerMgrDevEnvMonitoring.__init__(self, log, translog)
        self.val = val
        self.freq = float(frequency)
        self._serverDb = serverdb
        self._smgr_log = log
        self._smgr_trans_log = translog
        self._analytics_ip = analytics_ip

    # call_send function is the sending function of the sandesh object (send_inst)
    def call_send(self, send_inst):
        self._smgr_log.log(self._smgr_log.INFO, "Sending UVE Info over Sandesh")
        send_inst.send()

    # send_ipmi_stats function packages and sends the IPMI info gathered from server polling
    # to the analytics node
    def send_ipmi_stats(self, ipmi_data, hostname):
        sm_ipmi_info = SMIpmiInfo()
        sm_ipmi_info.name = str(hostname)
        sm_ipmi_info.sensor_stats = []
        sm_ipmi_info.sensor_status = []
        for ipmidata in ipmi_data:
            ipmi_stats = IpmiSensor()
            ipmi_stats.sensor = ipmidata.sensor
            ipmi_stats.reading = ipmidata.reading
            ipmi_stats.status = ipmidata.status
            sm_ipmi_info.sensor_stats.append(ipmi_stats)
            sm_ipmi_info.sensor_status.append(ipmi_stats)
        ipmi_stats_trace = SMIpmiInfoTrace(data=sm_ipmi_info)
        self.call_send(ipmi_stats_trace)

    # sandesh_init function opens a sandesh connection to the analytics node's ip
    # (this is recevied from Server Mgr's config or cluster config). The function is called only once.
    # For this node, a discovery client is set up and passed to the sandesh init_generator.
    def sandesh_init(self, analytics_ip_list):
        try:
            self._smgr_log.log(self._smgr_log.INFO, "Initializing sandesh")
            # storage node module initialization part
            module = Module.IPMI_STATS_MGR
            module_name = ModuleNames[module]
            node_type = Module2NodeType[module]
            node_type_name = NodeTypeNames[node_type]
            instance_id = INSTANCE_ID_DEFAULT
            analytics_ip_set = set()
            for ip in analytics_ip_list:
                analytics_ip_set.add(ip)
            for analytics_ip in analytics_ip_set:
                _disc = client.DiscoveryClient(str(analytics_ip), '5998', module_name)
                sandesh_global.init_generator(
                    module_name,
                    socket.gethostname(),
                    node_type_name,
                    instance_id,
                    [],
                    module_name,
                    HttpPortIpmiStatsmgr,
                    ['ipmi.ipmi'],
                    _disc)
        except Exception as e:
            raise ServerMgrException("Error during Sandesh Init: " + str(e))

    # The Thread's run function continually checks the list of servers in the Server Mgr DB and polls them.
    # It then calls other functions to send the information to the correct analytics server.
    def run(self):
        print "Starting monitoring thread"
        self._smgr_log.log(self._smgr_log.INFO, "Starting monitoring thread")
        ipmi_data = []
        supported_sensors = ['FAN|.*_FAN', '^PWR', 'CPU[0-9][" "].*', '.*_Temp', '.*_Power']
        while True:
            servers = self._serverDb.get_server(
                None, detail=True)
            ipmi_list = list()
            hostname_list = list()
            server_ip_list = list()
            data = ""
            if self._analytics_ip is not None:
                for server in servers:
                    server = dict(server)
                    if 'ipmi_address' in server:
                        ipmi_list.append(server['ipmi_address'])
                    if 'id' in server:
                        hostname_list.append(server['id'])
                    if 'ip_address' in server:
                        server_ip_list.append(server['ip_address'])
                for ip, hostname in zip(ipmi_list, hostname_list):
                    ipmi_data = []
                    cmd = 'ipmitool -H %s -U admin -P admin sdr list all' % ip
                    result = super(ServerMgrIPMIMonitoring, self).call_subprocess(cmd)
                    if result is not None:
                        self._smgr_trans_log.log("IPMI Polling: " + str(ip), self._smgr_trans_log.SMGR_POLL_DEV, True)
                        fileoutput = cStringIO.StringIO(result)
                        for line in fileoutput:
                            reading = line.split("|")
                            sensor = reading[0].strip()
                            reading_value = reading[1].strip()
                            status = reading[2].strip()
                            for i in supported_sensors:
                                if re.search(i, sensor) is not None:
                                    ipmidata = IpmiData()
                                    ipmidata.sensor = sensor
                                    ipmidata.reading = reading_value
                                    ipmidata.status = status
                                    ipmi_data.append(ipmidata)
                    else:
                        self._smgr_trans_log.log("IPMI Polling: " + str(ip), self._smgr_trans_log.SMGR_POLL_DEV, False)
                    self.send_ipmi_stats(ipmi_data, hostname=hostname)
            else:
                self._smgr_log.log(self._smgr_log.ERROR, "IPMI Polling: No Analytics IP info found")

            self._smgr_log.log(self._smgr_log.INFO, "Monitoring thread is sleeping for " + str(self.freq) + " seconds")
            time.sleep(self.freq)
            self._smgr_log.log(self._smgr_log.INFO, "Monitoring thread woke up")
