"""
Drivers for QRNG.
"""
from abc import ABC
import threading
from threading import Thread, Event
from queue import Queue, Empty
import time
import numpy as np
import logging
from pynq.buffer import allocate
from qsz007.ip import SocIp

class AbsDacDriver(SocIp):
    """
    Abstract class which defines methods that for RFDC DAC.
    """
    # The DAC channel has a mixer.
    DAC_HAS_MIXER = False
    # Maximum waveform amplitude.
    DAC_MAXV = 2**15-2
    # Scale factor between MAXV and the default maximum amplitude (necessary to avoid overshoot).
    DAC_MAXV_SCALE = 1.0
    # Name of the output connect to RFDC.
    DAC_RFDC_PORT = 'm_axis'

    # Configure this driver with links to the other drivers, and the signal gen channel number.
    def configure(self):
        super().configure()
        
        for p in ['fs', 'fs_mult', 'fs_div', 'interpolation', 'f_fabric']:
            self['dac'][p] = self.soc['dacs'][self['dac']['chname']][p]
        # interpolation reduces the DDS range
        self['dac']['f_dds'] = self['dac']['fs'] / self['dac']['interpolation']
        self['dac']['fdds_div'] = self['dac']['fs_div'] * self['dac']['interpolation']
        
        self['dac']['maxv'] = self.DAC_MAXV
        self['dac']['maxv_scale'] = self.DAC_MAXV_SCALE

    def configure_connections(self, soc):
        super().configure_connections(soc)

        # what RFDC port does this generator drive?
        trace_result = soc.metadata.trace_forward(self.fullpath, self.DAC_RFDC_PORT, ["usp_rf_data_converter"], 1)
        
        _, port, _ = trace_result[0]
        self['dac'] = {}
        self['dac']['chname'] = port[1:3]

class AbsAdcDriver(SocIp):
    """
    Abstract class which defines methods that for RFDC ADC.
    """
    # Name of the input connect to RFDC.
    ADC_RFDC_PORT = 's_axis'

    # Configure this driver with links to the other drivers, and the signal gen channel number.
    def configure(self):
        super().configure()
        
        for p in ['fs', 'fs_mult', 'fs_div', 'decimation', 'f_fabric']:
            self['adc'][p] = self.soc['adcs'][self['adc']['chname']][p]
        # interpolation reduces the DDS range
        self['adc']['f_dds'] = self['adc']['fs'] / self['adc']['decimation']
        self['adc']['fdds_div'] = self['adc']['fs_div'] * self['adc']['decimation']

    def configure_connections(self, soc):
        super().configure_connections(soc)

        # what RFDC port drives this readout?
        trace_result = soc.metadata.trace_back(self.fullpath, self.ADC_RFDC_PORT, ["usp_rf_data_converter"])
        
        _, port, _ = trace_result
        self['adc'] = {}
        self['adc']['chname'] = port[1:3]

