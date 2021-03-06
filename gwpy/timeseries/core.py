# -*- coding: utf-8 -*-
# Copyright (C) Duncan Macleod (2013)
#
# This file is part of GWpy.
#
# GWpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GWpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GWpy.  If not, see <http://www.gnu.org/licenses/>.

"""
The TimeSeriesBase
==================

This module defines the following classes

--------------------  ---------------------------------------------------------
`TimeSeriesBase`      base of the `TimeSeries` and `StateVector` classes,
                      provides the constructor and all common methods
                      (mainly everything that isn't signal-processing related)
`TimeSeriesBaseDict`  base of the `TimeSeriesDict`, this exists mainly so that
                      the `TimeSeriesDict` and `StateVectorDict` can be
                      distinct objects
`TimeSeriesBaseList`  base of the `TimeSeriesList` and `StateVectorList`,
                      same reason for living as the `TimeSeriesBaseDict`
--------------------  ---------------------------------------------------------

**None of these objects are really designed to be used other than as bases for
user-facing objects.**
"""

from __future__ import (division, print_function)

import os
import sys
import warnings
from math import ceil

import numpy

from astropy import units
from astropy import __version__ as astropy_version

from ..types import (Array2D, Series)
from ..detector import (Channel, ChannelList)
from ..io import datafind
from ..time import (Time, LIGOTimeGPS, to_gps)
from ..utils import gprint
from ..utils.compat import OrderedDict

__author__ = 'Duncan Macleod <duncan.macleod@ligo.org>'

__all__ = ['TimeSeriesBase', 'ArrayTimeSeries', 'TimeSeriesBaseDict']

ASTROPY_2_0 = astropy_version >= '2.0'

_UFUNC_STRING = {'less': '<',
                 'less_equal': '<=',
                 'equal': '==',
                 'greater_equal': '>=',
                 'greater': '>',
                 }


def _format_time(t):
    if isinstance(t, LIGOTimeGPS):
        return float(t)
    if isinstance(t, Time):
        return t.gps
    return t


