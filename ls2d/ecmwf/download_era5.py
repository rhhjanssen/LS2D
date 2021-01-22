#
# This file is part of LS2D.
#
# Copyright (c) 2017-2021 Wageningen University & Research
# Author: Bart van Stratum (WUR)
#
# LS2D is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# LS2D is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with LS2D.  If not, see <http://www.gnu.org/licenses/>.
#

# Python modules
import multiprocessing as mp
from multiprocessing import set_start_method
import subprocess as sp
import datetime
import sys,os

# Third party modules
import numpy as np

# LS2D modules
import ls2d.ecmwf.era_tools as era_tools
from ls2d.src.messages import *

# Yikes, but necessary (?) if you want to use
# MARS downloads without the Python CDS api installed?
try:
    import cdsapi
except ImportError:
    cdsapi = None

set_start_method('fork')

def _retrieve_from_MARS(request, settings, nc_dir, nc_file, qos):
    """
    Retrieve file from MARS
    """

    def execute(task):
        sp.call(task, shell=True, executable='/bin/bash')

    clean_name = nc_file[:-3]
    mars_req   = '{}.mars' .format(clean_name)
    grib_file  = '{}.grib' .format(clean_name)
    slurm_job  = '{}.slurm'.format(clean_name)

    # Wall clock limit
    wc_lim = '03:00:00' if qos == 'express' else '06:00:00'

    # Create MARS request
    f = open(mars_req, 'w')
    f.write('retrieve,\n')
    for key, value in request.items():
        f.write('{}={},\n'.format(key,value))
    f.write('target=\"{}\"\n'.format(grib_file))
    f.close()

    # Create SLURM job file
    date  = settings['date']
    ftype = settings['ftype'].split('_')
    jobname = '{0:04d}{1:02d}{2:02d}{3:}{4:}'.format(date.year, date.month, date.day, ftype[1], ftype[0])

    f = open(slurm_job, 'w')
    f.write('#!/bin/ksh\n')
    f.write('#SBATCH --qos={}\n'.format(qos))
    f.write('#SBATCH --job-name={}\n'.format(jobname))
    f.write('#SBATCH --output={}.%N.%j.out\n'.format(slurm_job))
    f.write('#SBATCH --error={}.%N.%j.err\n'.format(slurm_job))
    f.write('#SBATCH --workdir={}\n'.format(nc_dir))
    f.write('#SBATCH --time={}\n\n'.format(wc_lim))

    f.write('mars {}\n'.format(mars_req))
    f.write('grib_to_netcdf -o {} {}'.format(nc_file, grib_file))
    f.close()

    # Submit job
    execute('sbatch {}'.format(slurm_job))


