# -*- coding: utf-8 -*-
#
# Copyright 2015 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It contains functions to make a simulation plan.
"""
from tqdm import tqdm
import co2mpas.dispatcher.utils as dsp_utl
import co2mpas.utils as co2_utl
from .io import check_cache_fpath_exists, get_cache_fpath
from .io.dill import save_dill, load_from_dill
from .__main__ import file_finder
from .batch import _process_vehicle, _add2summary, vehicle_processing_model
from .model.physical.clutch_tc.torque_converter import TorqueConverter
from cachetools import cached, LRUCache


@cached(LRUCache(maxsize=256))
def get_results(model, fpath, overwrite_cache=False, **kw):
    cache_fpath = get_cache_fpath(fpath, ext=('res', 'job', 'dill',))

    if check_cache_fpath_exists(overwrite_cache, fpath, cache_fpath):
        res = load_from_dill(cache_fpath)
    else:
        kw = {k: v for k, v in kw.items() if k != 'plot_workflow'}
        res = _process_vehicle(model,
                               input_file_name=fpath,
                               overwrite_cache=overwrite_cache, **kw)
        save_dill(res, cache_fpath)

    return res


def build_default_models(model, paths, output_folder, **kw):
    dfl = {}
    paths = eval(paths or '()')
    for path in file_finder(paths):
        res = get_results(model, path, output_folder, **kw)
        out = res['dsp_model'].data_output.get('data.prediction.models', {})
        if 'torque_converter_model' in out:
            out['torque_converter_model'] = TorqueConverter()
        dfl.update(out)

    return dfl


def define_new_inputs(data, base, dsp_model):
    remove = []
    for k, v in co2_utl.stack_nested_keys(data, depth=2):
        if v is dsp_utl.EMPTY:
            remove.append(k)

    dsp = dsp_model.get_sub_dsp_from_workflow(data, check_inputs=False)
    n = set(base) - set(dsp.data_nodes)
    n.update(data)

    inp = dsp_utl.selector(n, base, allow_miss=True)
    d = co2_utl.combine_nested_dicts(inp, data, depth=2)

    for n, k in remove:
        co2_utl.get_nested_dicts(d, n).pop(k)

    return d


def make_simulation_plan(plan, timestamp, output_folder, main_flags):
    model, summary = vehicle_processing_model(), {}

    run_modes = tuple(model.get_sub_dsp_from_workflow(
        ('validated_data', 'vehicle_name'), check_inputs=False, graph=model.dmap
    ).data_nodes) + ('start_time', 'vehicle_name')

    kw = {
        'output_folder': output_folder,
        'plan': False,
        'timestamp': timestamp,
    }

    kw = dsp_utl.combine_dicts(main_flags, kw)
    for (i, base_fpath, defaults_fpats), p in tqdm(plan, disable=False):
        base = get_results(model, base_fpath, **kw)
        name = '%s:%d' % (base.get('vehicle_name', 'vehicle'), i)

        inputs = dsp_utl.selector(set(base).difference(run_modes), base)
        inputs['vehicle_name'] = name
        dsp_model = base['dsp_model']
        outputs = dsp_model.data_output

        dfl = build_default_models(model, defaults_fpats, **kw)
        if dfl:
            dfl = {'data.prediction.models': dfl}
            outputs = co2_utl.combine_nested_dicts(dfl, outputs, depth=2)

        inputs['validated_data'] = define_new_inputs(p, outputs, dsp_model)
        inputs.update(kw)
        res = _process_vehicle(model, **inputs)

        s = filter_summary(p, res.get('summary', {}))
        base_keys = {
            'vehicle_name': (defaults_fpats, base_fpath, name),
        }
        _add2summary(summary, s, base_keys)

    return summary


def filter_summary(changes, summary):
    l, variations = [], {}
    for k, v in changes.items():
        k = tuple(k.split('.')[:0:-1])
        l.append(k)
        k = k[0], 'plan %s' % k[1]
        co2_utl.get_nested_dicts(variations, *k).update(v)

    s = {}
    d = ('delta', 'co2_emission')
    if ('nedc', 'prediction') in l:
        l.append(('delta', 'co2_emission'))
    else:
        delta = summary.get('delta', {}).get('co2_emission', {})

        if delta:
            keys = ['nedc_%s_delta' % k
                    for k in ('wltp_h', 'wltp_l')
                    if (k, 'prediction') in l]
            if keys:
                keys += ['vehicle_name']
                d = co2_utl.get_nested_dicts(s, *d)
                d.update(dsp_utl.selector(keys, delta))

    for k, v in co2_utl.stack_nested_keys(summary, depth=2):
        if k in l:
            co2_utl.get_nested_dicts(s, *k[:-1])[k[-1]] = v

    return co2_utl.combine_nested_dicts(s, variations, depth=2)