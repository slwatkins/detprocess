import os
import sys
import numpy as np
from pathlib import Path
from scipy.optimize import curve_fit
import yaml
import copy
from yaml.loader import SafeLoader
import re
from pprint import pprint
from pytesdaq.io import convert_length_msec_to_samples
from qetpy.utils import convert_channel_name_to_list, convert_channel_list_to_name
import vaex as vx
from datetime import datetime
import stat
from glob import glob


__all__ = ['split_channel_name', 'extract_window_indices',
           'find_linear_segment', 'create_directory', 'create_series_name',
           'get_dataframe_series_list', 'get_indices_from_freq_ranges',
           'is_empty']


    
def create_series_name(facility):
    """
    Create output directory 
    
    Parameters
    ----------
    
    facility : int
       facility number
    
    
    Return
    ------
          
    name : str
        
    """

    now = datetime.now()
    series_day = now.strftime('%Y') +  now.strftime('%m') + now.strftime('%d') 
    series_time = now.strftime('%H') + now.strftime('%M')
    series_name = ('I' + str(facility) +'_D' + series_day + '_T'
                   + series_time + now.strftime('%S'))
    
    return series_name
        

def create_directory(directory_path):
    """
    create (sub) directory
    """

    if not os.path.isdir(directory_path):
        try:
            os.makedirs(directory_path)
            os.chmod(directory_path, stat.S_IRWXG | stat.S_IRWXU | stat.S_IROTH | stat.S_IXOTH)
        except OSError:
            raise ValueError('\nERROR: Unable to create directory "'+ directory_path  + '"!\n')
        
        


def split_channel_name(channel_name,
                       available_channels=None,
                       separator=None,
                       label=None):
    """
    Split channel name and return
    list of individual channels and separator
    """

    # allowed separators
    separators = [',' ,'+' ,'-' ,'|']


    # strip whitespace
    channel_name = channel_name.replace(' ','')
    
    # check if channel_name has any separators
    has_separator = False
    for sep in separators:
        if sep in channel_name:
             has_separator = True
             break
    if not has_separator:
        return [channel_name], None


    # case available_channels is None
    if  available_channels is None:

        if separator is None:
            raise ValueError(
                'ERROR: separator required when '
                '"available_channels" not provided! ')
        elif separator == '-':
             raise ValueError(
                'ERROR: "available_channels" required '
                'when using separator "-"')
        elif ((separator == '-' or separator == '+')
              and (',' in channel_name or '|' in channel_name)):
            raise ValueError(
                f'ERROR: Channels cannot be split with '
                f'{separator} before channels split with '
                f'"," and "|"')
        else:
            channel_list = channel_name.split(separator)
            return channel_list, separator



    # case already an individual channel
    # or no separator found
    if (channel_name in available_channels
        or channel_name == 'all'):
        return [channel_name], None

    # let's 
    channel_check = channel_name
    channel_list = []
    for chan in available_channels:
        if chan in channel_check:
            channel_check = channel_check.replace(chan, '')
            channel_list.append(chan)
            
    separator_list =  [x for x in channel_check]
    separator_list =  list(set(separator_list))
    channel_list = list(set(channel_list))

    non_separator_list = []
    for sep in separator_list:
        if sep not in separators:
            non_separator_list.append(sep)
    if non_separator_list:
        if label is None:
            raise ValueError(
                f'ERROR: Unidentified channel(s) in yaml file! '
                f'Perhaps not in raw data... or misspelled?'
            )
        else:
            raise ValueError(
                f'ERROR: Unidentified channel(s) in yaml file '
                f'({label}) '
                f'Perhaps not in raw data... or misspelled?'
            )


    if separator is None:
        if len(separator_list) == 1:
            separator_list = separator_list[0]
        return channel_list, separator_list

    # case separator provided
    
    # check separator
    if separator not in separators:
        raise ValueError(
            f'ERROR: separator "{separator}" not '
            f'recognized. Allowed separator '
            f'{separators} ')

    # check if separator in channe_name
    if separator not in channel_name:
        return [channel_name], None

    # now let's split channel name
    pattern = f"([{re.escape(separator)}])"
    split_parts = re.split(pattern, channel_name)
      
    channel_list = []
    current_name = ''
    for part in split_parts:
        
        if part in available_channels:

            # add current_name if constructed
            if current_name:
                channel_list.append(current_name)
            current_name = ''
            
            # add part ot list
            channel_list.append(part)


        elif part == separator:

            if (current_name
                and  current_name in available_channels):
                channel_list.append(current_name)
                current_name = ''     
            elif current_name:
                current_name += part
        else:
            current_name += part
        
    if current_name and  current_name in available_channels:
        channel_list.append(current_name)

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

def is_empty(param):
    """
    check if empty
    """
    
    if param is None:
        return True
    try:
        return len(param) == 0
    except TypeError:
        return False


def get_dataframe_series_list(file_path):
    """
    Get list of series of all files in data_path
        
    Parameters
    ----------

    file_path : str
       path to dataframe(s) 


    Return
    -------
    
     series_list : list of series name
    
    """
    
    # check argument
    if not os.path.isdir(file_path):
        raise ValueError('ERROR: Expecting a directory!')

    
    # initialize output
    series_list = []

    # get all files
    file_list =  glob(file_path + '/*.hdf5')
    if not file_list:
        raise ValueError(f'ERROR: No HDF5 files found in {self._raw_path}')
    
    # make unique and sort
    file_list = list(set(file_list))
    file_list.sort()
        
    # loop file
    for afile in file_list:
        aname = str(Path(afile).name)
        sep_start = aname.find('_I')
        sep_end = aname.find('_F')
        series_name = aname[sep_start+1:sep_end]
        
        if series_name not in series_list:
            series_list.append(series_name)
            
    return series_list

def get_indices_from_freq_ranges(freqs, freq_ranges):
    """
    convert frequency ranges to index ranges. Return 
    also name freq[0]_freq[1]
    """

    name_list = list()
    index_ranges = list()
        
    for it, freq_range in enumerate(freq_ranges):
                        
        # ignore if not a range
        if len(freq_range) != 2:
            continue
            
        # low/high frequency
        f_low = abs(freq_range[0])
        f_high = abs(freq_range[1])
            
        if f_low > f_high:
            f_low = abs(freq_range[1])
            f_high = abs(freq_range[0])
                
                    
        # indices
        ind_low = np.argmin(np.abs(freqs - f_low))
        ind_high = np.argmin(np.abs(freqs - f_high))

        # check if proper range
        if ind_low == ind_high:
            if ind_low < len(freqs)-2:
                ind_high = ind_low + 1
            else:
                continue
            
                
        # store
        name = f'{round(f_low)}_{round(f_high)}'
        
        if name in name_list:
            continue
            
        name_list.append(name)
        index_ranges.append((ind_low, ind_high))

            
    return name_list, index_ranges
