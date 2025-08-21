from collections import OrderedDict
from pynq.overlay import Overlay
from pynq.buffer import allocate
import xrfclk
from xrfdc import RFdc
from pathlib import Path
import numpy as np
import os
import traceback
import time
from qsz007.ip import Metadata
from qsz007.driver import AxisTomography

class SOC(Overlay):
    DAC_TILE_NUM = 4
    ADC_TILE_NUM = 4
    """
    This class defines the QRNG object.
    """
    
    def __init__(self, bitfile:str=None, ignore_version=True, download=False):
        """
        This method constructs the QSZ007 object.
        """
        print("Initializing QICK SOC")
        if bitfile == None:
            filepath = str(Path(__file__).parent/'qsz007.bit')
        else:
            filepath = str(Path(__file__).parent/bitfile)
        print(f"Loading bitfile: {filepath}")
        super().__init__(filepath, ignore_version=ignore_version, download=False)
        print("QICK SOC initialized")
        # Initialize the configuration
        self._cfg = {}

        self.__config_rfdc() # get on used DAC and ADC tiles and their reference clocks
        print("config_rfdc")
        self.__config_clocks(download) # set the clocks if clocks are not locked
        print("config_clocks")

        self.metadata = Metadata(self)
        print("metadata")

        self.__init_socip()
        print("init_socip")

    def __getitem__(self, key):
        return self._cfg[key]

    def __setitem__(self, key, val):
        self._cfg[key] = val

    def __config_rfdc(self):
        rf_config = self.ip_dict['usp_rf_data_converter_0']['parameters']

        self.hs_adc = rf_config['C_High_Speed_ADC'] == '1'

        self.dac_tiles = []
        self.adc_tiles = []
        refclk_freqs = []
        print("Configuring RFDC tiles")
        self['dacs'] = OrderedDict()
        self['adcs'] = OrderedDict()
        
        for iTile in range(4):
            print(f"Configuring RFDC DAC tile {iTile}")
            if rf_config['C_DAC%d_Enable' % (iTile)] != '1':
                continue
            self.dac_tiles.append(iTile)
            f_fabric = float(rf_config['C_DAC%d_Fabric_Freq' % (iTile)])
            f_refclk = float(rf_config['C_DAC%d_Refclk_Freq' % (iTile)])
            refclk_freqs.append(f_refclk)
            fbdiv = int(rf_config['C_DAC%d_FBDIV' % (iTile)])
            refdiv = int(rf_config['C_DAC%d_Refclk_Div' % (iTile)])
            outdiv = int(rf_config['C_DAC%d_OutDiv' % (iTile)])
            fs_div = refdiv*outdiv
            fs_mult = fbdiv
            fs = float(rf_config['C_DAC%d_Sampling_Rate' % (iTile)])*1000
            for iBlock in range(4):
                if rf_config['C_DAC_Slice%d%d_Enable' % (iTile, iBlock)] != 'true':
                    continue
                # define a 2-digit "name" that we'll use to refer to this channel
                chname = "%d%d" % (iTile, iBlock)
                interpolation = int(rf_config['C_DAC_Interpolation_Mode%d%d' % (iTile, iBlock)])
                self['dacs'][chname] = {'fs': fs,
                                       'fs_div': fs_div,
                                       'fs_mult': fs_mult,
                                       'f_fabric': f_fabric,
                                       'interpolation': interpolation}

        for iTile in range(4):
            print(f"Configuring RFDC ADC tile {iTile}")
            if rf_config['C_ADC%d_Enable' % (iTile)] != '1':
                continue
            self.adc_tiles.append(iTile)
            f_fabric = float(rf_config['C_ADC%d_Fabric_Freq' % (iTile)])
            f_refclk = float(rf_config['C_ADC%d_Refclk_Freq' % (iTile)])
            refclk_freqs.append(f_refclk)
            fbdiv = int(rf_config['C_ADC%d_FBDIV' % (iTile)])
            refdiv = int(rf_config['C_ADC%d_Refclk_Div' % (iTile)])
            outdiv = int(rf_config['C_ADC%d_OutDiv' % (iTile)])
            fs_div = refdiv*outdiv
            fs_mult = fbdiv
            fs = float(rf_config['C_ADC%d_Sampling_Rate' % (iTile)])*1000
            for iBlock in range(4):
                # for dual-ADC FPGAs, each channel is two blocks
                # so just look at the even blocks
                if self.hs_adc and (iBlock%2 != 0):
                    continue
                if rf_config['C_ADC_Slice%d%d_Enable' % (iTile, iBlock)] != 'true':
                    continue
                # define a 2-digit "name" that we'll use to refer to this channel
                chname = "%d%d" % (iTile, iBlock)
                decimation = int(rf_config['C_ADC_Decimation_Mode%d%d' % (iTile, iBlock)])
                self['adcs'][chname] = {'fs': fs,
                                       'fs_div': fs_div,
                                       'fs_mult': fs_mult,
                                       'f_fabric': f_fabric,
                                       'decimation': decimation}

        def get_common_freq(freqs):
            """
            Check that all elements of the list are equal, and return the common value.
            """
            if not freqs:  # input is empty list
                return None
            if len(set(freqs)) != 1:
                raise RuntimeError("Unexpected frequencies:", freqs)
            return freqs[0]

        self['refclk_freq'] = get_common_freq(refclk_freqs)

    def __clocks_locked(self):
        """
        Check if all the clocks are locked.
        """
        dac_locked = [self.usp_rf_data_converter_0.dac_tiles[iTile]
                      .PLLLockStatus == 2 for iTile in self.dac_tiles]
        adc_locked = [self.usp_rf_data_converter_0.adc_tiles[iTile]
                      .PLLLockStatus == 2 for iTile in self.adc_tiles]
        return all(dac_locked) and all(adc_locked)
        
    def __set_all_clks(self):
        """
        Resets all the board clocks
        # master clock generator is LMK04828, which is used for DAC/ADC clocks
        # only 245.76 available by default
        # LMX2594 is not used
        # available: 102.4, 204.8, 409.6, 491.52, 737.0
        """
        lmk_freq = self['refclk_freq']
        lmx_freq = self['refclk_freq']*2
        assert hasattr(xrfclk, "xrfclk") # ZCU216 only has a pynq 2.7 image
        xrfclk.xrfclk._find_devices()
        xrfclk.xrfclk._read_tics_output()
        xrfclk.set_ref_clks(lmk_freq=lmk_freq, lmx_freq=lmx_freq)

    def __config_clocks(self, download):
        """
        Configure PLLs if requested, or if any ADC/DAC is not locked.
        """
        if download:
            # self.download()
            pass
        # if not self.__clocks_locked():
            # print("Clocks not locked, setting all clocks")
            # self.__set_all_clks()
            # self.download()
        # if not self.__clocks_locked():
            # raise RuntimeError(
                # "Not all DAC and ADC PLLs are locked. You may want to repeat the initialization of the QickSoc.")

    def __init_socip(self):
        """
        Initialize the SOC IPs.
        """
        # Use the HWH parser to trace connectivity and deduce the channel numbering.
        for key, val in self.ip_dict.items():
            print(f"Initializing {key} with driver {val['driver']}")
            if hasattr(val['driver'], 'configure_connections'):
                getattr(self, key).configure_connections(self)

        # Signal generators (anything driven by the tProc)
        self.socip = []

        for key, val in self.ip_dict.items():
            if val['driver'] == AxisTomography:
                self.socip.append(getattr(self, key))

        for i, ip in enumerate(self.socip):
            ip.configure()
        
    def upload_file(self, file_name:str, file:bytes):
        """
        This method uploads the bitstream to the FPGA.
        """
        try:
            fullpath = Path(__file__).parent/file_name
            with open(fullpath, 'wb') as f:
                f.write(file)
        except Exception as e:
            return False, traceback.format_exc()
        return True, None
    
    def reload_bitstream(self, bitfile_name:str):
        """
        This method reloads the bitstream to the FPGA.
        """
        try:
            bitfile_name = os.path.join(os.path.dirname(__file__), bitfile_name)
            self.__init__(bitfile_name, ignore_version=True, download=True)
        except Exception as e:
            return False, traceback.format_exc()
        return True, None
        
    def set_waveform(self, ch:int=0, fall_time_ms:int=10, max_scal:float=1.0):
        """
        This method sets the waveform parameters for the specified channel.
        """
        if ch > len(self.socip) - 1 or ch < 0:
            raise ValueError(f"Invalid channel number: {ch}. Valid range is 0 to {len(self.socip) - 1}.")
        if not isinstance(self.socip[ch], AxisTomography):
            raise TypeError(f"Channel {ch} is not a valid AxisTomography instance.")
        
        self.socip[ch].set_waveform(fall_time_ms, max_scal)
        
        return True

    def set_ttl_tag(self, ch:int=0, ttl_bit:int=0, time_ms:int=150):
        """
        This method sets a TTL tag for the specified channel.
        """
        if ch > len(self.socip) - 1 or ch < 0:
            raise ValueError(f"Invalid channel number: {ch}. Valid range is 0 to {len(self.socip) - 1}.")
        if not isinstance(self.socip[ch], AxisTomography):
            raise TypeError(f"Channel {ch} is not a valid AxisTomography instance.")
        
        self.socip[ch].set_ttl_tag(ttl_bit, time_ms)
        
        return True
    
    def set_threshold(self, ch:int=0, threshold:float=0.5):
        """
        This method sets the ADC threshold for the specified channel.
        """
        if ch > len(self.socip) - 1 or ch < 0:
            raise ValueError(f"Invalid channel number: {ch}. Valid range is 0 to {len(self.socip) - 1}.")
        if not isinstance(self.socip[ch], AxisTomography):
            raise TypeError(f"Channel {ch} is not a valid AxisTomography instance.")
        
        self.socip[ch].set_threshold(threshold)
        
        return True
    
    def start_tomography(self, ch:int=0, cycle:int=1):
        """
        This method starts the tomography process for the specified channel.
        """
        if ch > len(self.socip) - 1 or ch < 0:
            raise ValueError(f"Invalid channel number: {ch}. Valid range is 0 to {len(self.socip) - 1}.")
        if not isinstance(self.socip[ch], AxisTomography):
            raise TypeError(f"Channel {ch} is not a valid AxisTomography instance.")
        
        self.socip[ch].start_tomography(cycle)
        
        return True
    
    def poll_data(self, ch:int=0, totaltime=0.5, timeout=None):
        """
        This method polls data from the specified channel.
        """
        if ch > len(self.socip) - 1 or ch < 0:
            raise ValueError(f"Invalid channel number: {ch}. Valid range is 0 to {len(self.socip) - 1}.")
        if not isinstance(self.socip[ch], AxisTomography):
            raise TypeError(f"Channel {ch} is not a valid AxisTomography instance.")
        
        return self.socip[ch].poll_data(totaltime, timeout)
