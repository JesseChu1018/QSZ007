import Pyro4 
import os

class QRNGClient:
    def __init__(self):
        Pyro4.config.SERIALIZER = "pickle"
        Pyro4.config.PICKLE_PROTOCOL_VERSION=4
    
    def connect(self, ns_ip:str, ns_port:int, remote_name:str):
        try:
            ns = Pyro4.locateNS(host=ns_ip, port=ns_port)
        except Exception as e:
            raise RuntimeError("Error: Could not find the nameserver.")
        try:
             
            self.qrng = Pyro4.Proxy(ns.lookup(remote_name))
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
        status, error = self.qrng.upload_file(file_name, bitfile)
        if not status:
            raise RuntimeError(error)
    
    def reload_bitstream(self, bitfile_name:str):
        """
        This method reloads the bitstream to the FPGA.
        bitfile_name: str
            The filename of the bitstream file in the FPGA.
        """
        status, error = self.qrng.reload_bitstream(bitfile_name)
        if not status:
            raise RuntimeError(error)
    
    def start_sampler(self, length:int, ratio:int, rawdata:bool=False, dummy_ip_dma:bool=False):
        """
        This method starts the sampler of the QRNG.
        length: int
            The length of the data.
        ratio: int
            The frequncy divider ratio of the sampler.
        """
        status, error = self.qrng.start_sampler(length, ratio, rawdata, dummy_ip_dma)
        print("start_sampler", status, error)
        if not status:
            raise RuntimeError(error)
            
    def get_sampler_data(self):
        """
        This method gets the sampler data from the QRNG.
        length: int
            The length of the data.
        ratio: int
            The frequncy divider ratio of the sampler.
        """
        status , data = self.qrng.get_sampler_data()
        print("get_sampler_data", status, data)
        if not status:
            raise RuntimeError(data)
        return data
    
    def set_rng_paramter(self,port:str, value:int):
        """
        This method sets the parameter of the qrng ip
        channel: int
            The channel number of the qrng ip.
        port: str
            The parameter name of the qrng ip.
        value: int
            The value of the parameter.
        """
        status , error = self.qrng.set_rng_paramter(port, value)
        if not status:
            raise RuntimeError(error)
        
    
    def get_rng_paramter(self, port:str):
        """
        This method gets the parameter from qrng IP.
        channel: int
            The channel number of the qrng ip.
        port: str
            The parameter name of the qrng ip.
        """
        status, data = self.qrng.get_rng_paramter(port)
        if not status:
            raise RuntimeError(data)
        return data
    
    def start_qrng(self, port_name:str, value:int, dataoutput:bool =False, data_length:int = 0, ratio:int=1):
        """
        This method starts the QRNG.
        port_name: str
            The port name of the qrng ip for start
        value: int
            The value of the port to start the qrng.
        dataoutput: bool
            The data output flag. True for enable the output of qrng data.
        data_length: int
            The length of the data. which must be multiple of 8.
        rawdata: bool
            The raw data flag. True for enable the sampler data output.
        """
        status, error = self.qrng.start_qrng(port_name, value, dataoutput, data_length, ratio)
        if not status:
            raise RuntimeError(error)
    
    def get_qrng_data(self, length:int):
        """
        This method gets the QRNG data.
        """
        status, data =self.qrng.get_qrng_data(length)
        if not status:
            raise RuntimeError(data)
        return data
    

