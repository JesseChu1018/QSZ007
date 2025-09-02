"""
Drivers for QRNG.
"""
from abc import ABC
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
                 'tri_limit': 1,
                 'cycle': 2,
                 'tx_period': 3,
                 'tx_tag_fall': 4,
                 'tx_ttl0_rise': 5,
                 'tx_ttl1_rise': 6,
                 'tx_ttl2_rise': 7,
                 'tx_ttl3_rise': 8,
                 'tx_ttl4_rise': 9,
                 'tx_ttl5_rise': 10,
                 'tx_ttl6_rise': 11,
                 'tx_ttl7_rise': 12,
                 'tx_ttl0_fall': 13,
                 'tx_ttl1_fall': 14,
                 'tx_ttl2_fall': 15,
                 'tx_ttl3_fall': 16,
                 'tx_ttl4_fall': 17,
                 'tx_ttl5_fall': 18,
                 'tx_ttl6_fall': 19,
                 'tx_ttl7_fall': 20,
                 'tx_ratio_rise': 21,
                 'tx_ratio_fall': 22,
                 'rx_state': 23,
                 'rx_tag_cnt': 24,
                 'rx_data_cnt': 25,
                 'rx_tri_mode': 26,
                 'rx_threshold': 27}
    
    # Name of the output connect to RFDC.
    DAC_RFDC_PORT = 'M0_DAC'
    # Name of the input connect to RFDC.
    ADC_RFDC_PORT = 'S0_ADC'
    DMA_TIME_PORT = 'M0_ADC'
    DMA_DC_PORT = 'M1_ADC'
    DMA_GRAPHY_PORT = 'M2_ADC'

    TRIGGER_LIMIT = 10000  # Maximum number of triggers per cycle.

    def __init__(self, description):
        super().__init__(description)
        # Generics
        self.INTERPOLATION = int(description['parameters']['INTERPOLATION'])

        self.graphy_clk = (1024 // self.INTERPOLATION)

        self.dma_time_buf = []
        self.dma_dc_buf = []
        self.dma_graphy_buf = []
        for i in range(2):
            self.dma_time_buf.append(allocate(shape=(self.TRIGGER_LIMIT), dtype=np.uint32))
            self.dma_dc_buf.append(allocate(shape=self.TRIGGER_LIMIT * self.INTERPOLATION, dtype=np.int16))
            self.dma_graphy_buf.append(allocate(shape=(self.TRIGGER_LIMIT * 1024), dtype=np.int16))

        self.__start_thread()
        
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

    def configure(self):
        super().configure()
        ttl_rise = int(np.round(10 * 1000 * self['dac']['f_fabric']))  # 10ms
        ttl_fall = int(np.round(140 * 1000 * self['dac']['f_fabric']))  # 140ms
        half_period = int(np.round(150 * 1000 * self['dac']['f_fabric']))  # 150ms
        # Default registers.
        self.trigger_num = int(np.round(150 * 10000 * 0.001)) # Number of triggers per cycle
        self.tri_limit = self.trigger_num
        self.cycle = 1
        self.tx_period = (half_period * 2) - 1  # 300ms
        self.tx_tag_fall = half_period - 1  # 150ms
        for i in range(8):
            setattr(self, "tx_ttl%d_rise"%(i), ttl_rise - 1)  # 150ms
            setattr(self, "tx_ttl%d_fall"%(i), ttl_fall - 1)  # 150ms
        self.tx_ratio_rise = int(np.round((self.DAC_MAXV * 2**16) / half_period))
        self.tx_ratio_fall = int(np.round((self.DAC_MAXV * 2**16) / half_period) * -1)
        self.rx_tri_mode = 0
        self.rx_threshold = self.DAC_MAXV / 2

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
        self.trigger_num = int(np.round(rise_time_ms * trigger_rate_hz * 0.001))
        if self.trigger_num > self.TRIGGER_LIMIT:
            raise RuntimeError("The number of triggers per cycle exceeds the limit (%d)." % self.TRIGGER_LIMIT)
        if fall_time_ms < 50:
            raise RuntimeError("fall_time_ms must be at least 50ms.")
        if not (0 <= max_scal <= 1):
            raise RuntimeError("max_scal must be between 0 and 1.")
        if trigger_rate_hz > 100000:
            raise RuntimeError("trigger_rate_hz must be at most 100000Hz.")

        rise_clk = int(np.round(rise_time_ms * 1000 * self['dac']['f_fabric']))
        fall_clk = int(np.round(fall_time_ms * 1000 * self['dac']['f_fabric']))
        self.tri_limit = self.trigger_num
        self.tx_tag_fall = rise_clk - 1
        self.tx_period = rise_clk + fall_clk - 1
        self.tx_ratio_rise = int(np.round((self.DAC_MAXV * max_scal * 2**16) / rise_clk)) # Positive for rise
        self.tx_ratio_fall = (int(np.round((self.DAC_MAXV * max_scal * 2**16) / fall_clk)) * -1) # Negative for fall

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
        setattr(self, "tx_ttl%d_rise"%(ttl_bit), rise_clk - 1)
        setattr(self, "tx_ttl%d_fall"%(ttl_bit), fall_clk - 1)

    def set_threshold(self, threshold:float=0.5):
        """
        Set the ADC threshold.
        :param threshold: Threshold value (0-1).
        """
        if not (0 <= threshold <= 1):
            raise RuntimeError("Threshold must be between 0 and 1.")

        self.rx_threshold = int(np.round(self.DAC_MAXV * threshold))

    def get_state(self):
        """
        Get the tomography state.
        :return: State of the tomography.
        """
        state = self.rx_state
        error = (state >> 4) != 0
        cycle_cnt = state & 0xF
        return error, cycle_cnt

    def start_tomography(self, cycle:int=1):
        """
        Start the tomography.
        """
        if cycle < 1:
            raise RuntimeError("Cycle must be greater than 0.")
        
        if not self.thread.is_alive():
            self.__start_thread()

        if not self.done_flag.is_set():
            self.stop_flag.set()
            self.done_flag.wait()
            self.stop_flag.clear()

        if not self.data_queue.empty():
            self.poll_data(totaltime=-1, timeout=0.1)

        self.done_flag.clear()
        self.set_cycle(cycle)
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
                data = self.data_queue.get(block=True, timeout=timeout)
                # if we stopped the readout while we were waiting for data, break out and return
                if self.stop_flag.is_set():
                    break
                new_data.append(data)
            except Empty:
                break
        return True, new_data

    def __start_thread(self):
        """
        Start the tomography thread.
        """
        self.stop_flag = Event()
        self.done_flag = Event()
        self.done_flag.set()
        self.par_queue = Queue()
        self.data_queue = Queue()
        self.error_queue = Queue()
        self.thread = self.thread = Thread(target=self.__run_tomography, daemon=True)
        self.thread.start()
    
    def __data_acquire(self, cycle_cnt, time_len, dc_len, graphy_len):
        """
        Start data acquisition for the given cycle count.
        """
        buf_index = cycle_cnt % 2
        time_buf = self.dma_time_buf[buf_index]
        dc_buf = self.dma_dc_buf[buf_index]
        graphy_buf = self.dma_graphy_buf[buf_index]

        self.dma_time.recvchannel.transfer(time_buf, nbytes=int(time_len))
        self.dma_dc.recvchannel.transfer(dc_buf, nbytes=int(dc_len))
        self.dma_graphy.recvchannel.transfer(graphy_buf, nbytes=int(graphy_len))

    def __data_wait(self):
        """
        Wait for data acquisition to complete.
        """
        self.dma_time.recvchannel.wait()
        self.dma_dc.recvchannel.wait()
        self.dma_graphy.recvchannel.wait()

    def __data_process(self, cycle_cnt):
        """
        Process the acquired data for the given cycle count.
        :return: List of dictionaries containing time, dc, and graphy data.
        """
        tag_cnt = self.rx_tag_cnt
        data_cnt = self.rx_data_cnt

        buf_index = cycle_cnt % 2
        time_buf = self.dma_time_buf[buf_index]
        dc_buf = self.dma_dc_buf[buf_index]
        graphy_buf = self.dma_graphy_buf[buf_index]
        start_clk = 0
        total_data = []
        for i in range(tag_cnt):
            time_data = time_buf[i] / (self['adc']['fs'] * 1000) # Convert to ms
            
            dc_data = dc_buf[i * self.INTERPOLATION:(i + 1) * self.INTERPOLATION]
            dc_data = np.frombuffer(dc_data, dtype=np.int16)
            
            pre_quotient = now_quotient if i > 0 else 0
            now_quotient = time_buf[i] // self.INTERPOLATION
            residue_clk = time_buf[i] % self.INTERPOLATION
            delta_clk = (now_quotient - pre_quotient) if i > 0 else 0
            if delta_clk > self.graphy_clk:
                delta_clk = self.graphy_clk
            start_clk += delta_clk
            start_index = start_clk * self.INTERPOLATION + residue_clk
            end_index = start_index + 1000
            # if end_index > data_cnt:
            #     raise RuntimeError("Data index out of range.")
            graphy_data = graphy_buf[start_index:end_index]
            graphy_data = np.frombuffer(graphy_data, dtype=np.int16)

            data = {'time': time_data, 'dc': dc_data.mean(), 'graphy': graphy_data.copy()}
            total_data.append(data)
        
        return total_data
    
    def __run_tomography(self):
        """
        Run the tomography in a separate thread.
        """
        while True:
            try:
                while self.par_queue.empty():
                    time.sleep(0.01)  # Wait for a new cycle request
                print(f"Starting tomography.")
                cycle_target = self.par_queue.get(block=True)
                
                time_len = self.trigger_num * 4 # 4 bytes for each time point
                dc_len = self.trigger_num * self.INTERPOLATION * 2 # 2 bytes for each DC point
                graphy_len = self.trigger_num * 1024 * 2 # 2 bytes for each graphy point
                self.__data_acquire(0, time_len, dc_len, graphy_len)

                error = False
                cycle = 0
                ctcle_cnt = 0
                self.start = 1
                print(f"Tomography started for {cycle_target} cycles.")
                while ctcle_cnt < cycle_target:
                    self.__data_wait()
                    print(f"Cycle {ctcle_cnt + 1}/{cycle_target} done.")
                    while cycle == (ctcle_cnt & 0xF):
                        if self.stop_flag.is_set():
                            break
                        # time.sleep(0.001)  # Wait for the next cycle
                        error, cycle = self.get_state()
                    print(f"Cycle count: {cycle}, Error: {error}")
                    if self.stop_flag.is_set():
                        break
                    if error:
                        self.error_queue.put(f"Error occurred during tomography at cycle {ctcle_cnt}.")
                        # break # exit the cycle loop
                    ctcle_cnt += 1
                    if ctcle_cnt < cycle_target:
                        self.__data_acquire(ctcle_cnt, time_len, dc_len, graphy_len)
                    t1 = time.time()
                    data = self.__data_process(ctcle_cnt - 1)
                    dt = time.time() - t1
                    print(f"Processed cycle {ctcle_cnt} in {dt:.3f} seconds.")
                    self.data_queue.put(data)
            except Exception as e:
                self.error_queue.put(str(e))
            finally:
                self.start = 0
                self.done_flag.set()
                print("Tomography finished.")
