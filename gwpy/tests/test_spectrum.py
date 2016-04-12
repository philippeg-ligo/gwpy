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

"""Unit test for spectrum module
"""

from tempfile import NamedTemporaryFile

from numpy import (testing as nptest, arange, linspace)

from scipy import signal

from astropy import units

from gwpy import version
from gwpy.spectrum import (Spectrum, SpectralVariance)
from gwpy.plotter import SpectrumPlot

from test_array import (SeriesTestCase, Array2DTestCase)

__author__ = 'Duncan Macleod <duncan.macleod@ligo.org>'
__version__ = version.version


# -----------------------------------------------------------------------------

class SpectrumTestCase(SeriesTestCase):
    """`~unittest.TestCase` for the `~gwpy.spectrum.Spectrum` class
    """
    TEST_CLASS = Spectrum

    def test_f0_df(self):
        array = self.create()
        self.assertEqual(array.f0, array.x0)
        self.assertEqual(array.df, array.dx)
        self.assertEqual(array.f0, 0 * units.Hertz)
        self.assertEqual(array.df, 1 * units.Hertz)

    def test_frequencies(self):
        array = self.create()
        self.assertArraysEqual(array.frequencies,
                               arange(array.size) * array.df + array.f0)

    def test_plot(self):
        array = self.create()
        plot = array.plot()
        self.assertIsInstance(plot, SpectrumPlot)

    def test_filter(self):
        array = self.create()
        a2 = array.filter([100], [1], 1e-2)
        self.assertIsInstance(a2, type(array))
        self.assertArraysEqual(a2.frequencies, array.frequencies)
        # manually rebuild the filter to test it works
        b, a, = signal.zpk2tf([100], [1], 1e-2)
        fresp = abs(signal.freqs(b, a, array.frequencies.value)[1])
        nptest.assert_array_equal(a2.value, fresp * array.value)

    def test_zpk(self):
        array = self.create()
        # just test that it works
        array2 = array.zpk([100], [1], 1e-2)
        self.assertIsInstance(array2, type(array))
        self.assertArraysEqual(array2.frequencies, array.frequencies)

    def test_to_from_lal(self):
        array = self.create()
        try:
            lalarray = array.to_lal()
        except ImportError as e:
            self.skipTest(str(e))
        nptest.assert_array_equal(lalarray.data.data, array.value)
        array2 = type(array).from_lal(lalarray)
        self.assertArraysEqual(array, array2, 'units', 'df', 'f0')

    def test_to_from_pycbc(self):
        array = self.create()
        try:
            pycbcarray = array.to_pycbc()
        except ImportError as e:
            self.skipTest(str(e))
        nptest.assert_array_equal(pycbcarray.data, array.value)
        array2 = type(array).from_pycbc(pycbcarray)
        self.assertArraysEqual(array, array2, 'units', 'df', 'f0')

    def _test_read_write(self, extension, fmt=None, **metadata):
        if fmt is None:
            fmt = extension.strip('.')
        if not extension.startswith('.'):
            extension = '.%s' % extension
        array = self.create(**metadata)
        with NamedTemporaryFile(suffix=extension, delete=True) as f:
            array.write(f.name, format=fmt)
            f.seek(0)
            array2 = self.TEST_CLASS.read(f.name, format=fmt)
        self.assertArraysEqual(array, array2)

    def test_read_write_hdf5(self):
        self.assertRaises(ValueError, self._test_read_write, 'hdf', fmt='hdf')
        self._test_read_write('hdf', fmt='hdf', name='Array name')
        self._test_read_write('hdf', name='Array name')

    def test_read_write_txt(self):
        self._test_read_write('txt', fmt='txt')
        self._test_read_write('txt')


class SpectralVarianceTestCase(Array2DTestCase):
    TEST_CLASS = SpectralVariance

    def setUp(self):
        super(SpectralVarianceTestCase, self).setUp()
        self.bins = linspace(0, 1e5, self.data.shape[1] + 1, endpoint=True)

    def create(self, *args, **kwargs):
        kwargs.setdefault('copy', False)
        return self.TEST_CLASS(self.data, self.bins, *args, **kwargs)

    def test_init(self):
        # test with some data
        array = self.create()
        nptest.assert_array_equal(array.value, self.data)
        nptest.assert_array_equal(array.bins.value, self.bins)
        self.assertEqual(array.x0, 0 * units.Hertz)
        self.assertEqual(array.df, 1 * units.Hertz)
        self.assertEqual(array.y0, self.bins[0])
        self.assertEqual(array.dy, self.bins[1] - self.bins[0])

    def test_plot(self):
        plot = self.create().plot()
        self.assertIsInstance(plot, SpectrumPlot)

    def test_value_at(self):
        ts1 = self.create(dx=.5, unit='m')
        y = self.bins[2]
        self.assertEqual(ts1.value_at(1.5, self.bins[3]),
                         self.data[3][3] * ts1.unit)
        self.assertEqual(ts1.value_at(1.0 * ts1.xunit,
                                      self.bins[1] * ts1.yunit),
                         self.data[2][1] * units.m)
        self.assertRaises(IndexError, ts1.value_at, 1.6, 5.8)


if __name__ == '__main__':
    unittest.main()