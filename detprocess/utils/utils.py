import os
import sys
import numpy as np
from scipy.optimize import curve_fit
import yaml
import copy
from yaml.loader import SafeLoader


__all__ = ['split_channel_name', 'extract_window_indices',
           'find_linear_segment', 'read_config']



def split_channel_name(channel_name,
                       available_channels,
                       separator=None):
    """
    Split channel name and return
    list of individual channels and separator
    """
    
    # intialize output
    channel_list = list()
        
    # case already an individual channel
    if (channel_name in available_channels
        or channel_name == 'all'
        or (separator is not None
            and separator not in channel_name)):

        channel_list.append(channel_name)
        return channel_list, None
        
    # keep copy
    channel_name_orig = channel_name
        
        
    # split
    if separator is not None:
        channel_split = channel_name.split(separator)
        for chan in channel_split:
            if chan:
                channel_list.append(chan.strip())

    else:

        # remove all known channels from string
        for chan in available_channels:
            if chan in channel_name:
                channel_list.append(chan)
                channel_name = channel_name.replace(chan, '')
                
        # find remaining separator
        separator_list = list()
        channel_name = channel_name.strip()
        for sep in channel_name:
            if sep not in separator_list:
                separator_list.append(sep)

        separator_list = list(set(separator_list))
               
        if len(separator_list) == 1:
            separator = separator_list[0]
        else:
            raise ValueError(
                f'ERROR: Unable to split {channel_name_orig}, '
                f'possibly because some channels are not in the '
                f'raw data and cannot be used in the yaml file. '
                f'Available channels: {available_channels}')

        # check if channel available in raw data
        for chan in channel_list:
            if chan not in available_channels:
                raise ValueError('ERROR: Channel "' + chan
                                 + '" does not exist in '
                                 + 'raw data! Check yaml file!')
            
    return channel_list, separator
        
   


def extract_window_indices(nb_samples,
                           nb_samples_pretrigger, fs,
                           window_min_from_start_usec=None,
                           window_min_to_end_usec=None,
                           window_min_from_trig_usec=None,
                           window_max_from_start_usec=None,
                           window_max_to_end_usec=None,
                           window_max_from_trig_usec=None):
    """
    Calculate window index min and max from various types
    of window definition
    
    Parameters
    ---------

        nb_samples : int
          total number of samples 

        nb_samples_pretrigger : int
           number of pretrigger samples

        fs: float
           sample rate

        window_min_from_start_usec : float, optional
           OF filter window start in micro seconds defined
           from beginning of trace

        window_min_to_end_usec : float, optional
           OF filter window start in micro seconds defined
           as length to end of trace
       
        window_min_from_trig_usec : float, optional
           OF filter window start in micro seconds from
           pre-trigger (can be negative if prior pre-trigger)


        window_max_from_start_usec : float, optional
           OF filter window max in micro seconds defined
           from beginning of trace

        window_max_to_end_usec : float, optional
           OF filter window max in micro seconds defined
           as length to end of trace


        window_max_from_trig_usec : float, optional
           OF filter window end in micro seconds from
           pre-trigger (can be negative if prior pre-trigger)
         



    Return:
    ------

        min_index : int
            trace index window min

        max_index : int 
            trace index window max
    """
    
    # ------------
    # min window
    # ------------
    min_index = 0
    if  window_min_from_start_usec is not None:
        min_index = int(window_min_from_start_usec*fs*1e-6)
    elif window_min_to_end_usec is not None:
        min_index = (nb_samples
                     - abs(int(window_min_to_end_usec*fs*1e-6))
                     - 1)
    elif window_min_from_trig_usec is not None:
        min_index = (nb_samples_pretrigger 
                     + int(window_min_from_trig_usec*fs*1e-6))

    # check
    if min_index<0:
        min_index=0
    elif min_index>nb_samples-1:
        min_index=nb_samples-1


    # -------------
    # max index
    # -------------
    max_index = nb_samples -1
    if  window_max_from_start_usec is not None:
        max_index = int(window_max_from_start_usec*fs*1e-6)
    elif window_max_to_end_usec is not None:
        max_index = (nb_samples
                     - abs(int(window_max_to_end_usec*fs*1e-6))
                     - 1)
    elif window_max_from_trig_usec is not None:
        max_index =  (nb_samples_pretrigger 
                      + int(window_max_from_trig_usec*fs*1e-6))

    # check
    if max_index<0:
        max_index=0
    elif max_index>nb_samples-1:
        max_index=nb_samples-1

        
    if max_index<min_index:
        raise ValueError('ERROR window calculation: '
                         + 'max index smaller than min!'
                         + 'Check configuration!')
    
        

    return min_index, max_index


