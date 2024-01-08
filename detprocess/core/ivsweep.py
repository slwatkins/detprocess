import os
import pandas as pd
import numpy as np
from pprint import pprint
import pytesdaq.io as h5io
import qetpy as qp
from glob import glob
import vaex as vx
from pathlib import Path
from detprocess.core.filterdata import FilterData
from detprocess.core.didv import DIDVAnalysis
from detprocess.core.noise import Noise
import matplotlib.pyplot as plt


__all__ = [
    'IVSweepAnalysis'
]


class IVSweepAnalysis(FilterData):
    """
    Class to manage iv/sweep calculations using 
    QETpy
    """

    def __init__(self, verbose=True):
        """
        Initialize class

        Parameters:
        ----------

        verbose : bool, optional
          display information

        """

        # IV, DIDV, Noise, Template objects    
        self._ibis_objects = dict()
        self._didv_objects = dict()
        self._noise_objects = dict()
        self._template_objects = dict()
        self._didv_summary = dict()

        # rshunt/rshunt_err if not in raw data
        # rp/rp error if no SC points
        self._readout_params = dict()

        # resolution
        self._resolution_data = dict()
        
        # instantiate base class
        super().__init__(verbose=verbose)
        
        
    def clear(self, channels=None):
        """
        Clear all data
        """
        # objects
        self._ibis_objects = dict()
        self._didv_objects = dict()
        self._noise_objects = dict()
        self._template_objects = dict()
        self._didv_summary = dict()
        self._resolution_data = dict()

        # filter data
        self.clear_data()


    def set_data_from_file(self, file_name):
        """
        Load IV processed data from file
        """

        self.load_hdf5(file_name)
        self.describe()


    def set_data_from_dict(self, data_dict, tag='default'):
        """
        Set data using dictionary  with 
        keys=channels, values=dataframe
        """

        for chan, df in data_dict.items():
            if not isinstance(df, pd.DataFrame):
                raise ValueError(
                    'ERROR: Unrecognized format. Dictionary '
                    'item should be a pandas dataframe!'
                )
            self.set_data_from_dataframe(chan, df, tag=tag)
            

        self.describe()

    def set_data_from_dataframe(self, channel, df,
                                tag='default'):
        """
        Set data from dataframe for a specific channel
        """

        if not isinstance(df, pd.DataFrame):
            raise ValueError(
                'ERROR: Unrecognized format. Dictionary '
                'item should be a pandas dataframe!'
            )

        # sort
        df = df.sort_values('tes_bias',
                            ascending=False,
                            key=abs,
                            ignore_index=True)
        
        # store dataframe
        self.set_ivsweep_data(channel,
                              df,
                              metadata=None,
                              tag=tag)
        
        self.describe()

        
    
    def set_rshunt(self, channels,
                   rshunt=None, rshunt_err=None):
        """
        Set rshunt and/or rshunt error for one or more channels
        if not stored in raw data file.
 
        If multiple channels, then all arguments should be a list. 
        """


        # convert to list 
        if isinstance(channels, str):
            channels = [channels]

        params = {'rshunt': rshunt,
                  'rshunt_err': rshunt_err}

        for param, param_val in params.items():
            if param_val is not None:
                if  (isinstance(param_val, int)
                     or isinstance(param_val, float)) :
                    param_val = [param_val]
                if len(param_val) == 1:
                    param_val = param_val*len(channels)
                elif len(param_val)!=len(channels):
                    raise ValueError(
                        f'ERROR: {param} list should be have length = '
                        ' # channels')
            else:
                param_val = [None]*len(channels)

            # replace
            params[param] =  param_val
      
            
        for ichan, chan in enumarate(channels):
            if chan not in self._readout_params.keys():
                self._readout_params[chan] = dict()
            self._readout_params[chan]['rshunt'] = params['rshunt'][ichan]
            self._readout_params[chan]['rshunt_err'] = params['rshunt_err'][ichan]


    def set_rp(self, channels,
               rp=None, rp_err=None):
        """
        Set rp and/or rp error for one or more channels if no SC points.  
        If multiple channels, then all arguments should be a list. 
        """


        # convert to list 
        if isinstance(channels, str):
            channels = [channels]


        params = {'rp': rp,
                  'rp_err': rp_err}

        for param, param_val in params.items():
            if param_val is not None:
                if  (isinstance(param_val, int)
                     or isinstance(param_val, float)):
                    param_val = [param_val]
                if len(param_val) == 1:
                    param_val = param_val*len(channels)
                elif len(param_val)!=len(channels):
                    raise ValueError(
                        f'ERROR: "{param}" list should be have length = '
                        ' # channels')
            else:
                param_val = [None]*len(channels)

            # replace
            params[param] = param_val
            
        for ichan,chan in enumarate(channels):
            if chan not in self._readout_params.keys():
                self._readout_params[chan] = dict()
            self._readout_params[chan]['rp'] =  params['rp'][ichan]
            self._readout_params[chan]['rp_err'] =  params['rp_err'][ichan]


               
    def analyze_sweep(self, channels=None, nnorms=None, nscs=None,
                      lgc_invert_offset='auto',
                      lgc_plot=False, lgc_save_plot=False,
                      plot_save_path=None,
                      tag='default',
                      **kwargs):
        """
        Analyze IV sweep (multi-channels) to  calculate I0,R0,P0 
        in function of bias.
        """

        # --------------------------------
        # Check arguments
        # ---------------------------------
        
        # check data
        available_channels = list()
        ivdata_label = 'ivsweep_data_' + tag
        for chan, chan_dict in self._filter_data.items():
            if ivdata_label in chan_dict.keys():
                available_channels.append(chan)

        if not available_channels:
            raise ValueError('ERROR: No channel available. '
                             'Set data first!')
        if channels is None:
            channels = available_channels
        else:
            if isinstance(channels, str):
                channels = [channels]
            for chan in channels:
                if chan not in available_channels:
                    raise ValueError(f'ERROR: No data for channel {chan}'
                                     ' available. Set data first!')
        # number of channels
        nb_channels = len(channels)

        # check number of normal / SC
        if nnorms is not None:
            if isinstance(nnorms, int):
                nnorms = [nnorms]
            elif len(nnorms) != nb_channels:
                raise ValueError(
                    'ERROR: "nnorms" should have same length'
                    'as "channels"')
        else:
            nnorms = [None]*nb_channels

        if nscs is not None:
            if isinstance(nscs, int):
                nscs = [nscs]
            elif len(nscs) != nb_channels:
                raise ValueError(
                    'ERROR: "nscs" should have same length'
                    'as "channels"')
        else:
            nscs = [None]*nb_channels

        # --------------------------------
        # Check sweep data for all channels
        # first
        # ---------------------------------
        
        # loop channel and check if sc / normal points available
        # check Rshunt and Rp (needed if no SC points)
        for ichan, chan in enumerate(channels):
            
            df = self.get_ivsweep_data(chan, tag=tag)
            df = df.sort_values('tes_bias',
                                ascending=False,
                                key=abs,
                                ignore_index=True)

            
            # check processing has been done
            if ('offset_noise' not in df.columns
                and 'offset_didv' not in df.columns):
                raise ValueError(f'ERROR: Unable to find an offset in'
                                 ' the channel {chan} dataframe! Check data!')
            
            # Rp / Rshunt
            rp = None
            rp_err = None
            rshunt = None
            rshunt_err = None

            # first check if parameters have been set manually
            for param_chan, param_dict in self._readout_params.items():
                if chan == param_chan:
                    if 'rp' in  param_dict.keys():
                        rp = param_dict['rp']
                    if 'rp_err' in  param_dict.keys():
                        rp_err = param_dict['rp_err']
                    if 'rshunt' in  param_dict.keys():
                        rshunt = param_dict['rshunt']
                    if 'rshunt_err' in  param_dict.keys():
                        rshunt_err = param_dict['rshunt_err']


            # If None, use dataframe
            if rp is None:
                if ('rp' in df.columns
                    and df['rp'].values[0] != np.nan):
                    rp = df['rp'].values[0]
            if rp_err is None:
                if ('rp_err' in df.columns
                    and df['rp_err'].values[0] != np.nan):
                    rp_err = df['rp_err'].values[0]
                elif rp is not None:
                    rp_err = rp*0.02
            if rshunt is None:
                if ('rshunt' in df.columns
                    and df['rshunt'].values[0] != np.nan):
                    rshunt = df['rshunt'].values[0]
            if rshunt_err is None:
                if ('rshunt_err' in df.columns
                    and df['rshunt_err'].values[0] != np.nan):
                    rshunt_err = df['rshunt_err'].values[0]
                elif rshunt is not None:
                    rshunt_err = rshunt*0.02
                    
            # store parameters
            if chan not in self._readout_params.keys():
                self._readout_params[chan] = dict()
            self._readout_params[chan]['rp'] = rp
            self._readout_params[chan]['rp_err'] = rp_err
            self._readout_params[chan]['rshunt'] = rshunt
            self._readout_params[chan]['rshunt_err'] = rshunt_err
            
                           
            # normal points
            nnorm = nnorms[ichan]
            if nnorm is None:
                normal_idx = np.where(df['state']=='normal')[0]
                if len(normal_idx) == 0:
                    raise ValueError(
                        'ERROR: Unknow number of normal points for '
                        'channel {chan}. Add "nnorms" argument!')
                else:
                    nnorms[ichan] = len(normal_idx)
                    
            # SC points
            nsc = nscs[ichan]
            if nsc is None:
                nsc_idx = np.where(df['state']=='sc')[0]
                if len(nsc_idx) == 0:
                    raise ValueError(
                        f'ERROR: Unknow number of SC points for channel {chan}. '
                        'To disable SC, set Rp and Rp error using "set_rp()" '
                        'function and set "nscs=0" argument'
                    )
                else:
                    nscs[ichan] = len(nsc_idx)
            elif nsc == 0:
                if rp is None or rp_err is None:
                    raise ValueError(
                        f'ERROR: Rp is required for channel {chan} when nscs=0 '
                        'Use set_rp() function first!')
                
            # check rshunt
            if rshunt is None or rshunt_err is None:
                raise ValueError(
                    f'ERROR: Rshunt is required for channel {chan}! '
                    'Use set_rshunt() function first!'
                )
            

        # --------------------------------
        # Loop channel and do analysis
        # ---------------------------------
        # Loop channels
        for ichan, chan in enumerate(channels):
            
            # get dataframe
            df = self.get_ivsweep_data(chan, tag=tag)
            df = df.sort_values('tes_bias',
                                ascending=False,
                                key=abs,
                                ignore_index=True)

            
            # sc/normal range
            nnorm = nnorms[ichan]
            range_normal = range(nnorm)
            
            range_sc = []
            nsc = nscs[ichan]
            fitsc = False
            if nsc > 0:
                fitsc = True
                range_sc = range(len(df)-nsc,
                                 len(df))
            
            # parameters dictionary
            param_dict = self._readout_params[chan]

            # check noise/dIdV sweep
            data_types = list()
            labels = list()
            if 'offset_noise' in df.columns:
                data_types.append('noise')
                labels.append(f'{chan} noise')
            if 'offset_didv' in df.columns:
                data_types.append('didv')
                labels.append(f'{chan} dIdV')
                
            # get values from dataframe
            tes_bias = df['tes_bias'].values
            offset = df['offset_' + data_types[0]].values
            offset_err = df['offset_err_' + data_types[0]].values
            if len(data_types)==2:
                tes_bias = np.vstack([tes_bias, tes_bias])
                tes_bias[1,:] = df['tes_bias_' + data_types[1]].values
                offset = np.vstack([offset, offset])
                offset[1,:] =  df['offset_' + data_types[1]].values
                offset_err = np.vstack([offset_err, offset_err])
                offset_err[1,:] =  df['offset_err_' + data_types[1]].values


            # invert offset
            offset = offset.copy()
            if (lgc_invert_offset != 'auto'
                and lgc_invert_offset):
                offset = -1*offset
                
            # rp guess
            rp = param_dict['rp']
            rp_err= param_dict['rp_err']
            if rp is None:
                rp = 0
                rp_err = 0
                
            # instantiate
            ivobj = qp.IBIS(dites=offset,
                            dites_err=offset_err,
                             ibias=tes_bias,
                            ibias_err=np.zeros_like(tes_bias),
                            rsh=param_dict['rshunt'],
                            rsh_err=param_dict['rshunt_err'],
                            rp_guess=rp,
                            rp_err_guess=rp_err,
                            chan_names=labels,
                            fitsc=fitsc,
                            normalinds=list(range_normal),
                            scinds=list(range_sc),)
                
            ivobj.analyze(**kwargs)
            
            rn_check = ivobj.rnorm[0, 0]
            if (lgc_invert_offset == 'auto' and rn_check<0):
                lgc_invert_offset = True
                ivobj.dites = -1*ivobj.dites
                ivobj.analyze(**kwargs)
            
                     
            # store results in dataframe/series
            df['lgc_invert_offset'] = lgc_invert_offset
            df['vb'] = ivobj.vb[0,0,:]
            df['vb_err'] = ivobj.vb_err[0,0,:]
            
            for itype, data_type in enumerate(data_types):
                
                # parameter dependend of bias point
                df['p0_' + data_type] = ivobj.ptes[0, itype]
                df['p0_err_' + data_type] = ivobj.ptes_err[0, itype]
                df['r0_' + data_type] = ivobj.r0[0, itype]
                df['r0_err_' + data_type] = ivobj.r0_err[0, itype]
                df['i0_' + data_type] = ivobj.ites[0, itype]
                df['i0_err_' + data_type] = ivobj.ites_err[0, itype]

                # get results independent of sweep
                rp = ivobj.rp[0, itype]
                rp_err =  ivobj.rp_err[0, itype]
                rn = ivobj.rnorm[0, itype]
                rn_err = ivobj.rnorm_err[0, itype]
                i0_off = ivobj.ioff[0, itype]
                i0_off_err = ivobj.ioff_err[0, itype]
                ibias_off = ivobj.ibias_off[0, itype]
                ibias_off_err = ivobj.ibias_off_err[0, itype]
                ibias_true = ivobj.ibias_true[0, itype]
                ibias_true_err = ivobj.ibias_true_err[0, itype]


                # store in df and dictionary
                df['rp_' + data_type] = rp
                df['rp_err_' + data_type] = rp_err
                df['rn_' + data_type] = rn
                df['rn_err_' + data_type] = rn_err
                df['i0_off_' + data_type] = i0_off
                df['i0_off_err_' + data_type] = i0_off_err
                df['ibias_off_' + data_type] = ibias_off
                df['ibias_off_err_' + data_type] = ibias_off_err
                df['ibias_true_' + data_type] = ibias_true
                df['ibias_true_err_' + data_type] = ibias_true_err
                df['percent_rn_' + data_type] = round(1000*df['r0_' + data_type]/rn)/10
                

                # store also in in pandas series
                results = dict()
                results['rp'] = rp
                results['rp_err'] = rp_err
                results['rn'] = rn
                results['rn_err'] = rn_err
                results['rshunt'] = param_dict['rshunt']
                results['rshunt_err'] = param_dict['rshunt_err']
                results['i0_off'] = i0_off
                results['i0_off_err'] = i0_off_err
                results['ibias_off'] = ibias_off
                results['ibias_off_err'] = ibias_off_err
                results['lgc_invert_offset'] = lgc_invert_offset
                results['close_loop_norm'] = (
                    df['close_loop_norm_' + data_type].values[0]
                )
                results['output_variable_offset'] = (
                    df['output_variable_offset_' + data_type].values[0]
                )
                results['output_variable_gain'] = (
                    df['output_variable_gain_' + data_type].values[0]
                )
                results['group_name_sweep'] = (
                    df['group_name_' + data_type].values[0]
                )


                # add i0 variable offset (usead for true current calculations)
                norm = results['close_loop_norm'] 
                voltage_offset = results['output_variable_offset']
                gain =  results['output_variable_gain']
                results['i0_variable_offset'] = (
                    voltage_offset * gain/norm
                )

                # save rp/rn
                self._readout_params[chan]['rp'] = rp
                self._readout_params[chan]['rp_err'] =  rp_err
                self._readout_params[chan]['rn'] = rn
                self._readout_params[chan]['rn_err'] =  rn_err
                              
                # convert to pandas series
                series = pd.Series(results)
                self.set_ivsweep_result(chan,
                                        series,
                                        data_type,
                                        metadata=None,
                                        tag=tag)

                       
            # save data
            self._ibis_objects[chan] = ivobj
            self.set_ivsweep_data(chan, df, tag=tag)
            
            if lgc_plot:
                print(f'Channel {chan} IV/dIdV Sweep Fits:')
                ivobj.plot_all_curves(lgcsave=False,
                                      savepath=None,
                                      savename=None)


                
    def analyze_didv(self, channels=None,
                     enable_normal=True, enable_sc=True,
                     enable_transition=True,
                     normal_percent_rn_min=99.8, nb_points_normal_max=4,
                     sc_percent_rn_max=0.05, nb_points_sc_max=4,
                     transition_percent_rn_max=70,
                     transition_percent_rn_min=5,
                     inf_loop_gain_approx='auto',
                     lgc_plot=False, lgc_save_plot=False,
                     plot_save_path=None,
                     tag='default',
                     **kwargs):
        
        """
        Fit dIdV for specified or all available channels
          1 pole fit for normal and sc tes
          2+3 poles fit for TES in transition
        
        """
        
      
        # fit SC data
        if enable_sc:

            
            self.fit_didv_sc(
                channels=channels,
                percent_rn_max=sc_percent_rn_max,
                nb_points_max=nb_points_sc_max,
                lgc_plot=lgc_plot,
                lgc_save_plot=lgc_save_plot,
                plot_save_path=plot_save_path,
                tag=tag,
                **kwargs
            )
            
             
        # fit normal data
        if enable_normal:
                
            self.fit_didv_normal(
                channels=channels,
                percent_rn_min=normal_percent_rn_min,
                nb_points_max=nb_points_normal_max,
                lgc_plot=lgc_plot,
                lgc_save_plot=lgc_save_plot,
                plot_save_path=plot_save_path,
                tag=tag,
                **kwargs
            )            

        # fit transition data
        if enable_transition:
                
            self.fit_didv_transition(
                channels=channels,
                percent_rn_min=transition_percent_rn_min,
                percent_rn_max=transition_percent_rn_max,
                lgc_plot=lgc_plot,
                lgc_save_plot=lgc_save_plot,
                plot_save_path=plot_save_path,
                tag=tag,
                **kwargs
            )            
            

    def fit_didv_sc(self, channels=None,
                    percent_rn_max=0.05, nb_points_max=4,
                    lgc_plot=False, lgc_save_plot=False,
                    plot_save_path=None,
                    tag='default',
                    **kwargs):
        """
        """

        self._fit_didv('sc', channels=channels,
                       percent_rn_max=percent_rn_max,
                       nb_points_max=nb_points_max,
                       lgc_plot=lgc_plot,
                       lgc_save_plot=lgc_save_plot,
                       plot_save_path=plot_save_path,
                       tag=tag,
                       **kwargs)
        



    def fit_didv_normal(self, channels=None,
                        percent_rn_min=99.8, nb_points_max=4,
                        lgc_plot=False, lgc_save_plot=False,
                        plot_save_path=None,
                        tag='default',
                        **kwargs):
        """
        """

        self._fit_didv('normal', channels=channels,
                       percent_rn_min=percent_rn_min,
                       nb_points_max=nb_points_max,
                       lgc_plot=lgc_plot,
                       lgc_save_plot=lgc_save_plot,
                       plot_save_path=plot_save_path,
                       tag=tag,
                       **kwargs)
        
    def fit_didv_transition(self, channels=None,
                            percent_rn_min=5,
                            percent_rn_max=70,
                            lgc_plot=False, lgc_save_plot=False,
                            inf_loop_gain_approx='auto',
                            plot_save_path=None,
                            tag='default',
                            **kwargs):
        """
        """
        
        self._fit_didv('transition', channels=channels,
                       percent_rn_min=percent_rn_min,
                       percent_rn_max=percent_rn_max,
                       inf_loop_gain_approx=inf_loop_gain_approx,
                       lgc_plot=lgc_plot,
                       lgc_save_plot=lgc_save_plot,
                       plot_save_path=plot_save_path,
                       tag=tag,
                       **kwargs)
        


    def plot_didv_summary(self, channel):
        """
        """

        if channel not in self._didv_summary:
            print(f'ERROR: No dIdV analysis done for  {channel}')
            return

        print(f'Summary dIdV Analysis for {channel}')
        print('=====================================')

        # SC 
        if 'sc' in self._didv_summary[channel]:
            data = self._didv_summary[channel]['sc']

            print(f'\nTES Superconducting Measurements:\n')
            print('Rp from dIdV fit = {:.2f} +/- {:.3f} mOhms'.format(
                np.array(data['rp'])*1e3, np.array(data['rp_err'])*1e3))
            print('Rp rom IV Sweep = {:.2f} +/- {:.3f} mOhms'.format(
                np.array(data['rp_iv'])*1e3, np.array(data['rp_iv_err'])*1e3))

        # normal
        if 'normal' in self._didv_summary[channel]:
            data = self._didv_summary[channel]['normal']

            print(f'\nTES Normal Measurements:\n')
            print('Rn from dIdV fit = {:.2f} +/- {:.3f} mOhms'.format(
                np.array(data['rn'])*1e3, np.array(data['rn_err'])*1e3))
            print('Rn rom IV Sweep = {:.2f} +/- {:.3f} mOhms'.format(
                np.array(data['rn_iv'])*1e3, np.array(data['rn_iv_err'])*1e3))

         
        
        # transition
        if 'transition' in self._didv_summary[channel]:

            data = self._didv_summary[channel]['transition']
            print(f'\nTES in Transition Measurements:')

                  
            plt.plot(data['Rn %'], data['chi2'],  'bo')
            plt.title(f'{channel} dIdV Fit chi2')
            plt.xlabel('Rn %')
            plt.ylabel('Chi2/Ndof')
            plt.grid(True)
            plt.show()
            
            # plot small signal
            
            print('\nSmall Signal Parameters')
            
            plt.errorbar(data['Rn %'], data['l'], yerr=data['l_err'],
                         fmt='o', ecolor='red', capsize=5,
                         linestyle='-', color='blue')
            plt.title(f'{channel} loop gain (l)')
            plt.xlabel('Rn %')
            plt.ylabel('loop gain (l)')
            plt.grid(True)
            plt.show()
        
            plt.errorbar(data['Rn %'], data['beta'], yerr=data['beta_err'],
                         fmt='o', ecolor='red', capsize=5,
                         linestyle='-', color='blue')
            plt.title(f'{channel} beta')
            plt.xlabel('Rn %')
            plt.ylabel('beta')
            plt.grid(True)
            plt.show()

            plt.errorbar(np.array(data['Rn %']), np.array(data['L'])*1e9,
                         yerr=np.array(data['L_err'])*1e9,
                         fmt='o', ecolor='red', capsize=5,
                         linestyle='-', color='blue')
            plt.title(f'{channel} Effective inductance L')
            plt.xlabel('Rn %')
            plt.ylabel('L [nH]')
            plt.grid(True)
            plt.show()

            plt.errorbar(np.array(data['Rn %']), np.array(data['tau0'])*1e3,
                         yerr=np.array(data['tau0_err'])*1e3,
                         fmt='o', ecolor='red', capsize=5,
                         linestyle='-', color='blue')
            plt.title(f'{channel} Tau0 ')
            plt.xlabel('Rn %')
            plt.ylabel('Tau0 [ms]')
            plt.grid(True)
            plt.show()
            
            plt.errorbar(data['Rn %'], data['gratio'], yerr=data['gratio_err'],
                         fmt='o', ecolor='red', capsize=5,
                         linestyle='-', color='blue')
            plt.title(f'{channel} gratio')
            plt.xlabel('Rn %')
            plt.ylabel('gratio')
            plt.grid(True)
            plt.show()


            print('\nFall Times')
            pars = ['tau+', 'tau-','tau3']
            for par in pars:
                plt.plot(np.array(data['Rn %']),
                         np.array(data[par])*1e3,  'bo')
                plt.title(f'{channel} {par}')
                plt.xlabel('Rn %')
                plt.ylabel(f'{par} [ms]')
                plt.grid(True)
                plt.show()


    def calc_energy_resolution(self, channels=None, template=None, template_name=None,
                               collection_eff=1, lgc_plot=False, tag='default'):
        """
        Calculate resolution
        """

        # channels
        available_channels = list()
        ivdata_label = 'ivsweep_data_' + tag
        for chan, chan_dict in self._filter_data.items():
            if ivdata_label in chan_dict.keys():
                available_channels.append(chan)

        if not available_channels:
            raise ValueError('ERROR: No channel available. '
                             'Set data first!')
        if channels is None:
            channels = available_channels
        else:
            if isinstance(channels, str):
                channels = [channels]
            for chan in channels:
                if chan not in available_channels:
                    raise ValueError(f'ERROR: No data for channel {chan}'
                                     ' available. Set data first!')
        # number of channels
        nb_channels = len(channels)


        # Do resolution analysis
        for chan in channels:

            # get dataframe
            df = self.get_ivsweep_data(chan, tag=tag)

            # check if psd available
            if 'psd' not in df.columns:
                raise ValueError('ERROR: No PSD available! Is it a dIdV only sweep?')

            # get didvanalysis
            if (chan not in self._didv_objects.keys()
                or 'transition' not in self._didv_objects[chan]):
                raise ValueError(
                    f'ERROR: No DIDV analysis done for channel {chan}.'
                    ' Use first "analyze_didv" function')
        
            didv_objs = self._didv_objects[chan]['transition']

            # Initialize data
            resolution_data = dict()
            resolution_data['tes_bias_uA'] = list()
            resolution_data['percent_rn'] = list()
            resolution_data['resolution_dirac'] = list()

            if template is not None:
                resolution_data['resolution_template'] = list()

                            
            for bias, obj in  didv_objs.items():

                # get psd amps2/Hz
                df_bias = df[df.tes_bias_uA==bias]

                if df_bias.empty:
                    raise ValueError(f'ERROR: Unable to get psd for {chan},'
                                     f' tes bias = {bias}')
                psd =  df_bias['psd'].iloc[0]
                fs =   df_bias['fs_noise'].iloc[0]
                percent_rn = df_bias['percent_rn_noise'].iloc[0]
                
                # calculate resolution for direct delta
                resolution_dirac = obj.calc_energy_resolution(
                    chan, psd, fs=fs, template=None,
                    collection_eff=collection_eff)

                resolution_template = None
                if template is not None:
                    resolution_template = obj.calc_energy_resolution(
                        chan, psd, fs=fs, template=template,
                        collection_eff=collection_eff)

                # store
                resolution_data['tes_bias_uA'].append(bias)
                resolution_data['percent_rn'].append(percent_rn)
                resolution_data['resolution_dirac'].append(resolution_dirac)
                if template is not None:
                    resolution_data['resolution_template'].append(resolution_template)

                
            # save
            self._resolution_data[chan] = resolution_data

            # display
            if lgc_plot:

                # Create the plot
                fig, ax1 = plt.subplots(figsize=(8,6))
                
                # Plot the data
                ax1.plot(np.array(resolution_data['percent_rn']),
                         np.array(resolution_data['resolution_dirac'])*1e3, 'b-o',
                         label='Dirac Delta Deposit')
                
                if template is not None:
                    
                    label_ext = 'External Template'
                    if template_name is not None:
                        label_ext = template_name
                        
                    ax1.plot(np.array(resolution_data['percent_rn']),
                             np.array(resolution_data['resolution_template'])*1e3, 'r-o',
                             label=label_ext)

                # axis label
                ax1.set_xlabel('% Rn')
                ax1.set_ylabel('Energy Resolution [meV]')
                ax1.legend()

                # add tes buas
                ax2 = ax1.twiny()
                ax2.xaxis.set_ticks_position('bottom') 
                ax2.xaxis.set_label_position('bottom') 
                ax2.spines['bottom'].set_position(('outward', 36))
                ax2.set_xticks(resolution_data['percent_rn'])
                ax2.set_xticklabels(resolution_data['tes_bias_uA'])
                ax2.set_xlabel('TES bias [uA]')
                         
                # Show the plot
                plt.show()



                
                     
    def _fit_didv(self, data_type,
                  channels=None,
                  percent_rn_max=None, percent_rn_min=None,
                  nb_points_max=None,
                  inf_loop_gain_approx='auto',
                  lgc_plot=False, lgc_save_plot=False,
                  plot_save_path=None,
                  tag='default',
                  **kwargs):
        """
        """
        
        # channels
        available_channels = list()
        ivdata_label = 'ivsweep_data_' + tag
        for chan, chan_dict in self._filter_data.items():
            if ivdata_label in chan_dict.keys():
                available_channels.append(chan)

        if not available_channels:
            raise ValueError('ERROR: No channel available. '
                             'Set data first!')
        if channels is None:
            channels = available_channels
        else:
            if isinstance(channels, str):
                channels = [channels]
            for chan in channels:
                if chan not in available_channels:
                    raise ValueError(f'ERROR: No data for channel {chan}'
                                     ' available. Set data first!')
        # number of channels
        nb_channels = len(channels)


        # check if IV analysis done 
        for chan in channels:
            if chan not in self._ibis_objects.keys():
                raise ValueError(
                    f'ERROR: No IV analysis done for channel {chan}.'
                    ' Use first "analyze_sweep" function')



        # loop channels, fit, and calculate small signal parameters
        for ichan, chan in enumerate(channels):

            if self._verbose:
                if data_type == 'sc':
                    print(f'\n{chan} SC dIdV analysis')
                elif data_type == 'normal':
                    print(f'\n{chan} Normal dIdV analysis')
                else:
                    print(f'\n{chan} Transition dIdV analysis')

            # reset summary
            if chan not in self._didv_summary:
                self._didv_summary[chan] = {}
            self._didv_summary[chan][data_type] = {}
                
                    
            # get dataframe
            df = self.get_ivsweep_data(chan, tag=tag)
            
            # get IV analysis result, rp, rn, rshunt + errors
            iv_type = 'noise'
            if ('rp_noise' not in df.columns):
                iv_type = 'didv'
            iv_result = self.get_ivsweep_result(chan,
                                                iv_type=iv_type,
                                                tag=tag)
            rp_iv = iv_result['rp']
            rp_iv_err = iv_result['rp_err']
            rn_iv =  iv_result['rn']
            rn_iv_err =  iv_result['rn_err']

                                  
            # filter dataframe
            var_name = None
            if 'percent_rn_noise' in df.columns:
                var_name = 'percent_rn_noise'
            elif 'percent_rn_didv' in df.columns:
                var_name = 'percent_rn_didv'
            else:
                raise ValueError(
                    f'ERROR: unable to find "percent_rn" in dataframe'
                    ' Something went wrong with sweep analysis')

            cut = (df[var_name] ==df[var_name])
            if percent_rn_max is not None:
                cut &= (df[var_name] < percent_rn_max)
            if percent_rn_min is not None:
                cut &= (df[var_name] > percent_rn_min)

                                    
            if sum(cut)==0:
                raise ValueError(
                    f'ERROR: Unable to find fit  points for channel {chan}. '
                    f'You may need to change "percent_rn_max" and/or '
                    f'"percent_rn_min"')
            
        
            df_filter = df[cut]
            if (data_type == 'normal' or data_type == 'transition'):
                df_filter = df_filter.sort_values(by='tes_bias_uA',
                                                  key=abs, ascending=False)
            else:
                df_filter = df_filter.sort_values(by='tes_bias_uA',
                                                  key=abs, ascending=True)
                 
            # loop and fit
            rpn_list = []
            dt_list = []
            
            nb_points = len(df_filter)
            if nb_points_max is not None:
                nb_points = min(len(df_filter), nb_points_max)

            
            for ind in range(nb_points):

                pd_series = df_filter.iloc[ind]

                # display case transiton
                if data_type == 'transition':
                    
                    iv_result['r0'] = pd_series['r0_' + iv_type]
                    iv_result['r0_err'] = pd_series['r0_err_' + iv_type]
                    iv_result['i0'] = pd_series['i0_' + iv_type]
                    iv_result['i0_err'] = pd_series['i0_err_' + iv_type]

                    # display
                    if self._verbose:
                        print('\n\n{} TES bias {:.3f} uA, '
                              'R0 = {:.2f} mOhms (% Rn = {:.2f})'.format(
                                  chan,
                                  pd_series['tes_bias_uA'],
                                  iv_result['r0']*1e3,
                                  pd_series[var_name])
                        )

                        print('========================================'
                              '=========================\n')
                                
                # instantiate didv and set data
                didvanalysis = DIDVAnalysis(verbose=False)
                didvanalysis.set_processed_data(chan, pd_series)
                
                # fit
                result = None
                if (data_type == 'normal' or data_type == 'sc'):
                    didvanalysis.dofit(1)
                    result = didvanalysis.get_fit_result(chan,1)
                else:
                    didvanalysis.dofit([2,3])
                    didvanalysis.set_ivsweep_results(chan, iv_result)
                    didvanalysis.calc_smallsignal_params(
                        calc_true_current=False,
                        inf_loop_gain_approx=inf_loop_gain_approx,
                    )
                    if 'psd_freq' in  pd_series:
                        freqs = pd_series['psd_freq']
                        didvanalysis.calc_dpdi(freqs, channels=chan,
                                               list_of_poles=3)
                    result = didvanalysis.get_fit_result(chan,3)
                    
                # store result
                rpn = result['smallsignalparams']['rp']
                if data_type == 'normal':
                    rpn -= rp_iv
                rpn_list.append(rpn)
                dt_list.append(result['smallsignalparams']['dt'])


                # display
                if data_type == 'transition':
                    
                    cov_matrix = np.abs(result['ssp_light']['cov'])
                    vals_vector = result['ssp_light']['vals']
                    sigmas_vector = result['ssp_light']['sigmas']

                    if self._verbose:
                        print('Fit chi2/Ndof = {:.3f}'.format(result['cost']))

                        print('\nFit time constants, NOT dIdV Poles: ')
                        print('Tau1: {:.3g} s'.format(np.abs(result['params']['tau1'])))
                        print('Tau2: {:.3g} s'.format(result['params']['tau2']))
                        print('Tau3: {:.4g} s'.format(result['params']['tau3']))
                        print(' ')

                        print('\nTrue dIdV Poles: ')
                        print('Tau_plus: {:.3g} s'.format(result['falltimes'][0]))
                        print('Tau_minus: {:.3g} s'.format(result['falltimes'][1]))
                        print('Tau_third: {:.4g} s'.format(result['falltimes'][2]))
                        
                        print('\nSmall Signal Parameters:')
                        print('l (loop gain) = {:.3f} +/- {:.4f}'.format(
                            vals_vector['l'], sigmas_vector['sigma_l']))
                        print('beta = {:.3f} +/- {:.4f}'.format(
                            vals_vector['beta'], sigmas_vector['sigma_beta']))
                        print('gratio = {:.3f} +/- {:.4f}'.format(
                            vals_vector['gratio'], sigmas_vector['sigma_gratio']))
                        print('tau0 = {:.3g} +/- {:.4g} ms'.format(
                            vals_vector['tau0']*1e3, sigmas_vector['sigma_tau0']*1e3))
                        print('L = {:.3f} +/- {:.4f} nH'.format(
                            vals_vector['L']*1e9, sigmas_vector['sigma_L']*1e9))

                    if lgc_plot:
                        cor_matrix = np.zeros([5,5])
                        i = 0
                        while i < len(cov_matrix):
                            j = 0
                            while j < len(cov_matrix[i]):
                                cor_matrix[i][j] = cov_matrix[i][j]/(
                                    np.sqrt(cov_matrix[i][i])
                                    * np.sqrt(cov_matrix[j][j]))
                                j += 1
                            i += 1

                        labels = ['beta', 'loopgain', 'L', 'tau0', 'gratio']
                        ticks = np.arange(0, 5, 1)
                        plt.figure(figsize=(6,4))
                        plt.matshow(np.log(cor_matrix), fignum=1)
                        plt.xticks(ticks=ticks, labels=labels)
                        plt.yticks(ticks=ticks, labels=labels)
                        plt.colorbar(label = "log(corr matrix term)")
                        plt.title("Correlation Matrix")
                        plt.show()



                    # store summary
                    if 'Rn %' not in self._didv_summary[chan]['transition']:
                        self._didv_summary[chan]['transition'] = {
                            'Rn %': [], 'tes_bias_uA': [], 'chi2': [],
                            'tau+': [], 'tau-': [], 'tau3': [],
                            'l': [], 'l_err': [], 'beta': [], 'beta_err': [],
                            'gratio': [], 'gratio_err': [], 'tau0': [], 
                            'tau0_err': [], 'L': [], 'L_err': []}
                    else:
                        current = self._didv_summary[chan]['transition']
                        current['Rn %'].append(pd_series[var_name])
                        current['tes_bias_uA'].append(pd_series['tes_bias_uA'])
                        current['chi2'].append(result['cost'])
                        current['tau+'].append(result['falltimes'][0])
                        current['tau-'].append(result['falltimes'][1])
                        current['tau3'].append(result['falltimes'][2])
                        current['l'].append(vals_vector['l'])
                        current['l_err'].append(sigmas_vector['sigma_l'])
                        current['beta'].append(vals_vector['beta'])
                        current['beta_err'].append(sigmas_vector['sigma_beta'])
                        current['gratio'].append(vals_vector['gratio'])
                        current['gratio_err'].append(sigmas_vector['sigma_gratio'])
                        current['tau0'].append(vals_vector['tau0'])
                        current['tau0_err'].append(sigmas_vector['sigma_tau0'])
                        current['L'].append(vals_vector['L'])
                        current['L_err'].append(sigmas_vector['sigma_L'])
                        self._didv_summary[chan]['transition'] = current
                        
                # display
                if lgc_plot:
                    didvanalysis.plot_fit_result(chan)

                    # plot only 1 point for normal / SC
                    if (data_type == 'normal' or data_type == 'sc'):
                        lgc_plot = False

                # save didvobjects
                if chan not in self._didv_objects:
                    self._didv_objects[chan] = dict()
                if data_type not in self._didv_objects[chan]:
                    self._didv_objects[chan][data_type] = dict()
                self._didv_objects[chan][data_type][pd_series['tes_bias_uA']] = didvanalysis
                    
                        
            # mean/std of rn/rp 
            rpn_didv_fit = np.mean(rpn_list)
            rpn_err_didv_fit = np.std(rpn_list)
            dt_didv_fit = np.mean(dt_list)
            dt_err_didv_fit = np.std(dt_list)

            # store summay
            if data_type == 'normal':
                self._didv_summary[chan]['normal'] = {
                    'rn':rpn_didv_fit,
                    'rn_err': rpn_err_didv_fit,
                    'rn_iv': rn_iv,
                    'rn_iv_err': rn_iv_err}
                
            elif data_type == 'sc':
                self._didv_summary[chan]['sc'] = {
                    'rp':rpn_didv_fit,
                    'rp_err': rpn_err_didv_fit,
                    'rp_iv': rn_iv,
                    'rp_iv_err': rn_iv_err}
                
            if self._verbose:
                
                if data_type == 'normal':
                    print('{} Rn from dIdV fit = {:.2f} +/- {:.3f} mOhms'.format(
                        chan, rpn_didv_fit*1e3,rpn_err_didv_fit*1e3))
                    print('{} Rn from IV Sweep = {:.2f} +/- {:.3f} mOhms'.format(
                        chan, rn_iv*1e3, rn_iv_err*1e3))
                elif data_type == 'sc':
                    print('{} Rp from dIdV fit = {:.2f} +/- {:.3f} mOhms'.format(
                        chan, rpn_didv_fit*1e3,rpn_err_didv_fit*1e3))
                    print('{} Rp from IV Sweep = {:.2f} +/- {:.3f} mOhms'.format(
                        chan, rp_iv*1e3, rp_iv_err*1e3))
