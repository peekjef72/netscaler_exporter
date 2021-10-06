#!/usr/bin/python3.6

import yaml
from pathlib import Path

import argparse, sys, os, re, json, logging, logging.handlers, inspect

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from requests.exceptions import ConnectionError, ReadTimeout

from netscaler_exporter.constants import (PKG_NAME, PKG_VERSION, EXPORTER_CONFIG_NAME)

from netscaler_exporter.netscaler_api import NetscalerAPI
from netscaler_exporter.filters import Filters, Filter
from netscaler_exporter.core_exporter import NetscalerExporter
from netscaler_exporter.collector import NetscalerCollector
from netscaler_exporter.yamlscript import YamlScript

#******************************************************************************************
config = None
logger = None
#******************************************************************************************
class myArgs:
  attrs = [ 'base_path', 'config_file', 'filter_path',
		'logger.facility', 'logger.level',
		'web.listen-address', 'dry_mode', 'metrics_file', 'metric', 'target'
          ]
  def __init__(self):

    for attr in myArgs.attrs:
        setattr(self, attr, None)

  def __repr__(self):
    obj = {}
    for attr in myArgs.attrs:
        val = getattr(self, attr)
        if not val is None:
           obj[attr] = val
    return json.dumps(obj)

#******************************************************************************************
def read_metrics_file(file, metric_name):
   metrics = {}
   with open(file, 'r') as met_file:
      try:
         mets = yaml.safe_load(met_file)
         for met in mets:
            if not 'name' in met:
               name = '{0}_{1}'.format( met_file, count)
               count += 1
            else:
               name = met['name']

            if metric_name is None or ( metric_name is not None and name == metric_name ):
               metrics[ name ] = met

      except yaml.YAMLError as exc:
         logger.error(exc)

   return metrics

#******************************************************************************************
def read_metrics_files(base_path, metrics_pattern, metric_name):
   patterns = []
   metrics = {}

   if not isinstance(metrics_pattern, list):
      metrics_pattern = [ metrics_pattern ]

   for pat in metrics_pattern:
      # pattern should be 'conf/*.yml' so must concatenate base_path and pattern,
      # then split path and filename
      if not re.match(r'^\.?/', pat):
         pat = base_path + '/' + pat
      (path, file) = os.path.split( pat )
      if file.find('*') != -1:
         pattern = file.replace('.', '\\.')
         pattern = pattern.replace('*', '.*')
         patterns.append( [path, re.compile('^' + pattern + '$')] )
      else:
         patterns.append( [None, pat] )

   for (path, pattern) in patterns:
      count = 1
      #two case: pattern or full path.
      if path is not None:
         try:
            for pa in Path(path).iterdir():
#               print('path: {0}'.format(pa) )
               if pattern.search(str(pa)):
                  metrics.update( read_metrics_file(pa, metric_name) )

         except FileNotFoundError as exc:
            logger.warning('{0}'.format(exc))

      else:
         metrics.update( read_metrics_file(pattern, metric_name) )

   return metrics

#******************************************************************************************
def my_exit(code):
   global logger

   logger.info('{0} {1} stopped.'.format(PKG_NAME, PKG_VERSION) )
   logging.shutdown();

   sys.exit(code)

