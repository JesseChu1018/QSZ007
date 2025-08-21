#!/usr/bin/env python3
"""This file starts a pyro nameserver and the proxying server."""
from pathlib import Path
import subprocess
import time
from server import start_server
import json

HERE = Path(__file__).parent

############
# parameters
############

#setting_file = '../settings.json'
setting_file = '/media/PYNQ/settings.json'
settings = json.load(open(setting_file))
#bitfile = '../qick_lib/qick/' + settings['bitfile']
bitfile = settings['bitfile']
proxy_name = settings['proxy_name']
ns_port = settings['ns_port']
# set to 0.0.0.0 to allow access from outside systems
ns_host = settings['ns_host']
ns_ip = settings['ns_ip']
enable_nameserver = settings['enable_nameserver']

############


# start the nameserver process
if enable_nameserver:
    ns_proc = subprocess.Popen(
        [f'PYRO_SERIALIZERS_ACCEPTED=pickle PYRO_PICKLE_PROTOCOL_VERSION=4 pyro4-ns -n {ns_host} -p {ns_port}'],
        shell=True
    )

# wait for the nameserver to start up
time.sleep(5)

# start the qick proxy server
start_server(
    bitfile=bitfile,
    proxy_name=proxy_name,
    ns_host= ns_ip,
    ns_port=ns_port
)
