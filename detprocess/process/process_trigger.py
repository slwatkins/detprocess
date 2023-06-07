import yaml
import warnings
from pathlib import Path
import numpy as np
import vaex as vx
import importlib
import sys
import os
from glob import glob
from pprint import pprint
from multiprocessing import Pool
from itertools import repeat
from datetime import datetime
import stat
import time
import astropy
import pytesdaq.io as h5io
import copy
from humanfriendly import parse_size
from detprocess.process._processing_data  import ProcessingData
from detprocess.process.eventbuilder import EventBuilder
from detprocess.process.oftrigger import OptimumFilterTrigger

__all__ = [
    'TriggerProcessing'
]




class TriggerProcessing:
    """
    Class to manage trigger processing and 
    extract features, dataframe can be saved
    in hdf5 using vaex framework
    
    Multiple nodes can be used if data splitted in 
    different series

    """

    def __init__(self, raw_path, config_file,
                 series=None, verbose=True):
        """
        Intialize data processing 
        
        Parameters
        ---------
    
        raw_path : str or list of str 
           raw data group directory OR full path to HDF5  file 
           (or list of files). Only a single raw data group 
           allowed 
            
        config_file : str 
           Full path and file name to the YAML settings for the
           processing.

        output_path : str, optional (default=No saved file)
           base directory where output feature file will be saved
     

        series : str or list of str, optional
            series to be process, disregard other data from raw_path

        processing_id : str, optional
            an optional processing name. This is used to be build 
            output subdirectory name and is saved as a feature in DetaFrame 
            so it can then be used later during 
            analysis to make a cut on a specific processing when mutliple 
            datasets/processing are added together.

        verbose : bool, optional
            if True, display info



        Return
        ------
        None
        """

        # display
        self._verbose = verbose

        # extract input file list
        input_data_dict, input_base_path, group_name = (
            self._get_file_list(raw_path, series=series)
        )

        if not input_data_dict:
            raise ValueError('No files were found! Check configuration...')

        self._input_data_dict = input_data_dict
        self._input_base_path = input_base_path
        self._input_group_name = group_name
        self._series_list = list(input_data_dict.keys())

        
        # extract processing configuration
        if not os.path.exists(config_file):
            raise ValueError('Configuration file "' + config_file
                             + '" not found!')
        available_channels = self._get_channel_list(input_data_dict)
      
        trigger_config, evtbuider_config, filter_file, channels = (
            self._read_config(config_file, available_channels)
        )

        self._trigger_config = trigger_config
        self._evtbuilder_config = evtbuider_config
        self._filter_file = filter_file
        self._trigger_channels = channels
      
        # check channels to be processed
        if not self._trigger_channels:
            raise ValueError('No trigger channels to be processed! ' +
                             'Check configuration...')
                                               

        
    def process(self, ntriggers=-1,
                processing_id=None,
                lgc_save=True,
                lgc_output=False,
                output_path=None,
                ncores=1,
                memory_limit='2GB'):
        
        """
        Process data 
        
        Parameters
        ---------

        ntriggers : int, optional
           number of events to be processed
           if not all events, requires ncores = 1
           Default: all available (=-1)
            
        processing_id : str, optional
            an optional processing name. This is used to be build 
            output subdirectory name and is saved as a feature in DetaFrame 
            so it can then be used later during 
            analysis to make a cut on a specific processing when mutliple 
            datasets/processing are added together.

        lgc_save : bool, optional
           if True, save dataframe in hdf5 files
           (dataframe not returned)
           if False, return dataframe (memory limit applies
           so not all events may be processed)
           Default: True

        output_path : str, optional
           base directory where output group will be saved
           default: same base path as input data
    
        ncores: int, optional
           number of cores that will be used for processing
           default: 1

        memory_limit : str or float, optional
           memory limit per file, example '2GB', '2MB'
           if float, then unit is byte

        """


        # check input
        if (ncores>1 and ntriggers>-1):
            raise ValueError('ERROR: Multi cores processing only allowed when '
                             + 'processing ALL events!')
        
        # check number cores allowed
        if ncores>len(self._series_list):
            ncores = len(self._series_list)
            if self._verbose:
                print('INFO: Changing number cores to '
                      + str(ncores) + ' (maximum allowed)')

        # processing ID 
        self._processing_id = processing_id
                
        # create output directory
        output_group_path = None
        
        if lgc_save:
            if  output_path is None:
                output_path  = self._input_base_path + '/processed'

            # add group name
            if self._input_group_name not in output_path:
                output_path += '/' + self._input_group_name
            
            output_group_path = self._create_output_directory(
                output_path,
                self._get_facility(self._input_data_dict)
            )
            
            if self._verbose:
                print(f'INFO: Processing output group path: {output_group_path}')

                
        # convert memory usage in bytes
        if isinstance(memory_limit, str):
            memory_limit = parse_size(memory_limit)

        # initialize output
        output_df = None
        
        # case only 1 node used for processing
        if ncores == 1:
            output_df = self._process(-1,
                                      self._series_list,
                                      ntriggers,
                                      lgc_save,
                                      lgc_output,
                                      output_group_path,
                                      memory_limit)

        else:
            
            # split data
            series_list_split = self._split_series(ncores)
            
            # for multi-core processing, we need to decrease the
            # max memory so it fits in RAM
            memory_limit /= ncores

              
            # lauch pool processing
            if self._verbose:
                print(f'INFO: Processing with be split between {ncores} cores!')

            node_nums = list(range(ncores+1))[1:]
            pool = Pool(processes=ncores)
            output_df_list = pool.starmap(self._process,
                                          zip(node_nums,
                                              series_list_split,
                                              repeat(ntriggers),
                                              repeat(lgc_save),
                                              repeat(lgc_output),
                                              repeat(output_group_path),
                                              repeat(memory_limit)))
            pool.close()
            pool.join()

            # concatenate output 
            if lgc_output:
                df_list = list()
                for df in output_df_list:
                    if df is not None:
                        df_list.append(df)
                if df_list:
                    output_df = vx.concat(df_list)
               
        # processing done
        if self._verbose:
            print('INFO: Trigger processing done!') 
                
        
        if lgc_output:
            return output_df 
        
           
    def _process(self, node_num,
                 series_list, ntriggers,
                 lgc_save, lgc_output,
                 output_group_path,
                 memory_limit):
        """
        Process data
        
        Parameters
        ---------

        node_num :  int
          node id number, used for display
        
        series_list : str
          list of series name to be processed

        ntriggers : int, optional
           number of events to be processed
           if not all events, requires ncores = 1
           Default: all available (=-1)
        
        lgc_save : bool, optional
           if True, save dataframe in hdf5 files
           (dataframe not returned)
           if False, return dataframe (memory limit applies
           so not all events may be processed)
           Default: True

        output_path : str, optional
           base directory where output feature file will be saved
           default: same base path as input data
    
        ncores: int, optional
           number of cores that will be used for processing
           default: 1

        memory_limit : float, optionl
           memory limit per file in bytes
           (and/or if return_df=True, max dataframe size)
   
        """

        # node string (for display)
        node_num_str = str()
        if node_num>-1:
            node_num_str = ' node #' + str(node_num)

                
        # instantiate process_data
        processing_data_inst = ProcessingData(
            self._input_data_dict,
            group_name=self._input_group_name,
            filter_file=self._filter_file,
            verbose=self._verbose
        )


        # instantiate event builder
        evtbuilder_inst = EventBuilder()
        
        
        # instantiate OF trigger and add to EventBuilder
        for trig_chan, trig_data in self._trigger_config.items():

            # get template
            template_tag = 'default'
            if 'template_tag' in trig_data:
                template_tag = trig_data['template_tag']
            
            template = processing_data_inst.get_template(
                trig_chan, tag=template_tag)   

                      
            # get psd
            psd_tag = 'default'
            if 'psd_tag' in trig_data:
                psd_tag = trig_data['psd_tag']

            psd, psd_freqs = processing_data_inst.get_psd(
                trig_chan, tag=psd_tag)
            
          
            # trigger name
            trigger_name = trig_chan
            if 'trigger_name' in trig_data:
                trigger_name = trig_data['trigger_name']
            
            # sample rate
            fs  = processing_data_inst.get_sample_rate()
                
            # instantiate optimal filter trigger
            oftrigger_inst = OptimumFilterTrigger(
                trigger_name, fs, template, psd)

            # add in EventBuilder
            evtbuilder_inst.add_trigger_object(
                trigger_name, oftrigger_inst)
            
            
                
        # intialize trigger counter
        # (only used to check maximum
        # number of events
        trigger_counter = 0

        # initialize output data frame
        output_df = None
        
        # loop series
        for series in series_list:


            if self._verbose:
                print('INFO' + node_num_str
                      + ': starting processing series '
                      + series)

            # initialize series dataframe
            series_df = None

                
            # set file list
            processing_data_inst.set_series(series)

            # output file name base (if saving data)
            output_base_file = None
            if lgc_save:
                output_base_file = (output_group_path
                                    + '/threshtrig'
                                    + '_' + series)

       
            # initialize dump counter
            dump_couter = 1
                        
            # loop events
            do_stop = False
            while (not do_stop):

                # -----------------------
                # Check number events
                # and memory usage
                # -----------------------
                ntriggers_limit_reached = (ntriggers>0
                                           and trigger_counter>=ntriggers)
                
                # flag memory limit reached
                memory_usage = 0
                memory_limit_reached = False
                if series_df is not None:
                    memory_usage = series_df.shape[0]*series_df.shape[1]*8
                    memory_limit_reached =  memory_usage  >= memory_limit

                total_memory_limit_reached = False
                if lgc_output and output_df is not None:
                    memory_usage = output_df.shape[0]*output_df.shape[1]*8
                    total_memory_limit_reached =  memory_usage  >= memory_limit

                memory_limit_reached  = (memory_limit_reached
                                         or total_memory_limit_reached)
                    
                # display
                if self._verbose:
                    if (trigger_counter%500==0 and trigger_counter!=0):
                        print('INFO' + node_num_str
                              + ': Local number of events = '
                              + str(trigger_counter)
                              + ' (memory = ' + str(memory_usage/1e6) + 'MB)')
                        
                # -----------------------
                # Read next event
                # -----------------------                
                success = processing_data_inst.read_next_event(
                    channels=self._trigger_channels
                )

                # end of file or raw data issue
                if not success:
                    do_stop = True
                    
                            
                # -----------------------
                # Handle stop or
                # nb trigger/memory limit
                # reached
                # -----------------------

                                
                # let's handle case we need to stop
                # or memory/nb events limit reached
                if (do_stop
                    or ntriggers_limit_reached
                    or memory_limit_reached):


                    # case nb triggers reached
                    if ntriggers_limit_reached:
                        nextra = trigger_counter-ntriggers
                        nkeep = len(series_df)-nextra
                        if nkeep>0:
                            series_df = series_df[0:nkeep]
                       
                    
                    # case output -> keep copy
                    if lgc_output:
                        if output_df is None:
                            output_df = series_df.copy()
                        else:
                            output_df = vx.concat(
                                [output_df, series_df.copy()])
                
                    # save file if needed
                    if lgc_save:
                        
                        # build hdf5 file name
                        dump_str = str(dump_couter)
                        file_name =  (output_base_file + '_F' + dump_str.zfill(4)
                                      + '.hdf5')
                        
                        # export
                        series_df.export_hdf5(file_name, mode='w')
                        
                        # increment dump
                        dump_couter += 1
                        print('INFO' + node_num_str
                              + ': Starting a new  dump for series '
                              + series)
                        
                        # initialize
                        del series_df
                        series_df = None
                        
                        
                    # case maximum number of events reached
                    # -> processing done!
                    if ntriggers_limit_reached:
                        print('INFO' + node_num_str
                              + ': Requested nb events reached. '
                              + 'Stopping processing!')
                        return output_df

                    # case memory limit reached
                    # -> processing needs to stop!
                    if lgc_output and memory_limit_reached:
                        print('WARNING' + node_num_str
                          + ': Stopping processing due to memory limit. '
                          + 'Not all events may have been processed!')
                        return output_df
                    
                # check if stop
                if do_stop:
                    break
               
                                 
                # -----------------------
                # process triggers
                # -----------------------
               
                # loop trigger channels
                for trig_chan, trig_data in self._trigger_config.items():
                    
                    # trigger name
                    trigger_name = trig_chan
                    if 'trigger_name' in trig_data.keys():
                        trigger_name = trig_data['trigger_name']

                    # get threshold
                    threshold = None
                    if 'threshold_sigma' in trig_data.keys():
                        threshold = trig_data['threshold_sigma']
                    else:
                        raise ValueError(
                            'ERROR: "treshold_sigma" missing in '
                            + 'yaml configuration file')
                        
                    # pileup window
                    pileup_window_msec = None
                    if 'pileup_window_msec' in trig_data.keys():
                        pileup_window_msec = (
                            trig_data['pileup_window_msec'])

                    pileup_window_samples = None
                    if 'pileup_window_samples' in trig_data.keys():
                        pileup_window_samples = (
                            trig_data['pileup_window_samples'])

                    # positive pulse
                    positive_pulses = True
                    if 'positive_pulses' in trig_data.keys():
                        positive_pulses = trig_data['positive_pulses']
                        
                    # get trace
                    trace = processing_data_inst.get_channel_trace(trig_chan)
                                     
                    # acquire trigger
                    evtbuilder_inst.acquire_triggers(
                        trigger_name,
                        trace,
                        threshold,
                        pileup_window_msec=pileup_window_msec,
                        pileup_window_samples=pileup_window_samples,
                        positive_pulses=positive_pulses)


                # -----------------------
                # build event
                # merge coincident triggers
                # -----------------------

                coincident_window_msec = None
                if 'coincident_window_msec' in self._evtbuilder_config.keys():
                    coincident_window_msec = (
                        self._evtbuilder_config['coincident_window_msec'])
                    
                coincident_window_samples = None
                if 'coincident_window_samples' in self._evtbuilder_config.keys():
                    coincident_window_samples = (
                        self._evtbuilder_config['coincident_window_samples'])
                    
                # get event metadata
                event_info = processing_data_inst.get_event_admin()
                


                # build event
                evtbuilder_inst.build_event(
                    event_info,
                    fs=fs,
                    coincident_window_msec=coincident_window_msec,
                    coincident_window_samples=coincident_window_samples
                )


                # get trigger data
                event_df = evtbuilder_inst.get_event_df()
                nb_triggers = len(event_df)

                # if no triggers -> next event
                if nb_triggers==0:
                    continue
                
                
                # increment counter
                trigger_counter += nb_triggers
                

                # -----------------------
                # Add metadata
                # -----------------------
                
                # add processing id
                event_df['processing_id'] = np.array(
                    [self._processing_id]*nb_triggers
                )
                
                """
                # add detector settings
                for channel in self._trigger_config.keys():
                    event_features.update(
                        processing_data_inst.get_channel_settings(channel)
                    )
                """

                # done processing event!
                # append event dictionary to dataframe
                if series_df is None:
                    series_df = event_df
                else:
                    series_df = vx.concat([series_df, event_df])

                    
        # return features
        return output_df
       


        
        
    def _get_file_list(self, file_path, series=None):
        """
        Get file list from path. Return as a dictionary
        with key=series and value=list of files

        Parameters
        ----------

        file_path : str or list of str 
           raw data group directory OR full path to HDF5  file 
           (or list of files). Only a single raw data group 
           allowed 
        
        series : str or list of str, optional
            series to be process, disregard other data from raw_path

        Return
        -------
        
        series_dict : dict 
          list of files for splitted inot series

        base_path :  str
           base path of the raw data

        group_name : str
           group name of raw data

        """
        
        # loop file path
        if not isinstance(file_path, list):
            file_path = [file_path]

        # initialize
        file_list = list()
        base_path = None
        group_name = None

        # loop files 
        for a_path in file_path:

                   
            # case path is a directory
            if os.path.isdir(a_path):

                if base_path is None:
                    base_path = str(Path(a_path).parent)
                    group_name = str(Path(a_path).name)
                            
                if series is not None:
                    if series == 'even' or series == 'odd':
                        file_name_wildcard = series + '_*.hdf5'
                        file_list = glob(a_path + '/' + file_name_wildcard)
                    else:
                        if not isinstance(series, list):
                            series = [series]
                        for it_series in series:
                            file_name_wildcard = '*' + it_series + '_*.hdf5'
                            file_list.extend(glob(a_path + '/' + file_name_wildcard))
                else:
                    file_list = glob(a_path + '/*.hdf5')
                
                # check a single directory
                if len(file_path) != 1:
                    raise ValueError('Only single directory allowed! ' +
                                     'No combination files and directories')
                
                    
            # case file
            elif os.path.isfile(a_path):

                if base_path is None:
                    base_path = str(Path(a_path).parents[1])
                    group_name = str(Path(Path(a_path).parent).name)
                    
                if a_path.find('.hdf5') != -1:
                    if series is not None:
                        if series == 'even' or series == 'odd':
                            if a_path.find(series) != -1:
                                file_list.append(a_path)
                        else:
                            if not isinstance(series, list):
                                series = [series]
                            for it_series in series:
                                if a_path.find(it_series) != -1:
                                    file_list.append(a_path)
                    else:
                        file_list.append(a_path)

            else:
                raise ValueError('File or directory "' + a_path
                                 + '" does not exist!')
            
        if not file_list:
            raise ValueError('ERROR: No raw input data found. Check arguments!')

        # sort
        file_list.sort()
      
        # get list of series
        series_dict = dict()
        h5reader = h5io.H5Reader()
        series_name = None
        file_counter = 0
        for file_name in file_list:

            # skip if filter file
            if 'filter' in file_name:
                continue
            
            # append file if series already in dictionary
            if (series_name is not None
                and series_name in file_name
                and series_name in series_dict.keys()):

                if file_name not in series_dict[series_name]:
                    series_dict[series_name].append(file_name)
                    file_counter += 1
                continue
            
            # get metadata
            metadata = h5reader.get_metadata(file_name)
            series_name = h5io.extract_series_name(metadata['series_num'])
            if series_name not in series_dict.keys():
                series_dict[series_name] = list()

            # append
            if file_name not in series_dict[series_name]:
                series_dict[series_name].append(file_name)
                file_counter += 1
                

        if self._verbose:
            print('INFO: Found total of '
                  + str(file_counter)
                  + ' files from ' + str(len(series_dict.keys()))
                  + ' different series number!')

      
        return series_dict, base_path, group_name
    
    
    def _read_config(self, yaml_file, available_channels):
        """
        Read and check yaml configuration
        file 

        Parameters
        ----------

        yaml_file : str
          yaml configuraton file name (full path)

        available_channels : list
          list of channels available in the file


        Return
        ------
        
        trigger_config : dict 
           dictionary with  trigger configuration
           for each trigger configuration

        eventbuilder_config : dict 
           dictionary with  eventbuilder configuration
           such as coincident events merging

        filter_file : str
            filter file name (full path)
        
        trigger_channels : list
            list of all channels used for triggering
           
        
        """

        # initialize output
        trigger_config = dict()
        eventbuilder_config = dict()
        filter_file = None
        trigger_channels = list()

        
        # open configuration file
        yaml_dict = dict()
        with open(yaml_file) as f:
            yaml_dict = yaml.safe_load(f)
        if not yaml_dict:
            raise ValueError('Unable to read processing configuration!')


        # filter file
        if 'filter_file' not in yaml_dict.keys():
            raise ValueError('ERROR: Filter file required!')
        
        filter_file = yaml_dict['filter_file']
        

        # trigger/eventbuilder info
        if 'trigger' not in yaml_dict.keys():
            raise ValueError('ERROR: Trigger info required!')

        trigger_data = yaml_dict['trigger']
        
        
        # Let's loop through keys and find trigger channels
        for key,val in trigger_data.items():
            
            # case it is not a trigger channel
            if not isinstance(val, dict):
                eventbuilder_config[key] = val
                continue

            # skip if disable
            if ('run' not in val.keys()
                or ('run' in val.keys()
                    and not val['run'])):
                continue
            
            # we need to split if ',' used 
            if ',' not in key:
                trigger_config[key] = val
            else:
                key_split = key.split(',')
                for key_sep in key_split:
                    trigger_config[key_sep] = val
                    
            # channel names
            split_char = None
            if ',' in key:
                split_char = ','
            elif '+' in key:
                split_char = '+'
            elif '-' in key:
                split_char = '-'
            elif '|' in key:
                split_char = '|'


            if split_char is None:
                trigger_channels.append(key)
            else: 
                key_split = key.split(split_char)
                for key_sep in key_split:
                    trigger_channels.append(key_sep)
                    
        # Let's check that there are no duplicate channels
        # or channels does not exist 
        channel_list = list()
        for trigger_chan in trigger_channels:
            
            # check if exist in data
            if trigger_chan not in available_channels:
                raise ValueError(
                    'ERROR: trigger channel ' + trigger_chan
                    + ' does not exist in data')
            
            # check if already trigger 
            if trigger_chan not in channel_list:
                channel_list.append(trigger_chan)
            else:
                raise ValueError(
                    'ERROR: ' + trigger_chan + ' trigger '
                    ' used multipled times!')
            
                    
                    
        # return
        return (trigger_config, eventbuilder_config,
                filter_file, trigger_channels)


    def _create_output_directory(self, base_path, facility):
        """
        Create output directory 

        Parameters
        ----------
        
        base_path :  str
           full path to base directory 
        
        facility : int
           id of facility 
    
        Return
        ------
          output_dir : str
            full path to created directory

        """

        now = datetime.now()
        series_day = now.strftime('%Y') +  now.strftime('%m') + now.strftime('%d') 
        series_time = now.strftime('%H') + now.strftime('%M')
        series_name = ('I' + str(facility) +'_D' + series_day + '_T'
                       + series_time + now.strftime('%S'))
        
        # prefix
        prefix = 'trigger'
        if self._processing_id is not None:
            prefix = self._processing_id + '_trigger'
        output_dir = base_path + '/' + prefix + '_' + series_name
        
        
        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir)
                os.chmod(output_dir, stat.S_IRWXG | stat.S_IRWXU | stat.S_IROTH | stat.S_IXOTH)
            except OSError:
                raise ValueError('\nERROR: Unable to create directory "'+ output_dir  + '"!\n')
    
        return output_dir
           

    def _split_series(self, ncores):
        """
        Split data  between nodes
        following series


        Parameters
        ----------

        ncores : int
          number of cores

        Return
        ------

        output_list : list
           list of dictionaries (length=ncores) containing 
           data
         

        """

        output_list = list()
        
        # split series
        series_split = np.array_split(self._series_list, ncores)

        # remove empty array
        for series_sublist in series_split:
            if series_sublist.size == 0:
                continue
            output_list.append(list(series_sublist))
            

        return output_list

    
    def _get_channel_list(self, file_dict):
        """ 
        Get the list of channels from raw data file
        
        Parameters
        ----------
        file_dict  : dict
           directionary with list of files for each series

        Return
        -------
        channels: list
          List of channels
    
        """

        # let's get list of channels available in file
        # first file
        file_name = str()
        for key, val in file_dict.items():
            file_name = val[0]
            break
        
        # get list from configuration
        h5 = h5io.H5Reader()
        detector_settings = h5.get_detector_config(file_name=file_name)
        h5.clear()
        
        return list(detector_settings.keys())


    def _get_facility(self, file_dict):
        """ 
        Get the list of channels from raw data file
        
        Parameters
        ----------
        file_dict  : dict
           directionary with list of files for each series

        Return
        -------
        channels: list
          List of channels
    
        """

        # let's get list of channels available in file
        # first file
        file_name = str()
        for key, val in file_dict.items():
            file_name = val[0]
            break
        
        # get list from configuration
        h5 = h5io.H5Reader()
        metadata = h5.get_metadata(file_name=file_name)
        facility = int(metadata['facility'])
        h5.clear()
        
        return facility