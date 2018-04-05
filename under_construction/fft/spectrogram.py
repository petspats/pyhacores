import numpy as np
from pyha import Hardware, simulate, hardware_sims_equal, sims_close
from under_construction.fft.bitreversal_fftshift_decimate import BitreversalFFTshiftDecimate
from under_construction.fft.conjmult import ConjMult
from under_construction.fft.fft_core import R2SDF
from under_construction.fft.packager import Packager, unpackage
from under_construction.fft.windower import Windower
from scipy import signal


class Spectrogram(Hardware):
    """ The gain of main/model_main wont match"""
    def __init__(self, nfft, window_type='hanning', decimate_by=2):
        self.DECIMATE_BY = decimate_by
        self.NFFT = nfft
        self.WINDOW_TYPE = window_type

        # components
        self.pack = Packager(self.NFFT)
        self.windower = Windower(nfft, self.WINDOW_TYPE)
        self.fft = R2SDF(nfft)
        self.abs = ConjMult()
        self.dec = BitreversalFFTshiftDecimate(nfft, decimate_by)

        self.DELAY = self.pack.DELAY + self.windower.DELAY + self.fft.DELAY + self.abs.DELAY + self.dec.DELAY

    def main(self, x):
        pack_out = self.pack.main(x)
        window_out = self.windower.main(pack_out)
        fft_out = self.fft.main(window_out)
        mag_out = self.abs.main(fft_out)
        dec_out = self.dec.main(mag_out)
        return dec_out

    def model_main(self, x):
        _, _, spectro_out = signal.spectrogram(x, 1, nperseg=self.NFFT, return_onesided=False, detrend=False,
                                               noverlap=0, window='hanning')

        # fftshift
        shifted = np.roll(spectro_out, self.NFFT // 2, axis=0)

        # # avg decimation
        l = np.split(shifted, len(shifted) // self.DECIMATE_BY)
        golden_output = np.average(l, axis=1).T

        return golden_output


def test_simple():
    np.random.seed(0)
    fft_size=128
    dut = Spectrogram(fft_size)

    packets = 2
    inp = np.random.uniform(-1, 1, fft_size * packets) + np.random.uniform(-1, 1, fft_size * packets) * 1j
    inp *= 0.25

    sims = simulate(dut, inp,
                    output_callback=unpackage,
                    simulations=['MODEL', 'PYHA', 'RTL'],
                    conversion_path='/home/gaspar/git/pyhacores/playground')

    # import matplotlib.pyplot as plt
    # plt.plot(np.hstack(sims['MODEL']))
    # plt.plot(np.hstack(sims['PYHA']))
    # plt.plot(np.hstack(sims['RTL']))
    # plt.show()

    sims['MODEL'] = np.array(sims['MODEL']) / np.array(sims['MODEL']).max()
    sims['PYHA'] = np.array(sims['PYHA']) / np.array(sims['PYHA']).max()
    sims['RTL'] = np.array(sims['RTL']) / np.array(sims['RTL']).max()
    assert sims_close(sims, rtol=1e-1, atol=1e-4)