class TimeSeriesBase(Series):
    """An `Array` with time-domain metadata.

    Parameters
    ----------
    value : array-like
        input data array

    unit : `~astropy.units.Unit`, optional
        physical unit of these data

    t0 : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
        GPS epoch associated with these data,
        any input parsable by `~gwpy.time.to_gps` is fine

    dt : `float`, `~astropy.units.Quantity`, optional, default: `1`
        time between successive samples (seconds), can also be given inversely
        via `sample_rate`

    sample_rate : `float`, `~astropy.units.Quantity`, optional, default: `1`
        the rate of samples per second (Hertz), can also be given inversely
        via `dt`

    times : `array-like`
        the complete array of GPS times accompanying the data for this series.
        This argument takes precedence over `t0` and `dt` so should be given
        in place of these if relevant, not alongside

    name : `str`, optional
        descriptive title for this array

    channel : `~gwpy.detector.Channel`, `str`, optional
        source data stream for these data

    dtype : `~numpy.dtype`, optional
        input data type

    copy : `bool`, optional, default: `False`
        choose to copy the input data to new memory

    subok : `bool`, optional, default: `True`
        allow passing of sub-classes by the array generator
    """
    _default_xunit = units.second
    _print_slots = ['t0', 'dt', 'name', 'channel']
    DictClass = None

    def __new__(cls, data, unit=None, t0=None, dt=None, sample_rate=None,
                times=None, channel=None, name=None, **kwargs):
        """Generate a new `TimeSeriesBase`.
        """
        # parse t0 or epoch
        epoch = kwargs.pop('epoch', None)
        if epoch is not None and t0 is not None:
            raise ValueError("give only one of epoch or t0")
        if epoch is None and t0 is not None:
            kwargs['x0'] = _format_time(t0)
        elif epoch is not None:
            kwargs['x0'] = _format_time(epoch)
        # parse sample_rate or dt
        if sample_rate is not None and dt is not None:
            raise ValueError("give only one of sample_rate or dt")
        if sample_rate is None and dt is not None:
            kwargs['dx'] = dt
        # parse times
        if times is not None:
            kwargs['xindex'] = times

        # generate TimeSeries
        new = super(TimeSeriesBase, cls).__new__(cls, data, name=name,
                                                 unit=unit, channel=channel,
                                                 **kwargs)

        # manually set sample_rate if given
        if sample_rate is not None:
            new.sample_rate = sample_rate

        return new

    # -- TimeSeries properties ------------------

    # rename properties from the Series
    t0 = Series.x0
    dt = Series.dx
    span = Series.xspan
    times = Series.xindex

    # -- epoch
    # this gets redefined to attach to the t0 property
    @property
    def epoch(self):
        """GPS epoch for these data.

        This attribute is stored internally by the `t0` attribute

        :type: `~astropy.time.Time`
        """
        try:
            return Time(self.t0, format='gps', scale='utc')
        except AttributeError:
            return None

    @epoch.setter
    def epoch(self, epoch):
        if epoch is None:
            del self.t0
        elif isinstance(epoch, Time):
            self.t0 = epoch.gps
        else:
            try:
                self.t0 = to_gps(epoch)
            except TypeError:
                self.t0 = epoch

    # -- sample_rate
    @property
    def sample_rate(self):
        """Data rate for this `TimeSeries` in samples per second (Hertz).

        This attribute is stored internally by the `dx` attribute

        :type: `~astropy.units.Quantity` scalar
        """
        return (1 / self.dt).to('Hertz')

    @sample_rate.setter
    def sample_rate(self, val):
        if val is None:
            del self.dt
            return
        self.dt = (1 / units.Quantity(val, units.Hertz)).to(self.xunit)
        if numpy.isclose(self.dt.value, round(self.dt.value)):
            self.dt = units.Quantity(round(self.dt.value), self.dt.unit)

    # -- duration
    @property
    def duration(self):
        """Duration of this series in seconds

        :type: `~astropy.units.Quantity` scalar
        """
        return units.Quantity(self.span[1] - self.span[0], self.xunit)

    # -- TimeSeries accessors -------------------

    @classmethod
    def fetch(cls, channel, start, end, host=None, port=None, verbose=False,
              connection=None, verify=False, pad=None, allow_tape=None,
              type=None, dtype=None):
        """Fetch data from NDS

        Parameters
        ----------
        channel : `str`, `~gwpy.detector.Channel`
            the data channel for which to query

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS end time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        host : `str`, optional
            URL of NDS server to use, if blank will try any server
            (in a relatively sensible order) to get the data

        port : `int`, optional
            port number for NDS server query, must be given with `host`

        verify : `bool`, optional, default: `False`
            check channels exist in database before asking for data

        connection : `nds2.connection`, optional
            open NDS connection to use

        verbose : `bool`, optional
            print verbose output about NDS progress, useful for debugging

        type : `int`, optional
            NDS2 channel type integer

        dtype : `type`, `numpy.dtype`, `str`, optional
            identifier for desired output data type
        """
        return cls.DictClass.fetch(
            [channel], start, end, host=host, port=port, verbose=verbose,
            connection=connection, verify=verify, pad=pad,
            allow_tape=allow_tape, type=type, dtype=dtype)[str(channel)]

    @classmethod
    def fetch_open_data(cls, ifo, start, end, sample_rate=4096,
                        format=None, host='https://losc.ligo.org',
                        verbose=False, **kwargs):
        """Fetch open-access data from the LIGO Open Science Center

        Parameters
        ----------
        ifo : `str`
            the two-character prefix of the IFO in which you are interested,
            e.g. `'L1'`

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
            GPS start time of required data, defaults to start of data found;
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
            GPS end time of required data, defaults to end of data found;
            any input parseable by `~gwpy.time.to_gps` is fine

        sample_rate : `float`, optional, default: `4096`
            the sample rate of desired data. Most data are stored
            by LOSC at 4096 Hz, however there may be event-related
            data releases with a 16384 Hz rate

        format : `str`, optional
            the data format to download and parse, defaults to 'txt.gz'
            which requires no extra packages. Other options include

            - ``'hdf5'`` - requires |h5py|_
            - ``'gwf'`` - requires |LDAStools.frameCPP|_

        verbose : `bool`, optional, default: `False`
            print verbose output while fetching data

        host : `str`, optional
            HTTP host name of LOSC server to access

        **kwargs
            any other keyword arguments are passed to the `TimeSeries.read`
            method that parses the file that was downloaded
        """
        from .io.losc import fetch_losc_data
        return fetch_losc_data(ifo, start, end, cls=cls,
                               sample_rate=sample_rate, format=format,
                               host=host, verbose=verbose, **kwargs)

    @classmethod
    def find(cls, channel, start, end, frametype=None,
             pad=None, dtype=None, nproc=1, verbose=False, **readargs):
        """Find and read data from frames for a channel

        Parameters
        ----------
        channel : `str`, `~gwpy.detector.Channel`
            the name of the channel to read, or a `Channel` object.

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS end time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        frametype : `str`, optional
            name of frametype in which this channel is stored, will search
            for containing frame types if necessary

        pad : `float`, optional
            value with which to fill gaps in the source data, only used if
            gap is not given, or `gap='pad'` is given

        nproc : `int`, optional, default: `1`
            number of parallel processes to use, serial process by
            default.

        dtype : `numpy.dtype`, `str`, `type`, or `dict`
            numeric data type for returned data, e.g. `numpy.float`, or
            `dict` of (`channel`, `dtype`) pairs

        allow_tape : `bool`, optional, default: `True`
            allow reading from frame files on (slow) magnetic tape

        verbose : `bool`, optional
            print verbose output about NDS progress.

        **readargs
            any other keyword arguments to be passed to `.read()`
        """
        return cls.DictClass.find(
            [channel], start, end, frametype=frametype, verbose=verbose,
            pad=pad, dtype=dtype, nproc=nproc, **readargs)[str(channel)]

    @classmethod
    def get(cls, channel, start, end, pad=None, dtype=None, verbose=False,
            allow_tape=None, **kwargs):
        """Get data for this channel from frames or NDS

        This method dynamically accesses either frames on disk, or a
        remote NDS2 server to find and return data for the given interval

        Parameters
        ----------
        channel : `str`, `~gwpy.detector.Channel`
            the name of the channel to read, or a `Channel` object.

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS end time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        pad : `float`, optional
            value with which to fill gaps in the source data, default to
            'don't fill gaps'

        dtype : `numpy.dtype`, `str`, `type`, or `dict`
            numeric data type for returned data, e.g. `numpy.float`, or
            `dict` of (`channel`, `dtype`) pairs

        nproc : `int`, optional, default: `1`
            number of parallel processes to use, serial process by
            default.

        allow_tape : `bool`, optional, default: `None`
            allow the use of frames that are held on tape, default is `None`
            to attempt to allow the `TimeSeries.fetch` method to
            intelligently select a server that doesn't use tapes for
            data storage (doesn't always work), but to eventually allow
            retrieving data from tape if required

        verbose : `bool`, optional
            print verbose output about NDS progress.

        **kwargs
            other keyword arguments to pass to either
            :meth:`.find` (for direct GWF file access) or
            :meth:`.fetch` for remote NDS2 access

        See Also
        --------
        TimeSeries.fetch
            for grabbing data from a remote NDS2 server
        TimeSeries.find
            for discovering and reading data from local GWF files
        """
        return cls.DictClass.get(
            [channel], start, end, pad=pad, dtype=dtype, verbose=verbose,
            **kwargs)[str(channel)]

    # -- utilities ------------------------------

    def plot(self, **kwargs):
        """Plot the data for this `TimeSeries`
        """
        from ..plotter import TimeSeriesPlot
        return TimeSeriesPlot(self, **kwargs)

    @classmethod
    def from_nds2_buffer(cls, buffer_, **metadata):
        """Construct a new `TimeSeries` from an `nds2.buffer` object

        Parameters
        ----------
        buffer_ : `nds2.buffer`
            the input NDS2-client buffer to read
        **metadata
            any other metadata keyword arguments to pass to the `TimeSeries`
            constructor

        Returns
        -------
        timeseries : `TimeSeries`
            a new `TimeSeries` containing the data from the `nds2.buffer`,
            and the appropriate metadata

        Notes
        -----
        This classmethod requires the nds2-client package
        """
        # cast as TimeSeries and return
        channel = Channel.from_nds2(buffer_.channel)
        metadata.setdefault('channel', channel)
        metadata.setdefault('epoch', LIGOTimeGPS(buffer_.gps_seconds,
                                                 buffer_.gps_nanoseconds))
        metadata.setdefault('sample_rate', channel.sample_rate)
        metadata.setdefault('unit', channel.unit)
        metadata.setdefault('name', str(channel))
        return cls(buffer_.data, **metadata)

    @classmethod
    def from_lal(cls, lalts, copy=True):
        """Generate a new TimeSeries from a LAL TimeSeries of any type.
        """
        from ..utils.lal import from_lal_unit
        try:
            unit = from_lal_unit(lalts.sampleUnits)
        except (TypeError, ValueError) as e:
            warnings.warn("%s, defaulting to 'dimensionless'" % str(e))
            unit = None
        channel = Channel(lalts.name, sample_rate=1/lalts.deltaT, unit=unit,
                          dtype=lalts.data.data.dtype)
        out = cls(lalts.data.data, channel=channel, t0=float(lalts.epoch),
                  dt=lalts.deltaT, unit=unit, name=lalts.name, copy=False)
        if copy:
            return out.copy()
        else:
            return out

    def to_lal(self):
        """Convert this `TimeSeries` into a LAL TimeSeries.
        """
        import lal
        from ..utils.lal import (LAL_TYPE_STR_FROM_NUMPY, to_lal_unit)
        typestr = LAL_TYPE_STR_FROM_NUMPY[self.dtype.type]
        try:
            unit = to_lal_unit(self.unit)
        except ValueError as e:
            warnings.warn("%s, defaulting to lal.DimensionlessUnit" % str(e))
            unit = lal.DimensionlessUnit
        create = getattr(lal, 'Create%sTimeSeries' % typestr.upper())
        lalts = create(self.name, lal.LIGOTimeGPS(self.epoch.gps), 0,
                       self.dt.value, unit, self.size)
        lalts.data.data = self.value
        return lalts

    @classmethod
    def from_pycbc(cls, ts, copy=True):
        """Convert a `pycbc.types.timeseries.TimeSeries` into a `TimeSeries`

        Parameters
        ----------
        ts : `pycbc.types.timeseries.TimeSeries`
            the input PyCBC `~pycbc.types.timeseries.TimeSeries` array

        copy : `bool`, optional, default: `True`
            if `True`, copy these data to a new array

        Returns
        -------
        timeseries : `TimeSeries`
            a GWpy version of the input timeseries
        """
        return cls(ts.data, t0=ts.start_time, dt=ts.delta_t, copy=copy)

    def to_pycbc(self, copy=True):
        """Convert this `TimeSeries` into a PyCBC
        `~pycbc.types.timeseries.TimeSeries`

        Parameters
        ----------
        copy : `bool`, optional, default: `True`
            if `True`, copy these data to a new array

        Returns
        -------
        timeseries : `~pycbc.types.timeseries.TimeSeries`
            a PyCBC representation of this `TimeSeries`
        """
        from pycbc import types
        return types.TimeSeries(self.value,
                                delta_t=self.dt.to('s').value,
                                epoch=self.epoch.gps, copy=copy)

    # -- TimeSeries operations ------------------

    if ASTROPY_2_0:
        def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
            # this is new in numpy 1.13, astropy 2.0 adopts it, we need to
            # work out how to handle this and __array_wrap__ together properly
            out = super(TimeSeriesBase, self).__array_ufunc__(
                ufunc, method, *inputs, **kwargs)
            if out.dtype is numpy.dtype(bool) and len(inputs) == 2:
                from .statevector import StateTimeSeries
                orig, value = inputs
                try:
                    op_ = _UFUNC_STRING[ufunc.__name__]
                except KeyError:
                    op_ = ufunc.__name__
                out = out.view(StateTimeSeries)
                out.__metadata_finalize__(orig)
                out.override_unit('')
                out.name = '%s %s %s' % (orig.name, op_, value)
            return out

    def __array_wrap__(self, obj, context=None):
        # if output type is boolean, return a `StateTimeSeries`
        if obj.dtype == numpy.dtype(bool):
            from .statevector import StateTimeSeries
            ufunc = context[0]
            value = context[1][-1]
            try:
                op_ = _UFUNC_STRING[ufunc.__name__]
            except KeyError:
                op_ = ufunc.__name__
            result = obj.view(StateTimeSeries)
            result.override_unit('')
            result.name = '%s %s %s' % (obj.name, op_, value)
        # otherwise, return a regular TimeSeries
        else:
            result = super(TimeSeriesBase, self).__array_wrap__(
                obj, context=context)
        return result