#******************************************************************************************
def main():
   global logger

   # get command line arguments

   parser = argparse.ArgumentParser(description='collector for Citrix Netscaler.')
   parser.add_argument('-b', '--base_path'
                        , help='set base directory to find default files.')
   parser.add_argument('-c', '--config_file'
                        , help='path to config files.')

   parser.add_argument('-F', '--filter_path'
                        , help='set filter directory to find filter files.')

   parser.add_argument('-f', '--logger.facility'
                        , help='logger facility (syslog or file path).'
		)

   parser.add_argument('-l', '--logger.level'
                        , help='logger level.'
			, choices=['error', 'warning', 'info', 'debug' ]
			, default='info'
		)

   parser.add_argument('-o ', '--metrics_file'
			, help='collect the metrics from the specified file instead of config.'
			, default=None
		)

   parser.add_argument('-m ', '--metric'
			, help='collect only the specified metric name from the metrics_file.'
			, default=None
		)

   parser.add_argument('-n ', '--dry_mode'
			, action='store_true'
			, help='collect the metrics then exit; display results to stdout.'
			, default=False
		)

   parser.add_argument('-t ', '--target'
			, help='In dry_mode collect metrics on specified target.i Default first from config file.'
			, default=None
		)

   parser.add_argument('-w', '--web.listen-address'
			, help='Address to listen on for web interface and telemetry.'
			, default=':9258'
		)
   parser.add_argument('-V', '--version'
			, action='version', version='{0} {1}'.format(PKG_NAME, PKG_VERSION)
                        , help='display program version and exit..')

   parser.add_argument('-v ', '--verbose'
			, action='store_true'
			, help='verbose mode; display log message to stdout.')
   inArgs = myArgs()
   args = parser.parse_args(namespace=inArgs)

   base_path = os.path.dirname( inspect.getabsfile(inspect.currentframe()) )
   if args.base_path is not None:
      base_path = inArgs.base_path

   config_file = base_path + '/conf/' + EXPORTER_CONFIG_NAME
   if args.config_file is not None:
      if not re.match(r'^(\.|\/)?/', args.config_file):
         config_file = base_path + '/' + args.config_file
      else:
         config_file = args.config_file

   with open(config_file, 'r') as cfg:
      try:
         config = yaml.safe_load(cfg)
      except yaml.YAMLError as exc:
         print(exc)
         sys.exit(1);

   logging.getLogger("urllib3").setLevel(logging.CRITICAL)
   #***********************************************************************************************
   #** init logging facility

   try:
      logfile = getattr(args, 'logger.facility')
      if logfile is None:
         logfile = config['logger']['facility']
      #print('ok: logfile defined in config.')
      if logfile is not None and logfile != 'syslog':
         if not re.match(r'^(\.|\/)?/', logfile):
            logfile = base_path + '/' + logfile
   except IndexError:
      logfile = None

   tmp = None
   try:
      tmp = getattr(args, 'logger.level')
      if tmp is None:
         tmp = config['logger']['level']
   except:
      pass

   if tmp is not None:
       tmp = tmp.lower()
       if tmp == 'critical':
           loglevel = logging.CRITICAL
           #print('logLevel: CRITICAL')
       elif tmp == 'error':
           loglevel = logging.ERROR
           #print('logLevel: ERROR')
       elif tmp == 'warning':
           loglevel = logging.WARNING
           #print('logLevel: WARNING')
       elif tmp == 'debug':
           loglevel = logging.DEBUG
           #print('logLevel: DEBUG')
       else:
           loglevel = logging.INFO
           #print('default logLevel: INFO')
   else:
      loglevel = logging.INFO
      print('logLevel: undefined - use default INFO')

   logger = logging.getLogger()
   logger.setLevel( loglevel )
   formatter = logging.Formatter( fmt= '{0}[%(process)d]: level=%(levelname)s - %(message)s'.format(PKG_NAME)
	, datefmt='%Y/%m/%d %H:%M:%S ' )

   if logfile is not None and logfile != 'syslog':
      handler = logging.FileHandler(logfile)
      handler.setLevel( loglevel )
      handler.setFormatter(formatter)
      logger.addHandler(handler)
   elif logfile == 'syslog':
      handler = logging.handlers.SysLogHandler(address = '/dev/log')
      handler.setLevel( loglevel )
      handler.setFormatter(formatter)
      logger.addHandler(handler)

   if args.verbose:
      stream_handler = logging.StreamHandler(stream=sys.stdout)
      stream_handler.setLevel(logging.DEBUG)
      logger.setLevel(logging.DEBUG)
      stream_handler.setFormatter(formatter)
      logger.addHandler(stream_handler)

   logger.info('{0} {1} starting....'.format(PKG_NAME, PKG_VERSION) )
   #******************************
   #* analyze config
   if config is None:
      logging.error("can't read config file '{0}'.".format(config_found))
      my_exit(1)

   logger.debug( 'config is {0}'.format(config) )

   metrics_file = None
   if args.metrics_file is not None:
      metrics_file = args.metrics_file
   elif 'metrics_file' in config:
      metrics_file = config['metrics_file']
   if metrics_file is not None:
      metrics = read_metrics_files( base_path, metrics_file, args.metric )
   else:
      logger.error('no metric definitions found in config file.')
      my_exit(1)

   if metrics is None or len(metrics) == 0:
      logger.error('no metrics found')
      my_exit(1)
