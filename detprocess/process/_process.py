import yaml
import warnings
from pathlib import Path
import numpy as np
import pandas as pd

from detprocess.io._load import load_traces
from detprocess.io._save import save_features
from detprocess.process._features import repack_h5info_dict, SingleChannelExtractors


__all__ = [
    'process_data',
]


def _get_single_channel_feature_names(chan_dict):
    """Helper function for getting feature extractors."""
    feature_list = []
    for feature in chan_dict:
        if isinstance(chan_dict[feature], dict) and chan_dict[feature]['run']:
            feature_list.append(feature)
    return feature_list


def process_data(raw_file, path_to_yaml, nevents=0, savepath=None):
    """
    Function for extracting features from a data file using the settings from a specified YAML file.

    Parameters
    ----------
    raw_file : str
        Full path and file name to the HDF5 file to be processed. Assumed to have been created by `pytesdaq`.
    path_to_yaml : str
        Full path and file name to the YAML settings for the processing.
    nevents : int
        The number of events to process in the file. Default of 0 is to process all events. Generally used for development purposes.
    savepath : str, NoneType
        The path to the folder to save the extracted features to (as an HDF5 file). If left as None, then the data will not be saved anywhere, and a warning will be shown specifying this.

    Returns
    -------
    feature_df : Pandas.DataFrame
        A DataFrame containing all of the extracted features for the given file.

    """

    if savepath is None:
        warnings.warn('savepath has not been set, the extracted features will be returned, but not saved to a file.')

    with open(path_to_yaml) as f:
        yaml_dict = yaml.safe_load(f)

    feature_df = pd.DataFrame()

    for chan in yaml_dict:
        traces, info_dict = load_traces(
            raw_file, channels=[chan], nevents=nevents,
        )
        fs = info_dict[0]['sample_rate']
        chan_dict = yaml_dict[chan]
        template = np.loadtxt(chan_dict['template_path'])
        psd = np.loadtxt(chan_dict['psd_path'])
        feature_list = _get_single_channel_feature_names(chan_dict)
        feature_dict = {}

        for ii, trace in enumerate(traces[:, 0]):
            for feature in feature_list:
                kwargs = {key: value for (key, value) in chan_dict[feature].items() if key!='run'}
                kwargs['template'] = template
                kwargs['psd'] = psd
                kwargs['fs'] = fs
                extractor = getattr(SingleChannelExtractors, feature)
                extracted_dict = extractor(trace, **kwargs)
                for ex_feature in extracted_dict:
                    ex_feature_name = f'{ex_feature}_{chan}'
                    if ex_feature_name not in feature_dict:
                        feature_dict[ex_feature_name] = np.zeros(len(traces))
                    feature_dict[ex_feature_name][ii] = extracted_dict[ex_feature]

        for feature in feature_dict:
            feature_df[feature] = feature_dict[feature]

    info_dict_repacked = repack_h5info_dict(info_dict)

    for info in info_dict_repacked:
        feature_df[info] = info_dict_repacked[info]

    if savepath is not None:
        save_features(
            feature_df,
            f'{savepath}/detprocess_{Path(raw_file).stem}.hdf5',
        )

    return feature_df