def _download_era5_file(settings):
    """
    Download ERA5 analysis or forecasts on surface, model or pressure levels
    Requested parameters are hardcoded and chosen for the specific use of LS2D

    Arguments:
        settings : dictionary
            Dictionary with keys:
                date : datetime object with date to download
                lat, lon : requested latitude and longitude
                size : download an area of lat+/-size, lon+/-size (degrees)
                path : absolute or relative path to save the NetCDF data
                case : case name used in file name of NetCDF files
                ftype : level/forecast/analysis switch (in: [model_an, model_fc, pressure_an, surface_an])
    """

    message('Downloading: {} - {}'.format(settings['date'], settings['ftype']))

    # Output file name
    nc_dir, nc_file = era_tools.era5_file_path(
            settings['date'].year, settings['date'].month, settings['date'].day,
            settings['era5_path'], settings['case_name'], settings['ftype'])

    # Write CDS API prints to log file (NetCDF file path/name appended with .out/.err)
    if settings['write_log']:
        out_file   = '{}.out'.format(nc_file)
        err_file   = '{}.err'.format(nc_file)
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = open(out_file, 'w')
        sys.stderr = open(err_file, 'w')

    # Bounds of domain
    lat_n = settings['central_lat']+settings['area_size']
    lat_s = settings['central_lat']-settings['area_size']
    lon_w = settings['central_lon']-settings['area_size']
    lon_e = settings['central_lon']+settings['area_size']

    # Monitor the required download time
    start = datetime.datetime.now()

    # Switch between CDS and MARS downloads
    if settings['data_source'] == 'CDS':

        if cdsapi is None:
            error('Can not import Python CDS API!')

        # Create instance of CDS API
        server = cdsapi.Client()

        # Surface and pressure level analysis, stored on HDs, so downloads are fast :-)
        if settings['ftype'] == 'pressure_an' or settings['ftype'] == 'surface_an':

            analysis_times = ['{0:02d}:00'.format(i) for i in range(24)]
            area = [lat_n, lon_w, lat_s, lon_e]

            request = {
                'product_type': 'reanalysis',
                'format': 'netcdf',
                'year': '{0:04d}'.format(settings['date'].year),
                'month': '{0:02d}'.format(settings['date'].month),
                'day': '{0:02d}'.format(settings['date'].day),
                'time': analysis_times,
                'area': area,
            }

            if settings['ftype'] == 'pressure_an':
                pressure_levels = [
                    '1', '2', '3', '5', '7', '10', '20', '30', '50', '70', '100', '125', '150', '175', '200',
                    '225', '250', '300', '350', '400', '450', '500', '550', '600', '650', '700', '750',
                    '775', '800', '825', '850', '875', '900', '925', '950', '975', '1000']

                request.update({
                    'pressure_level': pressure_levels,
                    'variable': 'geopotential'})

                server.retrieve('reanalysis-era5-pressure-levels', request, nc_file)

            elif settings['ftype'] == 'surface_an':
                request.update({
                    'variable': [
                        'instantaneous_moisture_flux', 'high_vegetation_cover', 'leaf_area_index_high_vegetation',
                        'leaf_area_index_low_vegetation', 'low_vegetation_cover', 'sea_surface_temperature',
                        'skin_temperature', 'soil_temperature_level_1', 'soil_temperature_level_2',
                        'soil_temperature_level_3', 'soil_temperature_level_4', 'soil_type',
                        'surface_pressure', 'instantaneous_surface_sensible_heat_flux', 'type_of_high_vegetation',
                        'type_of_low_vegetation', 'volumetric_soil_water_layer_1', 'volumetric_soil_water_layer_2',
                        'volumetric_soil_water_layer_3', 'volumetric_soil_water_layer_4',
                        'forecast_logarithm_of_surface_roughness_for_heat', 'forecast_surface_roughness']})

                server.retrieve('reanalysis-era5-single-levels', request, nc_file)

        # Model level analysis, stored in tape archive, so downloads are VERY slow :-(
        elif settings['ftype'] == 'model_an':

            model_levels = '/'.join(list(np.arange(1,138).astype(str)))
            analysis_times = '/'.join(['{0:02d}:00:00'.format(i) for i in range(24)])

            request = {
                'class': 'ea',
                'date': '{0:04d}-{1:02d}-{2:02d}'.format(
                    settings['date'].year, settings['date'].month, settings['date'].day),
                'expver': '1',
                'levelist': model_levels,
                'levtype': 'ml',
                'param': '75/76/130/131/132/133/135/203/246/247',
                'stream': 'oper',
                'time': analysis_times,
                'type': 'an',
                'area': '{}/{}/{}/{}'.format(lat_n, lon_w, lat_s, lon_e),
                'grid': '0.25/0.25',
                'format': 'netcdf'}

            server.retrieve('reanalysis-era5-complete', request, nc_file)


    elif settings['data_source'] == 'MARS':

        # Shared set of CDS Python API settings for all download types:
        request = {
            'class'   : 'ea',
            'expver'  : '{}'.format(settings['era5_expver']),
            'stream'  : 'oper',
            'date'    : '{0:04d}-{1:02d}-{2:02d}'.format(
                settings['date'].year, settings['date'].month, settings['date'].day),
            'area'    : '{}/{}/{}/{}'.format(lat_n, lon_w, lat_s, lon_e),
            'grid'    : '0.25/0.25',
            'format'  : 'netcdf',
        }

        # Model levels and time steps to retrieve
        model_levels = '1/to/137/by/1'
        press_levels = '1/2/3/5/7/10/20/30/50/70/100/125/150/175/200/225/250/300/350/400/450/\
    500/550/600/650/700/750/775/800/825/850/875/900/925/950/975/1000'

        an_times = '0/to/23/by/1'

        # Update request based on level/analysis/forecast:
        if settings['ftype'] == 'model_an':
            qos = 'normal'
            request.update({
                'levtype'  : 'ml',
                'type'     : 'an',
                'levelist' : model_levels,
                'time'     : an_times,
                'param'    : '75/76/129/130/131/132/133/135/152/246/247/248/203'
            })

        elif settings['ftype'] == 'pressure_an':
            qos = 'normal'
            request.update({
                'levtype'  : 'pl',
                'type'     : 'an',
                'levelist' : press_levels,
                'time'     : an_times,
                'param'    : '129.128'
            })

        elif settings['ftype'] == 'surface_an':
            qos = 'normal'
            request.update({
                'levtype'  : 'sfc',
                'type'     : 'an',
                'time'     : an_times,
                'param'    : '15.128/16.128/17.128/18.128/27.128/28.128/29.128/30.128/34.128/35.128/36.128/37.128/38.128/39.128/40.128/41.128/42.128/43.128/66.128/67.128/74.128/78.128/79.128/89.228/90.228/129.128/134.128/136.128/137.128/139.128/151.128/160.128/161.128/162.128/163.128/164.128/165.128/166.128/167.128/168.128/170.128/172.128/183.128/186.128/187.128/188.128/198.128/229.128/230.128/231.128/232.128/235.128/236.128/243.128/244.128/245.128'
            })

        # Submit download to SLURM:
        _retrieve_from_MARS(request, settings, nc_dir, nc_file, qos)

    # Restore printing to screen
    if settings['write_log']:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    message('Finished: {} - {} in {}'.format(settings['date'], settings['ftype'], datetime.datetime.now()-start))


