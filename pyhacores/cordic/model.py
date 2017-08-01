from enum import Enum

import numpy as np

from pyha.common.const import Const
from pyha.common.hwsim import HW
from pyha.common.sfix import resize, Sfix, right_index, left_index, fixed_wrap, fixed_truncate, ComplexSfix


class CordicMode(Enum):
    VECTORING, ROTATION = range(2)


class Cordic(HW):
    """
    CORDIC algorithm.

    readable paper -> http://www.andraka.com/files/crdcsrvy.pdf

    :param iterations: resource/ precision trade off
    :param mode: vectoring or rotation
    """

    def __init__(self, iterations, mode):
        self.MODE = mode
        self.iterations = iterations

        # + 1 is basically for initial step registers it also helps pipeline code
        self.iterations = iterations + 1
        self.phase_lut = [float(np.arctan(2 ** -i) / np.pi) for i in range(self.iterations)]
        self.PHASE_LUT = [Sfix(x, 0, -24) for x in self.phase_lut]

        # pipeline registers
        # give 1 extra bit, as there is stuff like CORDIC gain.. in some cases 2 bits may be needed!
        # there will be CORDIC gain + abs value held by x can be > 1
        self.x = [Sfix(0, 1, -17, overflow_style=fixed_wrap, round_style=fixed_truncate)] * self.iterations
        self.y = [Sfix(0, 1, -17, overflow_style=fixed_wrap, round_style=fixed_truncate)] * self.iterations
        self.phase = [Sfix(0, 1, -24, overflow_style=fixed_wrap, round_style=fixed_truncate)] * self.iterations

        self.DELAY = self.iterations

    def initial_step(self, phase, x, y):
        """
        CORDIC works in only 1 quadrant, this performs steps to make it usable on other qudrants.
        """
        self.x[0] = x
        self.y[0] = y
        self.phase[0] = phase
        if self.MODE == CordicMode.ROTATION:
            if phase > 0.5:
                # > np.pi/2
                self.x[0] = -x
                self.phase[0] = phase - 1.0
            elif phase < -0.5:
                # < -np.pi/2
                self.x[0] = -x
                self.phase[0] = phase + 1.0

        elif self.MODE == CordicMode.VECTORING:
            if x < 0.0 and y > 0.0:
                # vector in II quadrant -> initial shift by PI to IV quadrant (mirror)
                self.x[0] = -x
                self.y[0] = -y
                self.phase[0] = Sfix(1.0, phase)
            elif x < 0.0 and y < 0.0:
                # vector in III quadrant -> initial shift by -PI to I quadrant (mirror)
                self.x[0] = -x
                self.y[0] = -y
                self.phase[0] = Sfix(-1.0, phase)

    def main(self, x, y, phase):
        """
        Runs one step of pipelined CORDIC
        Returned phase is in 1 to -1 range
        """
        self.initial_step(phase, x, y)

        # pipelined CORDIC
        for i in range(len(self.PHASE_LUT) - 1):
            if self.MODE == CordicMode.ROTATION:
                direction = self.phase[i] > 0
            elif self.MODE == CordicMode.VECTORING:
                direction = self.y[i] < 0

            if direction:
                self.x[i + 1] = self.x[i] - (self.y[i] >> i)
                self.y[i + 1] = self.y[i] + (self.x[i] >> i)
                self.phase[i + 1] = self.phase[i] - self.PHASE_LUT[i]
            else:
                self.x[i + 1] = self.x[i] + (self.y[i] >> i)
                self.y[i + 1] = self.y[i] - (self.x[i] >> i)
                self.phase[i + 1] = self.phase[i] + self.PHASE_LUT[i]

        return self.x[-1], self.y[-1], self.phase[-1]


class ToPolar(HW):
    """
    Converts IQ to polar form, returning 'abs' and 'angle/pi'.
    """

    def __init__(self):
        self.core = Cordic(13, CordicMode.VECTORING)
        self.y_abs = Sfix(0, 0, -17, round_style=fixed_truncate)
        self.y_angle = Sfix(0, 0, -17, round_style=fixed_truncate)

        self._delay = self.core.iterations + 1

    def main(self, c):
        """
        :type c: ComplexSfix
        :return: abs (gain corrected) angle (in 1 to -1 range)
        """
        phase = Sfix(0.0, 0, -24)

        abs, _, angle = self.core.main(c.real, c.imag, phase)

        # get rid of CORDIC gain and extra bits
        self.y_abs = abs * (1.0 / 1.646760)
        self.y_angle = angle
        return self.y_abs, self.y_angle

    def model_main(self, cin):
        # note that angle in -1..1 range
        rabs = [np.abs(x) for x in cin]
        angle = [np.angle(x) / np.pi for x in cin]
        return rabs, angle


class Angle(HW):
    """
    Eaual to Numpy.angle()/pi
    """

    def __init__(self):
        self.core = ToPolar()
        self._delay = self.core._delay

    def main(self, c):
        _, angle = self.core.main(c)
        return angle

    def model_main(self, cin):
        # note that angle in -1..1 range
        return [np.angle(x) / np.pi for x in cin]


class Abs(HW):
    """
    Eaual to Numpy.abs()
    """

    def __init__(self):
        self.core = ToPolar()
        self._delay = self.core._delay

    def main(self, c):
        abs, _ = self.core.main(c)
        return abs

    def model_main(self, cin):
        return [np.abs(x) for x in cin]


class NCO(HW):
    """
    Baseband signal generator. Integrated phase accumulator.
    """
    def __init__(self):
        self.cordic = Cordic(16, CordicMode.ROTATION)
        self.phase_acc = Sfix(0, 0, -24, overflow_style=fixed_wrap, round_style=fixed_truncate)
        self._delay = self.cordic.iterations + 1

    def main(self, phase_inc):
        """
        :param phase_inc: amount of rotation applied for next clock cycle, must be normalized to -1 to 1.
        :rtype: ComplexSfix
        """
        self.phase_acc = self.phase_acc + phase_inc

        start_x = Sfix(1.0 / 1.646760, 0, -17)  # gets rid of cordic gain, could add amplitude modulation here
        start_y = Sfix(0.0, 0, -17)

        x, y, phase = self.cordic.main(start_x, start_y, self.phase_acc)
        xr = resize(x, 0, -17)
        yr = resize(y, 0, -17)
        retc = ComplexSfix(xr, yr)
        return retc

    def model_main(self, phase_list):
        p = np.cumsum(np.array(phase_list) * np.pi)
        return np.exp(p * 1j)