class AxisTomography(AbsDacDriver, AbsAdcDriver):
    """
    AxisTomography

    AXIS Tomography Registers.
    """
    bindto = ['Quantaser:user:axis_tomography:1.0']
    REGISTERS = {'start': 0,
                 'cycle': 1,
                 'tx_period': 2,
                 'tx_tag_fall': 3,
                 'tx_ttl0_rise': 4,
                 'tx_ttl1_rise': 5,
                 'tx_ttl2_rise': 6,
                 'tx_ttl3_rise': 7,
                 'tx_ttl4_rise': 8,
                 'tx_ttl5_rise': 9,
                 'tx_ttl6_rise': 10,
                 'tx_ttl7_rise': 11,
                 'tx_ttl0_fall': 12,
                 'tx_ttl1_fall': 13,
                 'tx_ttl2_fall': 14,
                 'tx_ttl3_fall': 15,
                 'tx_ttl4_fall': 16,
                 'tx_ttl5_fall': 17,
                 'tx_ttl6_fall': 18,
                 'tx_ttl7_fall': 19,
                 'tx_ratio_rise': 20,
                 'tx_ratio_fall': 21,
                 'rx_state': 22,
                 'rx_tag_cnt': 23,
                 'rx_data_cnt': 24,
                 'rx_tri_limit': 25,
                 'rx_tri_mode': 26,
                 'rx_tri_wait_time': 27,
                 'rx_tri_edge': 28,
                 'rx_tri_threshold': 29,
                 'rx_dc_limit': 30,
                 'rx_dc_en': 31,
                 'rx_dc_rate': 32}
    
    # Name of the output connect to RFDC.
    DAC_RFDC_PORT = 'M0_DAC'
    # Name of the input connect to RFDC.
    ADC_RFDC_PORT = 'S0_ADC'
    DMA_TIME_PORT = 'M0_ADC'
    DMA_GRAPHY_PORT = 'M1_ADC'
    DMA_DC_PORT = 'M2_ADC'

    TRIGGER_LIMIT = 15000  # Maximum number of triggers per cycle.
    DC_RATE = 500  # DC sampling rate in KHz.

    def __init__(self, description):
        super().__init__(description)
        self.lock = threading.Lock()
        # Generics
        self.INTERPOLATION = int(description['parameters']['INTERPOLATION'])

        self.graphy_clk = (1024 // self.INTERPOLATION)
        
        # self.dma_tag_buf = allocate(shape=(self.TRIGGER_LIMIT), dtype=np.uint32)
        # self.dma_dc_buf = allocate(shape=self.TRIGGER_LIMIT * self.INTERPOLATION, dtype=np.int16)
        # self.dma_graphy_buf = allocate(shape=(self.TRIGGER_LIMIT * 1024), dtype=np.int16)

        self.__start_thread()

    def configure(self):
        super().configure()

        self['interpolation'] = self.INTERPOLATION
        self['graphy_clk'] = self.graphy_clk

        # Allocate DMA buffers
        self.dma_tag_buf = []
        self.dma_dc_buf = []
        self.dma_graphy_buf = []
        for i in range(2):
            self.dma_tag_buf.append(allocate(shape=(self.TRIGGER_LIMIT), dtype=np.uint32))
            self.dma_dc_buf.append(allocate(shape=(200 * self.DC_RATE), dtype=np.int16))
            self.dma_graphy_buf.append(allocate(shape=(self.TRIGGER_LIMIT * 1024), dtype=np.int16))

        # Default TTL parameters.
        ttl_rise = int(np.round(10 * 1000 * self['dac']['f_fabric']))  # 10ms
        ttl_fall = int(np.round(140 * 1000 * self['dac']['f_fabric']))  # 140ms
        half_period = int(np.round(150 * 1000 * self['dac']['f_fabric']))  # 150ms

        # Default registers.
        self.cycle_period = 300 * 0.001  # 300ms
        self.cycle = 1
        self.tx_period = (half_period * 2) - 1  # 300ms
        self.tx_tag_fall = half_period - 1  # 150ms
        self.ttl = []
        for i in range(8):
            self.ttl.append({'rise': (2**32 - 1), 'fall': (2**32 - 1)})  # Disable TTL by default
            setattr(self, "tx_ttl%d_rise"%(i), self.ttl[i]['rise'])
            setattr(self, "tx_ttl%d_fall"%(i), self.ttl[i]['fall'])
        self.tx_ratio_rise = int(np.round((self.DAC_MAXV * 2**16) / half_period))
        self.tx_ratio_fall = int(np.round((self.DAC_MAXV * 2**16) / half_period) * -1)
        self.trigger_num = int(np.round(150 * 10000 * 0.001)) # Number of triggers per cycle
        self.rx_tri_limit = self.trigger_num
        self.rx_tri_mode = 1 # only consider plus trigger
        self.rx_tri_wait_time = int(np.round(1 * self['adc']['f_fabric'])) - 1 # wait plus trigger for 1us after minus trigger detected
        self.rx_tri_edge = 0
        self.rx_tri_threshold = self.DAC_MAXV / 2
        self.rx_dc_limit = int(np.round(150 * self.DC_RATE))  # 150ms * 500KHz
        self.rx_dc_en = 0
        self.rx_dc_rate = int(np.round((self['adc']['f_fabric'] * 1.0e+3) / self.DC_RATE)) - 1 # 500KHz
        
    def configure_connections(self, soc):
        super().configure_connections(soc)

        trace_result = soc.metadata.trace_forward(self.fullpath, self.DMA_TIME_PORT, ["axi_dma"], 1)
        block, _, _ = trace_result[0]
        self.dma_time = getattr(soc, block)

        trace_result = soc.metadata.trace_forward(self.fullpath, self.DMA_DC_PORT, ["axi_dma"], 1)
        block, _, _ = trace_result[0]
        self.dma_dc = getattr(soc, block)

        trace_result = soc.metadata.trace_forward(self.fullpath, self.DMA_GRAPHY_PORT, ["axi_dma"], 1)
        block, _, _ = trace_result[0]
        self.dma_graphy = getattr(soc, block)

    def set_cycle(self, cycle:int=1):
        """
        Set the number of cycles.
        :param cycle: Number of cycles.
        """
        if cycle < 1:
            raise RuntimeError("Cycle must be greater than 0.")
        
        self.cycle = cycle

    def set_waveform(self, rise_time_ms:int=150, fall_time_ms:int=50, 
                     max_scal:float=1.0, trigger_rate_hz:int=100000):
        """
        Set the waveform parameters.
        :param rise_time_ms: Rise time in ms.
        :param fall_time_ms: Fall time in ms.
        :param max_scal: Maximum scale (0-1).
        :param trigger_rate_hz: Trigger rate in Hz.
        """
        if fall_time_ms < 50:
            raise RuntimeError("fall_time_ms must be at least 50ms.")
        if not (0 <= max_scal <= 1):
            raise RuntimeError("max_scal must be between 0 and 1.")
        if trigger_rate_hz > 100000:
            raise RuntimeError("trigger_rate_hz must be at most 100000Hz.")
        self.trigger_num = int(np.round(rise_time_ms * trigger_rate_hz * 0.001))
        if self.trigger_num > self.TRIGGER_LIMIT:
            raise RuntimeError("The number of triggers per cycle exceeds the limit (%d)." % self.TRIGGER_LIMIT)

        self.cycle_period = (rise_time_ms + fall_time_ms) * 0.001
        rise_clk = int(np.round(rise_time_ms * 1000 * self['dac']['f_fabric']))
        fall_clk = int(np.round(fall_time_ms * 1000 * self['dac']['f_fabric']))
        self.tx_tag_fall = rise_clk - 1
        self.tx_period = rise_clk + fall_clk - 1
        ratio_rise = int(np.round((self.DAC_MAXV * max_scal * 2**16) / rise_clk))
        ratio_fall = (int(np.round((self.DAC_MAXV * max_scal * 2**16) / fall_clk)) * -1) # Negative for fall
        print(f'Set waveform: rise_clk={rise_clk}, fall_clk={fall_clk}, ratio_rise={ratio_rise}, ratio_fall={ratio_fall}.')
        self.tx_ratio_rise = ratio_rise
        self.tx_ratio_fall = ratio_fall
        # self.tx_ratio_rise = int(np.round((self.DAC_MAXV * max_scal * 2**16) / rise_clk)) # Positive for rise
        # self.tx_ratio_fall = (int(np.round((self.DAC_MAXV * max_scal * 2**16) / fall_clk)) * -1) # Negative for fall
        self.rx_tri_limit = self.trigger_num
        self.rx_dc_limit = int(np.round(rise_time_ms * self.DC_RATE))  # 150ms * 500KHz

    def set_ttl(self, ttl_bit:int=0, rise_ms:int=10, fall_ms:int=140):
        """
        Set the TTL duration time.
        :param ttl_bit: bit number (0-7).
        :param rise_ms: Rise time in ms.
        :param fall_ms: Fall time in ms.
        """
        if ttl_bit < 0 or ttl_bit > 7:
            raise RuntimeError("Tag must be between 0 and 7.")
        if rise_ms < 10:
            raise RuntimeError("rise_ms must be at least 10ms.")
        if fall_ms > 140:
            raise RuntimeError("fall_ms must be at most 140ms.")
        
        rise_clk = int(np.round(rise_ms * 1000 * self['dac']['f_fabric']))
        fall_clk = int(np.round(fall_ms * 1000 * self['dac']['f_fabric']))
        self.ttl[ttl_bit]['rise'] = rise_clk - 1
        self.ttl[ttl_bit]['fall'] = fall_clk - 1
        print(f'Set TTL{ttl_bit}: rise_clk={rise_clk}, fall_clk={fall_clk}.')
        # setattr(self, "tx_ttl%d_rise"%(ttl_bit), self.ttl[ttl_bit]['rise'])
        # setattr(self, "tx_ttl%d_fall"%(ttl_bit), self.ttl[ttl_bit]['fall'])

    def set_trigger_mode(self, mode:int=1, wait_time_us:float=1):
        """
        Set the trigger mode.
        :param mode: Trigger mode (0: both triggers(plus and minus), 1: plus trigger only).
        :param wait_time_ms: Wait time in ms after negative edge detected.
        """
        if mode not in [0, 1]:
            raise RuntimeError("Mode must be 0, or 1.")

        self.rx_tri_mode = mode
        self.rx_tri_wait_time = int(np.round(wait_time_us * self['adc']['f_fabric'])) - 1
    
    def set_threshold(self, threshold:float=0.5):
        """
        Set the ADC threshold.
        :param threshold: Threshold value (0-1).
        """
        if not (0 <= threshold <= 1):
            raise RuntimeError("Threshold must be between 0 and 1.")

        self.rx_tri_threshold = int(np.round(self.DAC_MAXV * threshold))

    def get_state(self):
        """
        Get the tomography state.
        :return: State of the tomography.
        """
        state = self.rx_state
        error = (state >> 4) != 0
        cycle = state & 0xF
        return error, cycle

    def start_tomography(self, cycle:int=1):
        """
        Start the tomography.
        """
        if cycle < 1:
            raise RuntimeError("Cycle must be greater than 0.")
        
        if not self.thread.is_alive():
            self.__start_thread()

        if not self.done_flag.is_set():
            # self.stop_flag.set()
            self.done_flag.wait()
            # self.stop_flag.clear()

        if not self.data_queue.empty():
            self.poll_data(totaltime=-1, timeout=0.1)

        self.done_flag.clear()
        # self.set_cycle(cycle)
        self.par_queue.put(cycle)

    def poll_data(self, totaltime=0.5, timeout=0.1):
        """
        Poll the tomography data.
        :param total_time: Total time to poll data in seconds.
        :param time_out: Timeout for each poll in seconds.
        :return: List of dictionaries containing time, dc, and graphy data.
        """
        time_end = time.time() + totaltime
        new_data = []
        while (totaltime < 0) or (time.time() < time_end):
            try:
                e = self.error_queue.get(block=False)
                return False, e
            except Empty:
                pass
            try:
                # data = self.data_queue.get(block=True, timeout=timeout)
                with self.lock:
                    type, buf_index, tag_cnt, data_cnt, dc_cnt = self.data_queue.get(block=True, timeout=timeout)
                    if type == 'DC':
                        dc_buf = np.frombuffer(self.dma_dc_buf[buf_index][:dc_cnt], dtype=np.int16)
                        data = dc_buf.copy()
                    else:  # 'AC'
                        tag_buf = np.frombuffer(self.dma_tag_buf[buf_index][:tag_cnt], dtype=np.uint32)
                        graphy_buf = np.frombuffer(self.dma_graphy_buf[buf_index][:data_cnt], dtype=np.int16)
                        data = tag_buf.copy(), graphy_buf.copy()
                    package = {'type': type, 'data': data}
                new_data.append(package)
            except Empty:
                break
        return True, new_data

    def __start_thread(self):
        """
        Start the tomography thread.
        """
        # self.stop_flag = Event()
        self.done_flag = Event()
        self.done_flag.set()
        self.par_queue = Queue()
        self.data_queue = Queue()
        self.error_queue = Queue()
        self.thread = self.thread = Thread(target=self.__run_tomography, daemon=True)
        self.thread.start()
    
    def __data_process(self, cycle, tag_len, graphy_len):
        """
        The data processing function.
        """
        assert cycle > 0 and cycle <= 2, "Cycle must be 1 or 2."
        error = False
        cycle_cnt = 0
        self.cycle = cycle
        # for i in range(8):
        #     setattr(self, "tx_ttl%d_rise"%(i), self.ttl[i]['rise'])
        #     setattr(self, "tx_ttl%d_fall"%(i), self.ttl[i]['fall'])
        self.rx_tri_limit = self.trigger_num
        self.rx_dc_limit = 0
        self.rx_dc_en = 0  # Disable DC acquisition
        t_start = time.time()
        with self.lock:
            self.start = 1
            error, cycle_cnt = self.get_state()
            for i in range(cycle):
                if (tag_len > 0) and (graphy_len > 0):
                    self.dma_time.recvchannel.transfer(self.dma_tag_buf[i], nbytes=int(tag_len))
                    self.dma_graphy.recvchannel.transfer(self.dma_graphy_buf[i], nbytes=int(graphy_len))
                    self.dma_time.recvchannel.wait()
                    self.dma_graphy.recvchannel.wait()
                while cycle_cnt == i:
                    error, cycle_cnt = self.get_state()
                if error:
                    self.error_queue.put(f"Error occurred during AC acquisition.")
                data = 'AC', i, self.rx_tag_cnt, (self.rx_data_cnt * self.INTERPOLATION), 0
                self.data_queue.put(data)
        dt = time.time() - t_start
        while (dt < (self.cycle_period * cycle)) or (not self.data_queue.empty()):
            time.sleep(0.001)
            dt = time.time() - t_start
        print(f'AC acquisition time: {dt:.3f}s for {cycle} cycles.')
    
    def __dc_process(self, cycle, dc_limit):
        """
        The data processing function.
        """
        assert cycle > 0 and cycle <= 2, "Cycle must be 1 or 2."
        dc_len = dc_limit * 2  # 2 bytes for each DC point
        error = False
        cycle_cnt = 0
        self.cycle = cycle
        for i in range(8):
            setattr(self, "tx_ttl%d_rise"%(i), (2**32 - 1)) # Disable TTL during DC acquisition
            setattr(self, "tx_ttl%d_fall"%(i), (2**32 - 1)) # Disable TTL during DC acquisition
        self.rx_tri_limit = 0 # Disable trigger acquisition
        self.rx_dc_limit = dc_limit
        self.rx_dc_en = 1  # Enable DC acquisition
        t_start = time.time()
        with self.lock:
            self.start = 1
            for i in range(cycle):
                self.dma_dc.recvchannel.transfer(self.dma_dc_buf[i], nbytes=int(dc_len))
                self.dma_dc.recvchannel.wait()
                while cycle_cnt == i:
                    error, cycle_cnt = self.get_state()
                if error:
                    self.error_queue.put(f"Error occurred during DC acquisition.")
                data = 'DC', i, self.rx_tag_cnt, (self.rx_data_cnt * self.INTERPOLATION), dc_limit
                self.data_queue.put(data)
        dt = time.time() - t_start
        while (dt < (self.cycle_period * cycle)) or (not self.data_queue.empty()):
            time.sleep(0.001)
            dt = time.time() - t_start
        print(f'DC acquisition time: {dt:.3f}s for {cycle} cycles.')
    
    def __run_tomography(self):
        """
        Run the tomography in a separate thread.
        """
        while True:
            try:
                while self.par_queue.empty():
                    time.sleep(0.01)  # Wait for a new cycle request
                cycle = self.par_queue.get(block=True)

                tag_len = self.trigger_num * 4 # 4 bytes for each time point
                graphy_len = self.trigger_num * 1024 * 2 # 2 bytes for each graphy point
                dc_limit = self.rx_dc_limit
                print(f'Set tomography for {cycle} cycles with tag_len={tag_len}, graphy_len={graphy_len}, dc_limit={dc_limit}.')

                # self.__dc_process(cycle=2, dc_limit=dc_limit)
                
                while cycle > 0:
                    print(f'Collecting AC data for {min(2, cycle)} cycles...')
                    if cycle >= 2:
                        for i in range(8):
                            setattr(self, "tx_ttl%d_rise"%(i), (2**32 - 1))
                            setattr(self, "tx_ttl%d_fall"%(i), (2**32 - 1))
                        self.__data_process(cycle=min(2, cycle), tag_len=0, graphy_len=0)
                    else:
                        for i in range(8):
                            setattr(self, "tx_ttl%d_rise"%(i), self.ttl[i]['rise'])
                            setattr(self, "tx_ttl%d_fall"%(i), self.ttl[i]['fall'])
                        self.__data_process(cycle=min(2, cycle), tag_len=tag_len, graphy_len=graphy_len)
                    cycle -= 2

                # print(f'Collecting DC data for {2} cycles...')
                # self.__dc_process(cycle=2, dc_limit=dc_limit)
                self.rx_dc_limit = dc_limit # Restore DC limit
            except Exception as e:
                self.error_queue.put(str(e))
            finally:
                self.done_flag.set()
                print("Tomography finished.")