def find_linear_segment(x, y, tolerance=0.05):
    """
    Find linear segment within tolerance using first 3 points
    fit (distance based on standardized X and Y using 
    first 3 points mean/std). 
    """
    # check length
    if len(x)<3:
        print('WARNING: Not enough points to check linearity!')
        return []

    if len(x) != len(y):
        raise ValueError('ERROR: X and Y arrays should have same length!')
    
    # standardize data using mean/std first 3 points
    xmean = np.mean(x[:3])
    xstd = np.std(x[:3])
    x = (x - xmean) / xstd
    
    ymean = np.mean(y[:3])
    ystd = np.std(y[:3])
    y = (y - ymean) / ystd
    
    # Use only the first three points to fit a linear
    # regression line
    slope, intercept = np.polyfit(x[:3], y[:3], 1)
    
    # Calculate fitted values for all points
    y_fit = slope * x + intercept
    
    # Compute deviations for all points
    deviations = np.abs(y - y_fit)


    # get linear index list
    # the deviation for the first 3 points used for the fit
    # should be very small. Will use tolerance/10
    index_list = list()
    nb_points = len(deviations)
    for idx in range(nb_points):
        deviation = deviations[idx]
        if (idx<3 and deviation>tolerance/10):
            return []
        if deviation>tolerance:
            if nb_points>idx+1:
                if deviations[idx+1]>tolerance:
                    break
            else:
                break    
        else:
            index_list.append(idx)
        
    return index_list