def download_era5(settings):
    """
    Download all required ERA5 fields for an experiment
    between `starttime` and `endtime`

    Analysis and forecasts are downloaded as 24 hour blocks:
        Analysis: 00 UTC to (including) 23 UTC
        Forecast: 06 UTC to (including) 05 UTC next day

    Arguments:
        start : datetime object
            Start date+time of experiment
        end : datetime object
            End date+time of experiment
        lat, lon : float
            Requested center latitude and longitude
        size : float
            Download an area of lat+/-size, lon+/-size degrees
        path : string
            Directory to save files
        case : string
            Case name used in file name of NetCDF files
    """

    header('Downloading ERA5 for period: {} to {}'.format(settings['start_date'], settings['end_date']))

    # Check if output directory exists, and ends with '/'
    if not os.path.isdir(settings['era5_path']):
        error('Output directory \"{}\" does not exist!'.format(settings['era5_path']))
    if settings['era5_path'][-1] != '/':
        settings['era5_path'] += '/'

    # Round date/time to full hours
    start = era_tools.lower_to_hour(settings['start_date'])
    end   = era_tools.lower_to_hour(settings['end_date']  )

    # Get list of required forecast and analysis times
    an_dates = era_tools.get_required_analysis(start, end)
    fc_dates = era_tools.get_required_forecast(start, end)

    # Base dictionary to pass to download function. In Python >3.3, multiprocessings Pool() can accept
    # multiple arguments. For now, keep it generic for older versions by passing all arguments inside a dict.
    download_settings = settings.copy()
    download_queue = []

    # Loop over all required files, check if there is a local version, if not add to download queue
    # Analysis files:
    for date in an_dates:
        for ftype in ['model_an', 'pressure_an', 'surface_an']:

            era_dir, era_file = era_tools.era5_file_path(
                    date.year, date.month, date.day, settings['era5_path'], settings['case_name'], ftype)

            if not os.path.exists(era_dir):
                message('Creating output directory {}'.format(era_dir))
                os.makedirs(era_dir)

            if os.path.isfile(era_file):
                message('Found {} - {} local'.format(date, ftype))
            else:
                settings_tmp = download_settings.copy()
                settings_tmp.update({'date': date, 'ftype':ftype})
                download_queue.append(settings_tmp)

    if settings['data_source'] == 'CDS' and settings['ntasks'] > 1:
        # Create download Pool:
        pool = mp.Pool(processes=settings['ntasks'])
        pool.map(_download_era5_file, download_queue)
    else:
        # Simple serial retrieve:
        for req in download_queue:
            _download_era5_file(req)