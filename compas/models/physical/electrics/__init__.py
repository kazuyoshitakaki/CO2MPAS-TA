#-*- coding: utf-8 -*-
#
# Copyright 2015 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It provides a final drive model.

The model is defined by a Dispatcher that wraps all the functions needed.
"""

__author__ = 'Vincenzo_Arcidiacono'

from compas.dispatcher import Dispatcher
from compas.functions.physical.electrics import *


def electrics():
    """
    Define the electrics model.

    .. dispatcher:: dsp

        >>> dsp = electrics()

    :return:
        The electrics model.
    :rtype: Dispatcher
    """

    electrics = Dispatcher(
        name='Electrics',
        description='Models the vehicle electrics.'
    )

    electrics.add_function(
        function=identify_electric_loads,
        inputs=['alternator_nominal_voltage', 'battery_currents',
                'gear_box_powers_in', 'times', 'on_engine', 'engine_starts'],
        outputs=['electric_load', 'start_demand']
    )

    electrics.add_function(
        function=calculate_state_of_charges,
        inputs=['battery_capacity', 'times', 'initial_state_of_charge',
                'battery_currents'],
        outputs=['state_of_charges']
    )

    electrics.add_function(
        function=identify_charging_statuses,
        inputs=['battery_currents', 'gear_box_powers_in', 'on_engine'],
        outputs=['alternator_statuses']
    )

    electrics.add_function(
        function=calculate_alternator_powers_demand,
        inputs=['alternator_nominal_voltage', 'alternator_currents',
                'alternator_efficiency', 'max_charging_current'],
        outputs=['alternator_powers_demand']
    )

    electrics.add_function(
        function=calibrate_alternator_status_model,
        inputs=['alternator_statuses', 'state_of_charges',
                'gear_box_powers_in'],
        outputs=['alternator_status_model']
    )

    electrics.add_function(
        function=identify_max_charging_current,
        inputs=['battery_currents', 'electric_load'],
        outputs=['max_charging_current']
    )

    electrics.add_function(
        function=predict_vehicle_electrics,
        inputs=['battery_capacity', 'alternator_status_model',
                'max_charging_current', 'alternator_nominal_voltage',
                'start_demand', 'electric_load', 'initial_state_of_charge',
                'times', 'gear_box_powers_in', 'on_engine', 'engine_starts'],
        outputs=['alternator_currents', 'battery_currents',
                 'state_of_charges', 'alternator_statuses']
    )

    return electrics