def read_config(yaml_file, available_channels):
    """
    Read configuration (yaml) file 
    
    Parameters
    ----------

    yaml_file : str
        yaml configuraton file name (full path)

    Return
    ------
        
    processing_config : dict 
        dictionary with  processing configuration
        
    """

    # obsolete keys
    obsolete_keys = {'nb_samples': 'trace_length_samples',
                     'nb_pretrigger_samples': 'pretrigger_length_samples'}

    # configuration types
    configuration_types = ['global', 'feature',
                           'didv', 'noise',
                           'template', 'trigger']
    
    
    # available global config
    global_parameters = ['filter_file']

    # global trigger parameters
    global_trigger_parameters = ['coincident_window_msec',
                                 'coincident_window_samples']
    
    # available channel separator
    separators = [',', '+', '-', '|']

    # available channels
    if isinstance(available_channels, str):
        available_channels =  [available_channels]
                    
    # load yaml file
    yaml_dict = yaml.load(open(yaml_file, 'r'),
                          Loader=_UniqueKeyLoader)

    if not yaml_dict:
        raise ValueError('ERROR: No configuration loaded'
                         'Something went wrong...')

    if 'include' in  yaml_dict:
        include_files = yaml_dict['include']
        if isinstance(include_files, str):
            include_files = [include_files]
        for afile in include_files:
            yaml_dict.update(yaml.load(open(afile, 'r'),
                                       Loader=_UniqueKeyLoader))
        yaml_dict.pop('include')
            

        
    # let's split configuration based on type of processing
    config_dicts = dict()
    for config_name  in configuration_types:
        
        # set to None
        config_dicts[config_name] = dict()
      
        # add if available
        if config_name in yaml_dict.keys():

            # add copy
            config_dicts[config_name] = copy.deepcopy(
                yaml_dict[config_name]
            )
            
            # remove from yaml file
            yaml_dict.pop(config_name)

    # global config based on  hard coded list
    for param in global_parameters:
        config_dicts['global'][param] = None
        if param in yaml_dict.keys():
            config_dicts['global'][param] = copy.deepcopy(
                yaml_dict[param]
            )
            yaml_dict.pop(param)
                

    # the rest of parameter are for  feature processing
    for param in  yaml_dict.keys():
        config_dicts['feature'][param] = copy.deepcopy(
            yaml_dict[param]
        )

    # rename obsolete keys
    for old_key, new_key in obsolete_keys.items():
        config_dicts = _rename_key_recursively(config_dicts, old_key, new_key)

        
    # intialize output
    processing_config = dict()
        
    # Loop configuration and check/cleanup parameters
    for config_name  in configuration_types:

        # check if there is anything available
        if not config_dicts[config_name]:
            continue
        
        # initialize  output
        processing_config[config_name] = dict()

        # dictionary
        config_dict = config_dicts[config_name]

        # global parameters
        if config_name == 'global':
            processing_config[config_name] = config_dict.copy()
            continue

        # configuration for 'all' (individual) channels
        # -> enable all
        if 'all' in config_dict.keys():
            
            # loop available channels and copy parameters
            for chan in available_channels:
                
                processing_config[config_name][chan] = copy.deepcopy(
                    config_dict['all']
                )
                
            # remove from dict    
            config_dict.pop('all')

        # let's split channels that are separated
        # by a comma and check duplicate
        parameter_list = list()
        iter_list = list(config_dict.keys())
        for chan in iter_list:
            
            if ',' in chan:
                
                # split channels
                split_channels ,_ = split_channel_name(
                    chan, available_channels, separator=','
                )

                # loop and add config for split channels
                for split_chan in split_channels:

                    # error if multiple times defined
                    if split_chan in parameter_list:
                        raise ValueError(f'ERROR: channel {split_chan} '
                                         f'defined multiple times in the '
                                         f'{config_name} configuration. '
                                         f'This is not allowed to avoid mistake.'
                                         f'Check yaml file!')

                    # copy dict 
                    config_dict[split_chan] = copy.deepcopy(
                        config_dict[chan]
                    )
                    
                    parameter_list.append(split_chan)

                # remove from config
                config_dict.pop(chan)
            
            else:

                if chan in parameter_list:
                    raise ValueError(f'ERROR: parameter or channel {chan} '
                                     f'defined multiple times in the '
                                     f'{config_name} configuration. '
                                     f'This is not allowed to avoid mistake!'
                                     f'Check yaml file!')

                parameter_list.append(chan)
                
        # check duplication of "length" parameters
        if ('coincident_window_msec' in parameter_list
            and 'coincident_window_samples' in  parameter_list):
            raise ValueError(f'ERROR: Found both "coincident_window_msec" '
                             f'and "coincident_window_samples" in '
                             f'{config_name} configuration. Choose between '
                             f'msec or samples!')
        
            
        # loop channels/keys and add to output configuratiob
        for chan, config in config_dict.items():

            # check if empty 
            if not config:
                raise ValueError(
                    f'ERROR: empty channel/parameter '
                    f'{chan} for {config_name} configuration!')

            # case individual channels
            if chan in  available_channels:
               
                if not isinstance(config, dict):
                    raise ValueError(f'ERROR: Empty channel {chan} in the '
                                     f'{config_name} configuration. Check '
                                     f'yaml file!')
                # check if disabled
                if ('disable' in config and config['disable']
                    or 'run' in config and not config['run']):

                    # remove if needed
                    if chan in processing_config[config_name]:
                        processing_config[config_name].pop(chan)
                        
                else:
                    # add
                    if chan in processing_config[config_name]:
                        processing_config[config_name][chan].update(
                            copy.deepcopy(config)
                        )
                    else:
                        processing_config[config_name][chan] = (
                            copy.deepcopy(config)
                        )

                    if 'disable' in processing_config[config_name][chan]:
                        processing_config[config_name][chan].pop('disable')

                continue

            # check if non-channel parameter
            if (config_name == 'trigger'
                and chan in global_trigger_parameters):
                processing_config[config_name][chan] = config
                continue
            
            # check if channel contains with +,-,| separator
            split_channels, separator = split_channel_name(
                chan, available_channels, separator=None
            )

            if separator in separators:
                
                # check if disabled
                if ('disable' in config and config['disable']
                    or 'run' in config and not config['run']):
                    if chan in processing_config[config_name]:
                        processing_config[config_name].pop(chan)
                else:
                    processing_config[config_name][chan] = (
                        copy.deepcopy(config)
                    )
                    
                    if 'disable' in processing_config[config_name][chan]:
                        processing_config[config_name][chan].pop('disable')

                continue

            # at this point, parameter is unrecognized
            raise ValueError(f'ERROR: Unrecognized parameter '
                             f'{chan} in the {config_name} '
                             f'configuration. Perhaps a channel '
                             f'not in raw data?')

        
    # check feature processing
    if 'feature' in processing_config:
        
        # 1: loop channels and remove disabled algorithm
        chan_list = list(processing_config['feature'].keys())
        for chan in chan_list:

            chan_config = copy.deepcopy(
                processing_config['feature'][chan]
            )
            algorithm_list = list()
            for param, val in chan_config.items():

                if not isinstance(val, dict):
                    continue

                if 'run' not in val.keys():
                    raise ValueError(
                        f'ERROR: Missing "run" parameter for channel '
                        f'{chan}, algorithm {param}. Please fix the '
                        f'configuration yaml file')

                if not val['run']:
                    processing_config['feature'][chan].pop(param)
                else:
                    algorithm_list.append(param)

            # remove channel if no algorithm
            if not algorithm_list:
                processing_config['feature'].pop(chan)
                
    # return
    return processing_config



class _UniqueKeyLoader(SafeLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None,
                'expected a mapping node, but found %s' % node.id,
                node.start_mark)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(f'ERROR: Duplicate key "{key}" '
                                 f'found in the yaml file for same '
                                 f'channel and algorithm. '
                                 f'This is not allowed to avoid '
                                 f'unwanted configuration!')
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


def _rename_key_recursively(d, old_key, new_key):
    """
    Recursively renames a key in a dictionary and 
    all its sub-dictionaries.
    """

    # check if dictionary
    if not isinstance(d, dict):
        return d
    
    for key in list(d.keys()):  
        if isinstance(d[key], dict):
            _rename_key_recursively(d[key], old_key, new_key)
        if key == old_key:
            d[new_key] = d.pop(old_key)
    return d
