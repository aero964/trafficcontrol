#!/usr/bin/python
# -*- coding: utf-8 -*-

import datetime
import os
import os.path
import re
import shlex
import locale
import hashlib
import logging
import subprocess
import collections
import yaml

class tcLogging(object):
    def __init__(self, name, formatter=logging.Formatter('[%(asctime)s] [%(process)d] %(levelname)s: %(name)s(): %(message)s')):
        self.logging = logging.getLogger(name)
        self.logging.setLevel(logging.DEBUG)
        self.formatter = formatter
        self.debug = self.logging.debug
        self.info = self.logging.info
        self.warning = self.logging.warning
        self.error = self.logging.error

    def addHandler(self, handler, formatter=None):
        handler.setLevel(self.logging.getEffectiveLevel())
        if formatter is None:
            handler.setFormatter(self.formatter)
        else:
            handler.setFormatter(formatter)
        self.logging.addHandler(handler)

class Shell(object):
    def __init__(self):
        self.history = []
        self.stdout = []
        self.stderr = []
        self.encoding = 'utf-8'
        self.logger = tcLogging('trafficcontrol.Shell')

    def get_lasterr(self):
        return self.stderr[-1]

    def get_locale(self, envvars='LANG'):
        return locale.getdefaultlocale(envvars)

    def execute(self, command, stdin=None):
        if isinstance(command, basestring):
            command = shlex.split(command)
        self.logger.debug(' '.join(command))
        p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate(stdin)
        self.history.append((command, p.returncode, stdout, stderr))
        self.stdout.append(stdout)
        self.stderr.append(stderr)
        if not p.returncode == 0:
            self.logger.error(stderr)
            raise Exception(stderr)
        return {'stdout': stdout, 'stderr' : stderr}

    def fputtetx(self, filename, data, encoding='utf-8'):
        self.fput(filename, data.encode(encoding))

    def fgettext(self, filename, encoding='utf-8'):
        return self.fget(filename).decode(encoding)

    def fput(self, filename, data):
        try:
            if os.path.exists(filename):
                self.execute('cp -a %s %s.bak' % (filename, filename))
            f = open(filename, 'w')
            f.write(data)
            f.close()
            if not self.execute('md5sum %s' % filename)[0].split('\n')[0].split()[0] == hashlib.md5(data).hexdigest():
                if os.path.exists(filename):
                    self.execute('cp -a %s.bak %s' % (filename, filename))
                raise Exception('File checksum does not match.')
        finally:
            if os.path.exists(filename):
                self.execute('rm -f %s.bak' % filename)

    def fget(self, filename):
        f = open(filename, 'r')
        data = f.read()
        f.close()
        return data

class TrafficControlManager(object):
    def __init__(self):
        self.logger = tcLogging('trafficcontrol.TrafficControlManager')
        self.shell = Shell()

    def exists_rule(self, interface):
        return re.search(r'qdisc.+ rate ', self.shell.execute('tc qdisc show dev %s' % interface)['stdout'], re.IGNORECASE)

    def set(self, params, wide_band):
        interface = 'eth0'
        if wide_band:
            bandwidth = '1000M'  # Link speed of eth0.
            root_weight = '100M' # 1/10 of eth0 link speed.
        else:
            bandwidth = '100M'   # Link speed of eth0.
            root_weight = '10M'  # 1/10 of eth0 link speed.
        rate = '%sM' % params['bandwidth']
        weight = '%sK' % (params['bandwidth'] * 1000 / 10)
        if self.exists_rule(interface):
            self.shell.execute('tc qdisc del dev %s root' % interface)
        self.shell.execute('tc qdisc add dev %s root handle 1 cbq bandwidth %sbit avpkt 30000 cell 8' % (interface, bandwidth))
        self.shell.execute('tc class change dev %s root cbq weight %sbit allot 1514' % (interface, root_weight))
        self.shell.execute('tc class add dev %s parent 1: classid 1:3000 cbq bandwidth %sbit rate %sbit weight %sbit prio 5 allot 1514 cell 8 maxburst 20 avpkt 30000 bounded' % (interface, bandwidth, rate,weight))
        self.shell.execute('tc qdisc add dev %s parent 1:3000 handle 3000 tbf rate %sbit buffer 10Kb/8 limit 15Kb mtu 1500' % (interface, rate))
        for port in params['ports']:
            if port == '*':
                self.shell.execute('tc filter add dev %s parent 1:0 protocol ip prio 99 u32 match ip 0xffff classid 1:3000' % interface)
                self.shell.execute('tc filter add dev %s parent 1:0 protocol ipv6 prio 100 u32 match ip6 0xffff classid 1:3000' % interface)
            else:
                self.shell.execute('tc filter add dev %s parent 1:0 protocol ip prio 99 u32 match ip sport %s 0xffff classid 1:3000' % (interface, port))
                self.shell.execute('tc filter add dev %s parent 1:0 protocol ipv6 prio 100 u32 match ip6 sport %s 0xffff classid 1:3000' % (interface, port))

    def get_band_config_list(self, conf_file):
        band_list = {}
        if os.path.exists(conf_file):
            f = open(conf_file, 'r')
            band_list = yaml.load(stream=f, Loader=yaml.SafeLoader)
        return band_list

    def get_current_band_config(self, band_list):
        band = 0
        hour = datetime.datetime.now().strftime('%H')
        min = datetime.datetime.now().strftime('%M')
        if (len(min) >= 2):
            tmpmin = min[:1] + '0'
            min = tmpmin
        if  band_list and band_list.has_key(int(hour)) and band_list[int(hour)].has_key(int(min)):
            band = band_list[int(hour)][int(min)]
        return band

def main():
    logdir = '/usr/local/aero/var/log/trafficcontrol/'
    base_conf = '/usr/local/aero/etc/trafficcontrol_base.yaml'
    user_conf = '/usr/local/aero/etc/trafficcontrol_user.yaml'
    is_wide_band = False
    old_umask = os.umask(0077);
    if not os.path.isdir(logdir):
        os.mkdir(logdir)
    logger = tcLogging('trafficcontrol')
    logger.addHandler(logging.FileHandler(logdir + 'trafficcontrol.log'))
    logger.info('Start traffic control.')
    base_band_list = TrafficControlManager().get_band_config_list(base_conf);
    base_band = TrafficControlManager().get_current_band_config(base_band_list)
    user_band_list = TrafficControlManager().get_band_config_list(user_conf);
    user_band = TrafficControlManager().get_current_band_config(user_band_list)
    band = base_band
    logger.info(base_band)
    logger.info(user_band)
    if user_band and user_band > base_band:
        band = user_band
    if band:
        band = band * 1.2
    if band >= 100:
        is_wide_band = True
    if band:
        TrafficControlManager().set({
            'bandwidth': band,
            'ports': [80, 443], },
            is_wide_band)
    logger.info('Finish traffic control.')
    os.umask(old_umask);

if __name__ == '__main__':
    main()