# -- ArrayTimeSeries ----------------------------------------------------------

class ArrayTimeSeries(TimeSeriesBase, Array2D):
    _default_xunit = TimeSeriesBase._default_xunit

    def __new__(cls, data, times=None, epoch=None, channel=None, unit=None,
                sample_rate=None, name=None, **kwargs):
        """Generate a new ArrayTimeSeries.
        """
        warnings.warn("The ArrayTimeSeries is deprecated and will be removed "
                      "before the 1.0 release", DeprecationWarning)
        # parse Channel input
        if channel:
            channel = (isinstance(channel, Channel) and channel or
                       Channel(channel))
            name = name or channel.name
            unit = unit or channel.unit
            sample_rate = sample_rate or channel.sample_rate
        # generate TimeSeries
        new = Array2D.__new__(cls, data, name=name, unit=unit, epoch=epoch,
                              channel=channel, x0=1/sample_rate,
                              xindex=times, **kwargs)
        return new


# -- TimeSeriesBaseDict -------------------------------------------------------

def as_series_dict_class(seriesclass):
    """Decorate a `dict` class to declare itself as the `DictClass` for
    its `EntryClass`

    This method should be used to decorate sub-classes of the
    `TimeSeriesBaseDict` to provide a reference to that class from the
    relevant subclass of `TimeSeriesBase`.
    """
    def decorate_class(cls):
        seriesclass.DictClass = cls
        return cls
    return decorate_class


