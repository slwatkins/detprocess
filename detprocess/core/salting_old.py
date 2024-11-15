import numpy as np
import os
import vaex as vx
import pandas as pd
import qetpy as qp
from math import log10, floor
from glob import glob
from pathlib import Path
import types
import itertools
import pytesdaq.io as h5io
import math
import array
from detprocess.core.oftrigger import OptimumFilterTrigger
from detprocess.process.randoms import Randoms
from detprocess.core.filterdata import FilterData
from scipy.signal import correlate
from scipy.fft import ifft, fft, next_fast_len
from scipy import stats, signal, interpolate, special, integrate

__all__ = [
    'Salting'
]


#class Salting(FilterData):
    """
    Class for injecting salt into datasets for multiple channels. Can be used to
    understand cut efficiencies.

    Attributes
    ----------
    asd:asdfasdfasdfasdfasdfasdf

    """

    def __init__(self,channels,filterfile,templatekeys,noisekeys, verbose=True,yaml_path=None):
        """
        Initialize class

        Parameters:
        ----------

        verbose : bool, optional
          display information


        """

        # initialize raw data dictionary
        self._raw_data = None
        self._group_name = None
        self._raw_base_path = None
        self._series_list = None
        self._detector_config = None
        self.channels = channels
        self._ivdidv_data = dict()
        self._saltarraydict = dict()
        self.templatesdict = dict()
        self.noisedict = dict()
        self.filttemplatesdict = dict()
        self.channelsdict = dict()

        
        # intialize randoms dataframe
        self._dataframe = None

        # intialize event list
        self._event_list = None

        # sample rate stored for convenience
        self._fs = None
        
        # store the energies from the DM spectra that you have sampled
        self._DMenergies = np.array([])
        #self._Channelenergies = np.array([])
        
        self._verbose = verbose

        if yaml_path is not None:
            if os.path.isfile(yaml_path):
                self._save_path = os.path.dirname(yaml_path)
                self._file_name = os.path.basename(yaml_path)
            elif os.path.isdir(yaml_path):
                self._save_path = yaml_path
            else:
                raise ValueError('ERROR: "yaml_path" should be a '
                                 'file or path!')

        super().__init__(verbose=verbose)

        separators = ['|', '+']
        channelsfull = []
        for r in range(1, len(channels) + 1):
            # Generate all combinations of size r
            for combo in itertools.combinations(channels, r):
                # For each combination, generate all permutations
                for perm in itertools.permutations(combo):
                    for sep in separators:
                        key = sep.join(perm)
                        channelsfull.append(key)    
        channelsfull = list(set(channelsfull)) 

        if filterfile is not None:
            self.load_hdf5(filterfile)

            parameter_list = [
                'psd', 'template',
                'csd'
            ]   
            for chan, chan_dict in self._filter_data.items():
                
                if chan not in  self.channelsdict.keys():
                    self.channelsdict[chan] = dict()
    
                for par_name, val in chan_dict.items():
                    
                    # check if metadata
                    if '_metadata' in par_name:
                        continue
                    
                    # check if metadata
                    if '_inds' in par_name:
                        continue

                    # find tag
                    par_split = par_name.split('_')
                    tag = par_split[-1]
                    base_par = par_name[:-len(tag)-1]
                    if (base_par not in parameter_list
                        and len(par_split)>=2):
                        tag = '_'.join(par_split[-2:])
                        base_par = par_name[:-len(tag)-1]
                        if base_par not in parameter_list:
                            tag = 'default'
                            base_par = par_name
                    if tag not in self.channelsdict[chan]:
                        self.channelsdict[chan][tag] = list()
                                
                    msg = base_par
                    if isinstance(val, pd.Series):
                        msg += ': pandas.Series '
                    elif  isinstance(val, pd.DataFrame):
                        msg += ': pandas.DataFrame  '
                    elif isinstance(val, np.ndarray):
                        ndim = val.ndim
                        msg += f': {ndim}D numpy.array  ' 
                    else:
                        msg += (str(type(val)) + ' ')
                                
                    msg += str(val.shape)
                                                        
                    self.channelsdict[chan][tag].append(msg)

            for chan in channelsfull:
                if chan in channels:
                    self.filttemplatesdict[chan] = {}
                    self.filttemplatesdict[chan] = {}
                if chan in self.channelsdict:
                    self.templatesdict[chan] = {}
                    self.noisedict[chan] = {}
                    subkeyvals = self.channelsdict[chan]
                    for subkey, vals in subkeyvals.items():
                        if any('csd' in item for item in vals):
                            csdarray, csdfreqs = self.get_csd(chan, fold=False, return_metadata=False, tag=subkey)
                            self.noisedict[chan][subkey] = (csdarray,csdfreqs)
                            continue
                        templatearray,templatetime = self.get_template(chan,return_metadata=False, tag=subkey)
                        self.templatesdict[chan][subkey] = (templatearray,templatetime)
            
            nnoise = 0
            ntemps = 0
            for chan in self.channelsdict:
                if self.noisedict[chan] == {}:
                    continue
                for n in noisekeys:
                    if n in self.noisedict[chan]:
                        nnoise +=1
                        csd = self.noisedict[chan][n][0]
                if nnoise > len(noisekeys) : raise ValueError('ERROR: Same noise key found more than once! Noisekeys cannot be repeated in multiple channels!')
                for t in templatekeys:
                    if t in self.templatesdict[chan]:
                        ntemps = 0
                        templates_td = self.templatesdict[chan][t][0]
                        tempinst = OptimumFilterTrigger(trigger_channel=chan, fs=1.25e6, template=templates_td, noisecsd=csd, pretrigger_samples=12500)
                        templates_td = templates_td.squeeze(axis=1)
                        tempinst.update_trace(templates_td)
                        filttemp = tempinst.get_filtered_trace()
                        if '|' in chan:
                            originalchannel = t.split('_')[1]
                        if originalchannel in self.filttemplatesdict:
                            if "single" in t:
                                self.filttemplatesdict[originalchannel]['singles'] = (filttemp)
                            else: self.filttemplatesdict[originalchannel]['default'] = (filttemp)
                    if ntemps > len(templatekeys) : raise ValueError('ERROR: Same template key found more than once! Tempkeys cannot be repeated in multiple channels!')
                    else:
                        continue
                                
    def get_raw_template(self,channel,tag):
            return self.templatesdict[channel][tag][0],self.templatesdict[channel][tag][1]
    def get_filtered_template(self,channel,tag):
            return self.filttemplatesdict[channel][tag][0]
    def get_detector_config(self, channel):
        """
        get detector config
        """
        if self._detector_config is None:
            print('WARNING: No data has been set yet! '
                  'Returning None ')
            return None
        elif channel not in self._detector_config.keys():
            print(f'WARNING: No channel {channel}  found! '
                  f'Returning None ')
            return None
        return self._detector_config[channel]
                
    def get_sample_rate(self):
        """
        Get sample rate in Hz ("calc_psd" needs to be 
        called before)
        """

        return self._fs

    def clear_randoms(self):
        """
        Clear internal data, however
        keep self._filter_data
        """

        # clear data
        self._dataframe = None
        self._event_list = None
        self._raw_data = None
        self._group_name = None
        self._raw_base_path = None
        self._series_list = None
        self._detector_config = None
        self._fs = None
        self._offset = dict()
        
    def set_randoms(self, raw_path, series=None,
                    dataframe=None,
                    event_list=None):
        """
        Set raw data path and vaex dataframe 
        with randoms events (either dataframe directly
        or path to vaex hdf5 files)
        """
        
        # initialize data
        self.clear_randoms()
        

        # check arguments
        if (dataframe is not None
            and event_list is not None):
            raise ValueError('ERROR: choose between "dataframe" and '
                             '"event_list", not both')
        
        # get file list
        raw_data, output_base_path, group_name = (
            self._get_file_list(raw_path,
                                series=series)
        )
        
        if not raw_data:
            raise ValueError('No raw data files were found! '
                             + 'Check configuration...')
        
        # store as internal data
        self._raw_base_path = output_base_path
        self._raw_data = raw_data
        self._group_name = group_name
        self._series_list = list(raw_data.keys())
        self._detector_config = dict()

        # check dataframe
        if dataframe is not None:
            
            if isinstance(dataframe, vx.dataframe.DataFrame):
                if len(dataframe)<1:
                    raise ValueError('ERROR: No event found in the datafame!')
            else:
                dataframe = self._load_dataframe(dataframe)

            self._dataframe = dataframe

        elif event_list is not None:
            self._event_list = event_list

        # check filter data
        if self._filter_data:
            print('WARNING: Some noise data have been previously saved. '
                  'Use "describe()" to check. If needed clear data '
                  'using "clear_data(channels=None, tag=None)" function!')
            
            
    def generate_randoms(self, raw_path, series=None,
                         random_rate=None,
                         nevents=None,
                         min_separation_msec=100,
                         edge_exclusion_msec=50,
                         restricted=False,
                         calib=False,
                         ncores=1):
        """
        Generate randoms from continuous data
        """

        # initialize data
        self.clear_randoms()

        # get file list
        raw_data, output_base_path, group_name = (
            self._get_file_list(raw_path,
                                series=series,
                                restricted=restricted,
                                calib=calib)
        )
        
        if not raw_data:
            raise ValueError('No raw data files were found! '
                             + 'Check configuration...')
        
        # store as internal data
        self._raw_data = raw_data
        self._group_name = group_name
        self._raw_base_path = output_base_path
        self._series_list = list(raw_data.keys())
        self._detector_config = dict()

             
        # generate randoms
        rand_inst = Randoms(raw_path, series=series,
                            verbose=self._verbose,
                            restricted=restricted,
                            calib=calib)
        
        self._dataframe = rand_inst.process(
            random_rate=random_rate,
            nrandoms=nevents,
            min_separation_msec=min_separation_msec,
            edge_exclusion_msec=edge_exclusion_msec,
            lgc_save=False,
            lgc_output=True,
            ncores=ncores
        )

        # check filter data
        if self._filter_data:
            print('WARNING: Some noise data have been previously saved. '
                  'Use "describe()" to check. If needed clear data '
                  'using "clear_data(channels=None, tag=None)" function!')
            
    def _get_file_list(self, file_path,
                       series=None,
                       is_raw=True,
                       restricted=False,
                       calib=False):
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

        restricted : boolean
            if True, use restricted data 
            if False (default), exclude restricted data

        Return
        -------
        
        series_dict : dict 
          list of files for splitted inot series

        base_path :  str
           base path of the raw data

        group_name : str
           group name of raw data

        """

        # convert file_path to list 
        if isinstance(file_path, str):
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

      
        # convert to series dictionary so can be easily split
        # in multiple cores
        
        series_dict = dict()
        h5reader = h5io.H5Reader()
        series_name = None
        file_counter = 0
        
        for afile in file_list:

            file_name = str(Path(afile).name)
                        
            # skip if filter file
            if 'filter_' in file_name:
                continue

            # skip didv
            if ('didv_' in file_name
                or 'iv_' in file_name):
                continue
                      
            if 'treshtrig_' in file_name:
                continue

            # calibration
            if (calib
                and 'calib_' not in file_name):
                continue

            # not calibration
            if not calib: 
                
                if 'calib_' in file_name:
                    continue
                            
                # restricted
                if (restricted
                    and 'restricted' not in file_name):
                    continue

                # not restricted
                if (not restricted
                    and 'restricted' in file_name):
                    continue
                      
            # append file if series already in dictionary
            if (series_name is not None
                and series_name in afile
                and series_name in series_dict.keys()):

                if afile not in series_dict[series_name]:
                    series_dict[series_name].append(afile)
                    file_counter += 1
                continue
            
            # get metadata
            if is_raw:
                metadata = h5reader.get_metadata(afile)
                series_name = h5io.extract_series_name(metadata['series_num'])
            else:
                sep_start = file_name.find('_I')
                sep_end = file_name.find('_F')
                series_name = file_name[sep_start+1:sep_end]
                              
            if series_name not in series_dict.keys():
                series_dict[series_name] = list()

            # append
            if afile not in series_dict[series_name]:
                series_dict[series_name].append(afile)
                file_counter += 1
       
            
        if self._verbose:
            msg = ' raw data file(s) from '
            if not is_raw:
                msg = ' dataframe file(s) from '
                
            print('INFO: Found total of '
                  + str(file_counter)
                  + msg
                  + str(len(series_dict.keys()))
                  + ' different series number!')

      
        return series_dict, base_path, group_name
    
    def set_iv_didv_results_from_file(self, file_name,
                                      poles=2,
                                      channels=None):
        """
        set dIdV and/or IV sweep results from an HDF5 file
        """

        self.load_hdf5(file_name)

        if channels is None:
            channels = self._filter_data.keys()
            if not channels:
                raise ValueError(f'ERROR: No data loaded... '
                                 f'Check file {file_name}')
        elif isinstance(channels, str):
            channels = [channels]
       
        for chan in channels:

            # check if filter data
            if chan not in self._filter_data.keys():
                raise ValueError(f'ERROR: No data loaded for channel {chan}. '
                                 f'Check file {file_name} !')
            # dIdV results
            didv_results = None
            try:
                didv_results = self.get_didv_results(chan, poles=poles)
            except:
                print(f'WARNING: No {poles}-poles dIdV results found for '
                      f'channel {chan}!')

            # IV results
            ivsweep_results = None
            try:
                ivsweep_results = self.get_ivsweep_results(chan)
            except:
                pass


            self.set_iv_didv_results_from_dict(
                chan,
                didv_results=didv_results,
                poles=poles,
                ivsweep_results=ivsweep_results
            )
   
    def set_iv_didv_results_from_dict(self, channel,
                                      didv_results=None,
                                      poles=2,
                                      ivsweep_results=None):
        """
        Set didv from dictionary for specified channel
        """

        # check if channel exist
        if channel not in self._ivdidv_data.keys():
            self._ivdidv_data[channel] = dict()


        # save poles
        self._poles = poles
   
        # dIdV results
        if didv_results is not None:
            if poles is None:
                raise ValueError('ERROR: dIdV poles (2 or 3) required!')

            # add to filter data
            metadata = None
            if 'metadata' in didv_results:
                metadata = didv_results['metadata']
            self.set_didv_results(
                channel,
                didv_results,
                poles=poles,
                metadata=metadata
            )

            # Add small signal parameters
            if 'smallsignalparams' in didv_results.keys():
                self._ivdidv_data[channel]['smallsignalparams'] = (
                    didv_results['smallsignalparams'].copy()
                )

            else:
                raise ValueError(f'ERROR: dIdV fit results '
                                 f'does not contain "smallsignalparams" '
                                 f'for channel {channel}!')
            
            # add "biasparams"
            if ('biasparams' in didv_results.keys()
                and didv_results['biasparams'] is not None):
                self._ivdidv_data[channel]['biasparams'] = didv_results['biasparams']

                
            if('dpdi_3poles_default' in didv_results.keys()
               and didv_results['dpdi_3poles_default'] is not None):
               self._ivdidv_data[channel]['dpdi_3poles_default'] = didv_results['dpdi_3poles_default']


        # IV results
        if ivsweep_results is not None:
            print("IV results isn't none!")
            # add to filter data
            self.set_ivsweep_results(
                channel,
                ivsweep_results,
                'noise')

            # add to noise data
            if 'biasparams' not in self._ivdidv_data[channel]:
                self._ivdidv_data[channel]['biasparams'] = ivsweep_results
            else:
                self._ivdidv_data[channel]['biasparams'].update(ivsweep_results)

            # add more quantities
            if 'rn' not in  self._ivdidv_data[channel]['biasparams']:
                self._ivdidv_data[channel]['biasparams']['rn'] = (
                    ivsweep_results['rn']
                )

            # add more quantities
            if 'rp' not in  self._ivdidv_data[channel]['biasparams']:
                self._ivdidv_data[channel]['biasparams']['rp'] = (
                    ivsweep_results['rp']
                )
                

            if 'rshunt' not in self._ivdidv_data[channel]['biasparams']:
                self._ivdidv_data[channel]['biasparams']['rshunt'] = (
                    ivsweep_results['rshunt']
                )
                
    def sample_DMpdf(self,function, xrange, nsamples=1000, npoints=10000, normalize_cdf=True):
        """
        Produces randomly sampled values based on the arbitrary PDF defined
        by `function`, done using inverse transform sampling.

        Parameters
        ----------
        function : FunctionType
            The 1D probability density function to be randomly sampled from.
        xrange : array_like
            A 1D array of length 2 that defines the range over which the PDF
            in `function` is defined. Outside of this range, it is assumed that
            the PDF is zero.
        nsamples : int, optional
            The number of random samples that we wish to create from the PDF
            defined by `function`.
        npoints : int, optional
            The number of points to use in the numerical integration to evaluate
            the CDF of `function`. This is also the number of points used in the
            interpolation of the inverse of the CDF.
        normalize_cdf : bool, optional
            Boolean value to normalize the CDF or not. If True, the CDF is normalized
            by the PDF area. If False, no normalization is done.

        Returns
        -------
        rvs : ndarray
            The random samples that were taken from the inputted PDF defined by
            `function`. This is a 1D array of length `nsamples`.

        Raises
        ------
        TypeError
            If inputted `function` is not of FunctionType

        Notes
        -----
        For a discussion of inverse transform sampling, see the Wikipedia page:
            https://en.wikipedia.org/wiki/Inverse_transform_sampling

        """
        if not isinstance(function, types.FunctionType):
            raise TypeError("Inputted variable function is not FunctionType.")

        x = np.linspace(xrange[0], xrange[1], num=npoints)
        pdf = function(x)

        cdf = integrate.cumtrapz(pdf, x=x, initial=0.0)

        if normalize_cdf:
            cdf /= cdf[-1]

        inv_cdf = interpolate.interp1d(cdf, x)

        samples = np.random.rand(nsamples)
        sampled_energies = inv_cdf(samples)

        self._DMenergies = sampled_energies
        return sampled_energies

    def get_DMenergies(self):
        return self._DMenergies
        
    def channel_energy_split(self,mean=0.5, std_dev=0.2, npairs=10):
        #make n pairs which will be the same as the number of events to sim
        listofsplits = []
        print(npairs)
        for i in range(npairs):
            # Generate random numbers from a Gaussian distribution
            random_numbers = np.random.normal(loc=mean, scale=std_dev, size=2)
            
            # Clip values to be between 0 and 1
            random_numbers = np.clip(random_numbers, 0, 1)
            
            # Check if the sum is positive (important for the normalization step)
            if np.sum(random_numbers) > 0:
                # Normalize to sum to 1
                random_numbers = random_numbers / np.sum(random_numbers)
            
            listofsplits.extend([random_numbers])
            #self._Channelenergies = listofsplits
        return listofsplits

    def get_energy_perchannel(self):
        return self._Channelenergies

    def generate_salt(self,nb_events,energies,channels, templatetag, PCE,Usespectrum = True,cont_data = None):
        # generate the random selections in time to generate the salts
        self.generate_randoms(cont_data, series=None, nevents=nb_events, min_separation_msec=100, ncores=1)
        #get the energies 
        if Usespectrum:
            DM_energies = self.get_DMenergies() * 1e3 #this is hardcoded! This is because the dRdE spectrum I'm using is in keV!
            if nb_events > len(DM_energies):
                raise ValueError('ERROR: nb_events to generate cannot be larger '
                                 'than the number of sampled energies!')
        else: DM_energies = energies
        if not isinstance(templatetag,str):
            raise ValueError('Error: Only one template type can be used at a time, and it must be a string!')
        # get the values to put into dict
        #salt_var_dict = {'salt_amplitude': list(),
        #                 'salt_filt_amplitude': list(), 
        #                 'salt_template_tag': list(),
        #                'salt_energy': list()}
        
        salt_var_dict = {'salt_template_tag': list(),
                         'salt_recoil_energy_eV': list()}
        base_keys = ['salt_amplitude', 'salt_filt_amplitude',  'salt_energy_eV']
        
        # Create channel-specific keys
        for key in base_keys:
            for chan in channels:
                salt_var_dict[f'{key}_{chan}'] = [[] for _ in range(nb_events)]
        #get the scaling factors for the template
        #this includes fraction of deposited energy in each channel and PCE
        if len(channels) > 1:
            #salts = np.zeros((nb_events,2))
            energiesplits = self.channel_energy_split(npairs=nb_events)     
            #get the template to use for the salt
            salts = [[] for _ in range(nb_events)]
            filtsalts = [[] for _ in range(nb_events)]
            for i,chan in enumerate(channels):
                template,time_array = self.get_raw_template(chan,templatetag)
                filttemplate = self.get_filtered_template(chan,templatetag)
                dpdi = self.get_dpdi(channel=chan,poles=3)
                norm_energy = qp.get_energy_normalization(time_array, template, dpdi=dpdi[0], lgc_ev=True)
                scaled_template = template/norm_energy
                for n in range(nb_events):
                    if 'single' in templatetag: 
                        fullyscaled_template = scaled_template * DM_energies[n]
                        scaledfilttemplate = filttemplate * DM_energies[n]
                    else: 
                        fullyscaled_template = scaled_template * DM_energies[n]*energiesplits[n][i]*PCE[i]
                        scaledfilttemplate = filttemplate * DM_energies[n]*energiesplits[n][i]*PCE[i]
                        
                    salts[n].append([fullyscaled_template])   
                    filtsalts[n].append([scaledfilttemplate]) 
                    if 'saltarray' not in self._saltarraydict:
                        self._saltarraydict['saltarray'] = []
                        self._saltarraydict['filtsaltarray'] = []
                        self._saltarraydict['timearray'] = []  
                    if len(self._saltarraydict['saltarray']) <= n:
                        self._saltarraydict['saltarray'].append([])
                        self._saltarraydict['filtsaltarray'].append([])
                        self._saltarraydict['timearray'].append([])
                    if len(salt_var_dict['salt_template_tag']) <= n:
                        salt_var_dict['salt_template_tag'].append([])
                        salt_var_dict['salt_recoil_energy_eV'].append([])
                        
                    self._saltarraydict['saltarray'][n].append(fullyscaled_template)
                    self._saltarraydict['filtsaltarray'][n].append(scaledfilttemplate)
                    self._saltarraydict['timearray'][n].append(time_array)
                    
                    salt_var_dict[f'salt_amplitude_{chan}'][n] = max(fullyscaled_template)
                    salt_var_dict[f'salt_energy_eV_{chan}'][n] = DM_energies[n]*energiesplits[n][i]
                    salt_var_dict[f'salt_filt_amplitude_{chan}'][n] = max(scaledfilttemplate) 
                    salt_var_dict[f'salt_template_tag'][n] = templatetag
                    salt_var_dict[f'salt_recoil_energy_eV'][n] = DM_energies[n]
        else: 
            salts = []
            filtsalts = []
            template,time_array = self.get_raw_template(chan,templatetag)
            filttemplate = self.get_filtered_template(chan,templatetag)
            dpdi = self.get_dpdi(channel=channels,poles=3)
            norm_energy = qp.get_energy_normalization(time_array, template, dpdi = dpdi[0], lgc_ev=True)
            scaled_template = template/norm_energy
            #have to ask Bruno about correct scaling from template
            for n in range(nb_events):
                if 'single' in templatetag: 
                    fullyscaled_template = scaled_template * DM_energies[n]
                    filttemplate = filttemplate * DM_energies[n]
                else: 
                    fullyscaled_template = scaled_template * DM_energies[n]*PCE[i]
                    filttemplate = filttemplate * DM_energies[n]*PCE[i]
                salts.append(fullyscaled_template)
                filtsalts.append(filttemplate)
                if 'saltarray' not in self._saltarraydict:
                    self._saltarraydict['saltarray'] = [] 
                    self._saltarraydict['timearray'] = []
                    self._saltarraydict['filtsaltarray'] = []
                self._saltarraydict['saltarray'].append(fullyscaled_template)
                self._saltarraydict['filtsaltarray'].append(scaledfilttemplate)
                self._saltarraydict['timearray'].append(time_array)
                salt_var_dict[f'salt_amplitude_{chan}'] = max(fullyscaled_template)
                salt_var_dict[f'salt_energy_eV_{chan}'] = DM_energies[n]
                salt_var_dict[f'salt_filt_amplitude_{chan}'] = max(filttemplate)
                salt_var_dict[f'salt_template_tag'] = templatetag
                
        
        df = vx.from_dict(salt_var_dict)
        self._dataframe = self._dataframe.join(df)
        return salts,filtsalts  

    def inject_raw_salt(self,traces,metadata,channels):
        newtraces = [[] for _ in range(len(traces))]
        #templates_td = self.templatesdict[chan][templatetag][0]
        # = OptimumFilterTrigger(trigger_channel=chan, fs=1.25e6, template=templates_td, noisecsd=csd, pretrigger_samples=12500)
        #newfilttraces = [[] for _ in range(len(traces))]
        for n, event in enumerate(traces):
            for chan,waveform in enumerate(event):
                if len(event) > 1 and len(channels) > 1:
                    salt=self._saltarraydict['saltarray'][n][chan]
                    filtsalt=self._saltarraydict['filtsaltarray'][n][chan]
                    times=self._saltarraydict['timearray'][n][chan]
                else: 
                    salt=self._saltarraydict['saltarray'][n]
                    times=self._saltarraydict['timearray'][n]
                    filtsalt=self._saltarraydict['filtsaltarray'][n]                    
                newtrace = np.array(waveform,copy=True)
                salts_before_ADC=np.zeros(np.shape(waveform),dtype=float)
                nb_samples=len(times)
                simtime = self._dataframe['trigger_time'].values[n] 
                simtime = simtime.astype(int)
                salt_and_baseline = salt+newtrace[0]
                salt_and_baseline -= salt_and_baseline[0]
                salts_before_ADC[simtime:simtime+nb_samples] += salt_and_baseline
                newtrace += salts_before_ADC
                newtraces[n].append([newtrace])
        return newtraces


