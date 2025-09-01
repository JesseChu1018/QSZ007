import Pyro4 
import os

class Client:
    def __init__(self):
        Pyro4.config.SERIALIZER = "pickle"
        Pyro4.config.PICKLE_PROTOCOL_VERSION=4
    
    def connect(self, ns_ip:str, ns_port:int, remote_name:str):
        try:
            ns = Pyro4.locateNS(host=ns_ip, port=ns_port)
        except Exception as e:
            raise RuntimeError("Error: Could not find the nameserver.")
        try:
             
            self.remote_obj = Pyro4.Proxy(ns.lookup(remote_name))
        except Exception as e:
            raise RuntimeError("Error: Could not find the remote object.")
    
    def upload_file(self, bitfile_name:str):
        """
        This method uploads the bitstream to the FPGA.
        bitfile_name: str
            The fullpath of the bitstream file in the client.
        """
        with open(bitfile_name, 'rb') as f:
            bitfile = f.read()
        file_name = os.path.basename(bitfile_name)
        status, error = self.remote_obj.upload_file(file_name, bitfile)
        if not status:
            raise RuntimeError(error)
    
    def reload_bitstream(self, bitfile_name:str):
        """
        This method reloads the bitstream to the FPGA.
        bitfile_name: str
            The filename of the bitstream file in the FPGA.
        """
        status, error = self.remote_obj.reload_bitstream(bitfile_name)
        if not status:
            raise RuntimeError(error)
        
    def set_cycle(self, ch:int=0, cycle:int=1):
        """
        This method sets the cycle for tomography measurement.
        ch: int
            The channel index, 0 or 1.
        cycle: int
            The number of cycles for the tomography measurement.
        """
        status, error = self.remote_obj.set_cycle(ch, cycle)
        if not status:
            raise RuntimeError(error)
    
    def set_waveform(self, ch:int=0, rise_time_ms:int=150, fall_time_ms:int=50, 
                     max_scal:float=1.0, trigger_rate_hz:int=100000):
        """
        This method sets the waveform parameters.
        ch: int
            The channel index, 0 or 1.
        rise_time_ms: int
            The rise time of the waveform in milliseconds.
        fall_time_ms: int
            The fall time of the waveform in milliseconds.
        max_scal: float
            The maximum scale of the waveform.
        trigger_rate_hz: int
            The trigger rate in Hz.
        """
        status, error = self.remote_obj.set_waveform(ch, rise_time_ms, fall_time_ms, max_scal, trigger_rate_hz)
        if not status:
            raise RuntimeError(error)
        
    def set_ttl(self, ch:int=0, ttl_bit:int=0, rise_ms:int=10, fall_ms:int=140):
        """
        This method sets the TTL parameters.
        ch: int
            The channel index, 0 or 1.
        ttl_bit: int
            The TTL bit to be set, 0-7.
        rise_ms: int
            The rise time of the TTL in milliseconds.
        fall_ms: int
            The fall time of the TTL in milliseconds.
        """
        status, error = self.remote_obj.set_ttl(ch, ttl_bit, rise_ms, fall_ms)
        if not status:
            raise RuntimeError(error)
        
    def set_threshold(self, ch:int=0, threshold:float=0.5):
        """
        This method sets the threshold for photon detection.
        ch: int
            The channel index, 0 or 1.
        threshold: float
            The threshold value for photon detection.
        """
        status, error = self.remote_obj.set_threshold(ch, threshold)
        if not status:
            raise RuntimeError(error)
        
    def start_tomography(self, ch:int=0, cycle:int=1):
        """
        This method starts the tomography measurement.
        ch: int
            The channel index, 0 or 1.
        cycle: int
            The number of cycles for the tomography measurement.
        """
        status, error = self.remote_obj.start_tomography(ch, cycle)
        if not status:
            raise RuntimeError(error)
        
    def poll_data(self, ch:int=0, totaltime=0.5, timeout=1):
        """
        This method polls the data from the remote object.
        ch: int
            The channel index, 0 or 1.
        totaltime: float
            The total time to poll data in seconds. If negative, it will poll indefinitely until stopped.
        timeout: float
            The timeout for each polling attempt in seconds.
        Returns:
            A list of data polled from the remote object.
        """
        status, new_data = self.remote_obj.poll_data(ch, totaltime, timeout)
        if not status:
            raise RuntimeError(new_data)
        return new_data
    