@as_series_dict_class(TimeSeriesBase)
class TimeSeriesBaseDict(OrderedDict):
    """Ordered key-value mapping of named `TimeSeriesBase` objects

    This object is designed to hold data for many different sources (channels)
    for a single time span.

    The main entry points for this object are the
    :meth:`~TimeSeriesBaseDict.read` and :meth:`~TimeSeriesBaseDict.fetch`
    data access methods.
    """
    EntryClass = TimeSeriesBase

    def __iadd__(self, other):
        return self.append(other)

    def copy(self):
        new = self.__class__()
        for key, val in self.items():
            new[key] = val.copy()
        return new

    def append(self, other, copy=True, **kwargs):
        for key, ts in other.items():
            if key in self:
                self[key].append(ts, **kwargs)
            elif copy:
                self[key] = ts.copy()
            else:
                self[key] = ts
        return self

    def prepend(self, other, **kwargs):
        for key, ts in other.items():
            if key in self:
                self[key].prepend(ts, **kwargs)
            else:
                self[key] = ts
        return self

    def crop(self, start=None, end=None, copy=False):
        """Crop each entry of this `TimeSeriesBaseDict`.

        This method calls the :meth:`crop` method of all entries and
        modifies this dict in place.

        Parameters
        ----------
        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
            GPS end time of required data, defaults to end of data found;
            any input parseable by `~gwpy.time.to_gps` is fine

        See Also
        --------
        TimeSeries.crop
            for more details
        """
        for key, val in self.items():
            self[key] = val.crop(start=start, end=end, copy=copy)
        return self

    def resample(self, rate, **kwargs):
        """Resample items in this dict.

        This operation over-writes items inplace.

        Parameters
        ----------
        rate : `dict`, `float`
            either a `dict` of (channel, `float`) pairs for key-wise
            resampling, or a single float/int to resample all items.

        **kwargs
             other keyword arguments to pass to each item's resampling
             method.
        """
        if not isinstance(rate, dict):
            rate = dict((c, rate) for c in self)
        for key, resamp in rate.items():
            self[key] = self[key].resample(resamp, **kwargs)
        return self

    @classmethod
    def fetch(cls, channels, start, end, host=None, port=None,
              verify=False, verbose=False, connection=None,
              pad=None, allow_tape=None, type=None,
              dtype=None):
        """Fetch data from NDS for a number of channels.

        Parameters
        ----------
        channels : `list`
            required data channels.

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
            GPS end time of required data, defaults to end of data found;
            any input parseable by `~gwpy.time.to_gps` is fine

        host : `str`, optional
            URL of NDS server to use, if blank will try any server
            (in a relatively sensible order) to get the data

        port : `int`, optional
            port number for NDS server query, must be given with `host`.

        verify : `bool`, optional, default: `True`
            check channels exist in database before asking for data

        verbose : `bool`, optional
            print verbose output about NDS progress.

        connection : `nds2.connection`, optional
            open NDS connection to use.

        allow_tape : `bool`, optional
            allow data access from slow tapes. If `host` or `connection` is
            given, the default is to do whatever the server default is,
            otherwise servers will be searched in logical order allowing tape
            access if necessary to retrieve the data

        type : `int`, `str`, optional
            NDS2 channel type integer or string name.

        dtype : `numpy.dtype`, `str`, `type`, or `dict`
            numeric data type for returned data, e.g. `numpy.float`, or
            `dict` of (`channel`, `dtype`) pairs

        Returns
        -------
        data : :class:`~gwpy.timeseries.TimeSeriesBaseDict`
            a new `TimeSeriesBaseDict` of (`str`, `TimeSeries`) pairs fetched
            from NDS.
        """
        from ..io import nds2 as io_nds2
        from .io.nds2 import (print_verbose, fetch)

        # -- open a connection ------------------

        # open connection to specific host
        if connection is None and host is not None:
            print_verbose("Opening new connection to {0}...".format(host),
                          end=' ', verbose=verbose)
            connection = io_nds2.auth_connect(host, port)
            print_verbose('connected', verbose=verbose)
        # otherwise cycle through connections in logical order
        elif connection is None:
            ifos = set([Channel(channel).ifo for channel in channels])
            if len(ifos) == 1:
                ifo = list(ifos)[0]
            else:
                ifo = None
            hostlist = io_nds2.host_resolution_order(ifo, epoch=start)
            if allow_tape is None:
                tapes = [False, True]
            else:
                tapes = [allow_tape]
            for allow_tape in tapes:
                for host, port in hostlist:
                    try:
                        return cls.fetch(channels, start, end, host=host,
                                         port=port, verbose=verbose, type=type,
                                         dtype=dtype, pad=pad,
                                         allow_tape=allow_tape)
                    except (RuntimeError, ValueError) as e:
                        print_verbose('something went wrong:',
                                      file=sys.stderr, verbose=verbose)
                        warnings.warn(str(e), io_nds2.NDSWarning)

                # if we got this far, we can't get all channels in one go
                if len(channels) > 1:
                    return cls(
                        (c, cls.EntryClass.fetch(c, start, end,
                                                 verbose=verbose, type=type,
                                                 verify=verify,
                                                 dtype=dtype.get(c), pad=pad,
                                                 allow_tape=allow_tape))
                        for c in channels)
            e = "Cannot find all relevant data on any known server."
            if not verbose:
                e += (" Try again using the verbose=True keyword argument to "
                      "see detailed failures.")
            raise RuntimeError(e)

        # -- at this point we have an open connection, so perform fetch

        start = to_gps(start)
        end = to_gps(end)
        istart = int(start)
        iend = int(ceil(end))

        return fetch(channels, istart, iend, connection=connection,
                     host=host, port=port, verbose=verbose, type=type,
                     dtype=dtype, pad=pad, allow_tape=allow_tape,
                     series_class=cls.EntryClass).crop(start, end)

    @classmethod
    def find(cls, channels, start, end, frametype=None,
             frametype_match=None, pad=None, dtype=None, nproc=1,
             verbose=False, allow_tape=True, observatory=None, **readargs):
        """Find and read data from frames for a number of channels.

        Parameters
        ----------
        channels : `list`
            required data channels.

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
            GPS end time of required data, defaults to end of data found;
            any input parseable by `~gwpy.time.to_gps` is fine

        frametype : `str`, optional
            name of frametype in which this channel is stored, by default
            will search for all required frame types

        frametype_match : `str`, optional
            regular expression to use for frametype matching

        pad : `float`, optional
            value with which to fill gaps in the source data, defaults to
            'don't fill gaps'

        dtype : `numpy.dtype`, `str`, `type`, or `dict`
            numeric data type for returned data, e.g. `numpy.float`, or
            `dict` of (`channel`, `dtype`) pairs

        nproc : `int`, optional, default: `1`
            number of parallel processes to use, serial process by
            default.

        allow_tape : `bool`, optional, default: `True`
            allow reading from frame files on (slow) magnetic tape

        verbose : `bool`, optional
            print verbose output about NDS progress.

        **readargs
            any other keyword arguments to be passed to `.read()`
        """
        start = to_gps(start)
        end = to_gps(end)
        # -- find frametype(s)
        if frametype is None:
            frametypes = dict()
            for c in channels:
                ft = datafind.find_best_frametype(
                    c, start, end, frametype_match=frametype_match,
                    allow_tape=allow_tape)
                try:
                    frametypes[ft].append(c)
                except KeyError:
                    frametypes[ft] = [c]
            if verbose and len(frametypes) > 1:
                gprint("Determined %d frametypes to read" % len(frametypes))
            elif verbose:
                gprint("Determined best frametype as %r"
                       % list(frametypes.keys())[0])
        else:
            frametypes = {frametype: channels}
        # -- read data
        out = cls()
        for ft, clist in frametypes.items():
            if verbose:
                gprint("Reading data from %s frames..." % ft, end=' ')
            # parse as a ChannelList
            channellist = ChannelList.from_names(*clist)
            # strip trend tags from channel names
            names = [c.name for c in channellist]
            # find observatory for this group
            if observatory is None:
                try:
                    observatory = ''.join(
                        sorted(set(c.ifo[0] for c in channellist)))
                except TypeError as e:
                    e.args = ("Cannot parse list of IFOs from channel names",)
                    raise
            # find frames
            connection = datafind.connect()
            cache = connection.find_frame_urls(observatory, ft, start, end,
                                               urltype='file')
            if len(cache) == 0:
                raise RuntimeError("No %s-%s frame files found for [%d, %d)"
                                   % (observatory, ft, start, end))
            # read data
            readargs.setdefault('format', 'gwf')
            new = cls.read(cache, names, start=start, end=end, pad=pad,
                           dtype=dtype, nproc=nproc, **readargs)
            # map back to user-given channel name and append
            out.append(type(new)((key, new[c]) for
                                 (key, c) in zip(clist, names)))
            if verbose:
                gprint("Done")
        return out

    @classmethod
    def get(cls, channels, start, end, pad=None, dtype=None, verbose=False,
            allow_tape=None, **kwargs):
        """Retrieve data for multiple channels from frames or NDS

        This method dynamically accesses either frames on disk, or a
        remote NDS2 server to find and return data for the given interval

        Parameters
        ----------
        channels : `list`
            required data channels.

        start : `~gwpy.time.LIGOTimeGPS`, `float`, `str`
            GPS start time of required data,
            any input parseable by `~gwpy.time.to_gps` is fine

        end : `~gwpy.time.LIGOTimeGPS`, `float`, `str`, optional
            GPS end time of required data, defaults to end of data found;
            any input parseable by `~gwpy.time.to_gps` is fine

        frametype : `str`, optional
            name of frametype in which this channel is stored, by default
            will search for all required frame types

        pad : `float`, optional
            value with which to fill gaps in the source data, only used if
            gap is not given, or `gap='pad'` is given

        dtype : `numpy.dtype`, `str`, `type`, or `dict`
            numeric data type for returned data, e.g. `numpy.float`, or
            `dict` of (`channel`, `dtype`) pairs

        nproc : `int`, optional, default: `1`
            number of parallel processes to use, serial process by
            default.

        allow_tape : `bool`, optional, default: `None`
            allow the use of frames that are held on tape, default is `None`
            to attempt to allow the `TimeSeries.fetch` method to
            intelligently select a server that doesn't use tapes for
            data storage (doesn't always work), but to eventually allow
            retrieving data from tape if required

        verbose : `bool`, optional
            print verbose output about NDS progress.

        **kwargs
            other keyword arguments to pass to either
            `TimeSeriesBaseDict.find` (for direct GWF file access) or
            `TimeSeriesBaseDict.fetch` for remote NDS2 access
        """
        try_frames = True
        # work out whether to use NDS2 or frames
        if not os.getenv('LIGO_DATAFIND_SERVER'):
            try_frames = False
        host = kwargs.get('host', None)
        if host is not None and host.startswith('nds'):
            try_frames = False
        # try and find from frames
        if try_frames:
            if verbose:
                gprint("Attempting to access data from frames...")
            try:
                return cls.find(channels, start, end, pad=pad, dtype=dtype,
                                verbose=verbose,
                                allow_tape=allow_tape or False,
                                **kwargs)
            except (ImportError, RuntimeError, ValueError) as e:
                if verbose:
                    gprint(str(e), file=sys.stderr)
                    gprint("Failed to access data from frames, trying NDS...")

        # remove kwargs for .find()
        for key in ('nproc', 'frametype', 'frametype_match', 'observatory'):
            kwargs.pop(key, None)

        # otherwise fetch from NDS
        try:
            return cls.fetch(channels, start, end, pad=pad, dtype=dtype,
                             allow_tape=allow_tape, verbose=verbose, **kwargs)
        except RuntimeError as e:
            # if all else fails, try and get each channel individually
            if len(channels) == 1:
                raise
            else:
                if verbose:
                    gprint(str(e), file=sys.stderr)
                    gprint("Failed to access data for all channels as a "
                           "group, trying individually:")
                return cls(
                    (c, cls.EntryClass.get(c, start, end, pad=pad, dtype=dtype,
                                           allow_tape=allow_tape,
                                           verbose=verbose, **kwargs))
                    for c in channels)

    def plot(self, label='key', **kwargs):
        """Plot the data for this `TimeSeriesBaseDict`.

        Parameters
        ----------
        label : `str`, optional
            labelling system to use, or fixed label for all elements
            Special values include

            - ``'key'``: use the key of the `TimeSeriesBaseDict`,
            - ``'name'``: use the :attr:`~TimeSeries.name` of each element

            If anything else, that fixed label will be used for all lines.

        **kwargs
            all other keyword arguments are passed to the plotter as
            appropriate
        """
        from ..plotter import TimeSeriesPlot
        figargs = dict()
        for key in ['figsize', 'dpi']:
            if key in kwargs:
                figargs[key] = kwargs.pop(key)
        plot_ = TimeSeriesPlot(**figargs)
        ax = plot_.gca()
        for lab, ts in self.items():
            if label.lower() == 'name':
                lab = ts.name
            elif label.lower() != 'key':
                lab = label
            ax.plot(ts, label=lab, **kwargs)
        return plot_