#   else:
#      logger.debug( 'metrics are: {0}'.format(metrics) )

   #*****************************
   #* load custom filters

   filter_path = None
   if args.filter_path is not None:
      filter_path = inArgs.filter_path
      if not re.match(r'^(\.|\/)?/', filter_path):
         filter_path = base_path + '/' + filter_path
   else:
      if 'custom_filters' in config:
         if not re.match(r'^(\.|\/)?/', config['custom_filters']):
            config_file = base_path + '/' + config['custom_filters']
         else:
            filter_path = config['custom_filters']

   filters = Filters(path = filter_path)

   #*****************************
   #* build netscaler api interface

   apis = []
   for netscaler in config['netscalers']:

      protocol = "https"
      if 'protocol' in netscaler:
         protocol = netscaler['protocol']

      verify_ssl = True
      if 'verify_ssl' in netscaler:
         verify_ssl = netscaler['verify_ssl']

      timeout = 10.0
      if 'timeout' in netscaler:
         timeout = float(netscaler['timeout'])

      keep_session = True
      if 'keep_session' in netscaler:
         keep_session = netscaler['keep_session']

      port = None
      if 'port' in netscaler:
         port = int(netscaler['port'])

      labels = None
      if 'default_labels' in netscaler:
         labels = netscaler['default_labels']

      proxy = None
      if 'proxy' in netscaler and isinstance(netscaler['proxy'], dict):
         proxy = netscaler['proxy']

      #* to do on every "request"
      try:
         api =  NetscalerAPI(
		( netscaler['user'], netscaler['password'] ),
		host=netscaler['host'],
		port=port,
		url_path_prefix="nitro/v1",
		protocol = protocol,
		verify = verify_ssl,
		timeout = timeout,
		keep_session = keep_session,
		labels = labels,
		proxy = proxy,
         )
      except:
         logger.error('can\'t init netscaler-exporter api.')
         my_exit(1);

      apis.append(api)

   if len(apis) < 1:
      logger.error('No exporter target host found.')
      my_exit(1);

   #*****************************
   #* build yamscript prog
   try:
      script = YamlScript(
	filters=filters,
	debug=args.verbose,
	logger=logger
      )
   except:
      logger.error('can\'t init YAM script engine.')
      my_exit(1);

   #*****************************
   #* build netscaler exporter interface
   exporter = NetscalerExporter(
	engine=script,
	apis=apis,
	metrics=metrics,
	debug=args.verbose,
	logger=logger
   )

   if args.dry_mode:
      target = None
      if args.target is not None:
         for api in exporter.apis:
            if args.target == api.getHost():
               target = api
               break
    
      if target is None:
         if args.target is not None:
            logger.warning("Specified target '{0}' not found. use default.".format(args.target))
         target = exporter.apis[0]
      exporter.api = target
      exporter.collect( {} )
      my_exit(0)

   #*****************************
   #* start collector

   addr = None
   try:
      addr = getattr(args, 'web.listen-address')
      if addr is not None:
         tmp = addr.split(':')
         if len(tmp) > 0:
            addr = tmp[0]
         if len(tmp) > 1:
            port = tmp[1]
         if port is not None and isinstance(port, str):
            port = int(port)
      else:
         addr = config['weblisten']['address']
         port = config['weblisten']['port']
         if port is not None and isinstance(port, str):
            port = int(port)
   except (AttributeError, IndexError):
      pass

   collector = NetscalerCollector(
	exporter = exporter,
	port=port,
	address=addr,
   )

   collector.starts()

   my_exit(0)
# end main...

#*****************************************************************************
if __name__  == '__main__':
   main()
