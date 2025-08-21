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
                 'tx_period': 1,
                 'tx_polarity': 2,
                 'tx_tag_fall': 3,
                 'tx_tag_ttl0': 4,
                 'tx_tag_ttl1': 5,
                 'tx_tag_ttl2': 6,
                 'tx_tag_ttl3': 7,
                 'tx_tag_ttl4': 8,
                 'tx_tag_ttl5': 9,
                 'tx_tag_ttl6': 10,
                 'tx_tag_ttl7': 11,
                 'tx_ratio_rise': 12,
                 'tx_ratio_fall': 13,
                 'rx_state': 14,
                 'rx_tri_cnt': 15,
                 'rx_data_cnt': 16,
                 'rx_tri_mode': 17,
                 'rx_threshold': 18,}
    
    # Name of the output connect to RFDC.
    DAC_RFDC_PORT = 'M0_DAC'
    # Name of the input connect to RFDC.
    ADC_RFDC_PORT = 'S0_ADC'
    DMA_TIME_PORT = 'M0_ADC'
    DMA_DC_PORT = 'M1_ADC'
    DMA_GRAPHY_PORT = 'M2_ADC'

    def __init__(self, description):
        super().__init__(description)
        # Generics
        self.INTERPOLATION = int(description['parameters']['INTERPOLATION'])
        self.TRIGGER_LIMIT = int(description['parameters']['TRIGGER_LIMIT'])

        self.dma_time_buf = allocate(shape=(self.TRIGGER_LIMIT+1), dtype=np.uint32)
        self.dma_dc_buf = allocate(shape=(self.TRIGGER_LIMIT+1) * self.INTERPOLATION, dtype=np.int16)
        self.dma_graphy_buf = allocate(shape=(self.TRIGGER_LIMIT * 1024) + self.INTERPOLATION, dtype=np.int16)

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
        half_period = int(np.round(150 * 1000 * self['dac']['f_fabric']))  # 150ms
        # Default registers.
        self.start = 0
        self.tx_period = half_period * 2  # 300ms
        self.tx_polarity = 0
        self.tx_tag_fall = half_period
        for i in range(8):
            setattr(self, "tx_tag_ttl%d"%(i), half_period)  # 150ms
        self.tx_ratio_rise = int(np.round((self.DAC_MAXV * 2**16) / half_period))
        self.tx_ratio_fall = int(np.round((self.DAC_MAXV * 2**16) / half_period))
        self.rx_tri_mode = 0
        self.rx_threshold = self.DAC_MAXV / 2

    def set_waveform(self, fall_time_ms:int=10, max_scal:float=1.0):
        """
        Set the waveform parameters.
        :param fall_time_ms: Fall time in milliseconds.
        :param max_scal: Max scal (0-1).
        """
        if fall_time_ms < 0:
            raise RuntimeError("Fall time must be non-negative.")
        if not (0 <= max_scal <= 1):
            raise RuntimeError("max_scal must be between 0 and 1.")

        rise_clk = int(np.round(150 * 1000 * self['dac']['f_fabric']))
        fall_clk = int(np.round(fall_time_ms * 1000 * self['dac']['f_fabric']))
        self.tx_period = rise_clk + fall_clk
        self.tx_ratio_rise = int(np.round((self.DAC_MAXV * max_scal * 2**16) / rise_clk)) # Positive for rise
        self.tx_ratio_fall = (int(np.round((self.DAC_MAXV * max_scal * 2**16) / fall_clk)) * -1) # Negative for fall

    def set_ttl_tag(self, ttl_bit:int=0, time_ms:int=150):
        """
        Set the TTL tag time.
        :param ttl_bit: bit number (0-7).
        :param time_ms: Time in microseconds.
        """
        if ttl_bit < 0 or ttl_bit > 7:
            raise RuntimeError("Tag must be between 0 and 7.")
        if time_ms < 0:
            raise RuntimeError("Time must be non-negative.")
        
        setattr(self, "tx_tag_ttl%d"%(ttl_bit), int(np.round(time_ms * 1000 * self['dac']['f_fabric'])))

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
    
    def get_data(self):
        """
        Get the tomography data.
        :return: List of dictionaries containing time, dc, and graphy data.
        """
        tri_cnt = self.rx_tri_cnt
        data_cnt = self.rx_data_cnt
        if tri_cnt == 0:
            return []
        
        time_len = (tri_cnt + 1) * 4 # 4 bytes for each time point
        dc_len = (tri_cnt + 1) * self.INTERPOLATION * 2 # 2 bytes for each DC point
        graphy_len = (data_cnt + 1) * self.INTERPOLATION * 2 # 2 bytes for each graphy point

        self.dma_time.recvchannel.transfer(self.dma_time_buf, nbytes=int(time_len))
        self.dma_dc.recvchannel.transfer(self.dma_dc_buf, nbytes=int(dc_len))
        self.dma_graphy.recvchannel.transfer(self.dma_graphy_buf, nbytes=int(graphy_len))
        
        self.dma_time.recvchannel.wait()
        self.dma_dc.recvchannel.wait()
        self.dma_graphy.recvchannel.wait()

        total_data = []
        for i in range(tri_cnt):
            time_data = self.dma_time_buf[i] / (self['adc']['f_fabric'] * 1000) # Convert to ms
            
            dc_data = self.dma_dc_buf[i * self.INTERPOLATION:(i + 1) * self.INTERPOLATION]
            dc_data = np.frombuffer(dc_data, dtype=np.int16)
            
            start_index = i * 1024
            if i > 0:
                delta_time = self.dma_time_buf[i] - self.dma_time_buf[i - 1]
                if delta_time < 1024:
                    start_index -= (1024 - delta_time)
            end_index = start_index + 1024
            graphy_data = self.dma_graphy_buf[start_index:end_index]
            graphy_data = np.frombuffer(graphy_data, dtype=np.int16)
            
            data = {'time': time_data, 'dc': dc_data.average(), 'graphy': graphy_data.copy()}
            total_data.append(data)
        
        return total_data

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
        self.par_queue.put(cycle)

    def poll_data(self, totaltime=0.5, timeout=1):
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
                raise RuntimeError(e)
            except Empty:
                pass
            try:
                data = self.data_queue.get(block=True, timeout=timeout)
                # if we stopped the readout while we were waiting for data, break out and return
                if self.done_flag.is_set():
                    print("tomography done")
                if self.stop_flag.is_set():
                    break
                new_data.append(data)
            except Empty:
                break
        return new_data

    def __start_thread(self):
        self.stop_flag = Event()
        self.done_flag = Event()
        self.done_flag.set()
        self.par_queue = Queue()
        self.data_queue = Queue()
        self.error_queue = Queue()
        self.thread = self.thread = Thread(target=self.__run_tomography, daemon=True)
        self.thread.start()
    
    def __run_tomography(self):
        """
        Run the tomography in a separate thread.
        """
        while True:
            try:
                while self.par_queue.empty():
                    time.sleep(0.01)  # Wait for a new cycle request
                print("Starting tomography cycle")
                cycle_target = self.par_queue.get(block=True)
                ctcle_cnt = 0
                self.start = 1
                while ctcle_cnt < cycle_target:
                    if self.stop_flag.is_set():
                        break
                    error, cycle = self.get_state()
                    if cycle != (ctcle_cnt & 0xF):
                        if error:
                            self.error_queue.put("Error in tomography state.")
                            break
                        else:
                            data = self.get_data()  # Get the data for the previous cycle
                            if data == []:
                                print("No data received for cycle %d" % ctcle_cnt)
                            self.data_queue.put(data)
                        ctcle_cnt += 1
                    else:
                        time.sleep(0.01)  # Wait for the next cycle
            except Exception as e:
                self.error_queue.put(str(e))
            finally:
                self.start = 0
                self.done_flag.set()