# -- TimeSeriesBaseList -------------------------------------------------------

class TimeSeriesBaseList(list):
    """Fancy list representing a list of `TimeSeriesBase`

    The `TimeSeriesBaseList` provides an easy way to collect and organise
    `TimeSeriesBase` for a single `Channel` over multiple segments.

    Parameters
    ----------
    *items
        any number of `TimeSeriesBase`

    Returns
    -------
    list
        a new `TimeSeriesBaseList`

    Raises
    ------
    TypeError
        if any elements are not `TimeSeriesBase`
    """
    EntryClass = TimeSeriesBase

    def __init__(self, *items):
        """Initialise a new list
        """
        super(TimeSeriesBaseList, self).__init__()
        for item in items:
            self.append(item)

    @property
    def segments(self):
        from ..segments import SegmentList
        return SegmentList([item.span for item in self])

    def append(self, item):
        if not isinstance(item, self.EntryClass):
            raise TypeError("Cannot append type '%s' to %s"
                            % (type(item).__name__, type(self).__name__))
        super(TimeSeriesBaseList, self).append(item)
        return self
    append.__doc__ = list.append.__doc__

    def extend(self, item):
        item = TimeSeriesBaseList(*item)
        super(TimeSeriesBaseList, self).extend(item)
    extend.__doc__ = list.extend.__doc__

    def coalesce(self):
        """Merge contiguous elements of this list into single objects

        This method implicitly sorts and potentially shortens this list.
        """
        self.sort(key=lambda ts: ts.t0.value)
        i = j = 0
        N = len(self)
        while j < N:
            this = self[j]
            j += 1
            if j < N and this.is_contiguous(self[j]) == 1:
                while j < N and this.is_contiguous(self[j]):
                    try:
                        this = self[i] = this.append(self[j])
                    except ValueError as e:
                        if 'cannot resize this array' in str(e):
                            this = this.copy()
                            this = self[i] = this.append(self[j])
                        else:
                            raise
                    j += 1
            else:
                self[i] = this
            i += 1
        del self[i:]
        return self

    def join(self, pad=0.0, gap='raise'):
        """Concatenate all of the elements of this list into a single object

        Parameters
        ----------
        pad : `float`, optional, default: `0.0`
            value with which to pad gaps

        gap : `str`, optional, default: `'raise'`
            what to do in the event of a discontguity in the data, one of

            - 'raise': raise an exception
            - 'warn': print a warning
            - 'ignore': append series as if there was no gap
            - 'pad': pad the gap with a value

        Returns
        -------
        `TimeSeriesBase`
             a single `TimeSeriesBase` covering the full span of all entries
             in this list

        See Also
        --------
        TimeSeriesBase.append
            for details on how the individual series are concatenated together
        """
        if len(self) == 0:
            return self.EntryClass(numpy.empty((0,) * self.EntryClass._ndim))
        self.sort(key=lambda t: t.epoch.gps)
        out = self[0].copy()
        for ts in self[1:]:
            out.append(ts, gap=gap, pad=pad)
        return out

    def __getslice__(self, i, j):
        return type(self)(*super(TimeSeriesBaseList, self).__getslice__(i, j))

    def __getitem__(self, key):
        if isinstance(key, slice):
            return type(self)(
                *super(TimeSeriesBaseList, self).__getitem__(key))
        else:
            return super(TimeSeriesBaseList, self).__getitem__(key)

    def copy(self):
        """Return a copy of this list with each element copied to new memory
        """
        out = type(self)()
        for series in self:
            out.append(series.copy())
        return out
