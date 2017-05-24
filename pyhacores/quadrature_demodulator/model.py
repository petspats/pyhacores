import numpy as np

from pyha.common.const import Const
from pyha.common.hwsim import HW, default_sfix
from pyha.common.sfix import ComplexSfix, resize, Sfix, fixed_truncate

from pyhacores.cordic.model import Angle
from pyhacores.util_complex.model import Conjugate, ComplexMultiply


class QuadratureDemodulator(HW):
    """
    http://gnuradio.org/doc/doxygen-3.7/classgr_1_1analog_1_1quadrature__demod__cf.html#details

    :param gain: inverse of tx sensitivity. In RTL this is further multiplied by PI, because CORDIC returns angle in -1 to 1 range.
    """
    def __init__(self, gain=1.0, normalized_output=True):
        """

        :param gain: inverse of tx sensitivity
        :param normalized_output: If True, returns in [-1 ... 1] range, else in [-pi .. pi]
        """
        self.gain = gain

        # components / registers
        self.conjugate = Conjugate()
        self.complex_mult = ComplexMultiply()
        self.angle = Angle()
        self.y = Sfix(0, default_sfix, round_style=fixed_truncate)

        # constants
        # pi term puts angle output to pi range
        self.gain_sfix = Const(Sfix(self.gain * np.pi, 3, -14))

        self._delay = self.conjugate._delay + \
                     self.complex_mult._delay + \
                     self.angle._delay + 1

    def main(self, c):
        """
        :type c: ComplexSfix
        :rtype: Sfix
        """
        conj = self.conjugate.main(c)
        mult = self.complex_mult.main(c, conj)
        angle = self.angle.main(mult)

        self.y = self.gain_sfix * angle
        return self.y

    def model_main(self, c):
        demod = np.angle(c[1:] * np.conjugate(c[:-1]))
        fix_gain = self.gain * demod
        return fix_gain
