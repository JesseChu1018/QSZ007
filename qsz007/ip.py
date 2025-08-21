"""
Support classes for dealing with FPGA IP blocks.
"""
from pynq.overlay import DefaultIP
import numpy as np
import logging

class SocIp(DefaultIP):
    """
    Base class for firmware IP drivers.
    Registers are accessed as attributes.
    Configuration constants are accessed as dictionary items.
    """
    REGISTERS = {}

    def __init__(self, description):
        """
        Constructor method
        """
        super().__init__(description)
        # DefaultIP.__init__(self, description)
        # this block's unique identifier in the firmware
        self.fullpath = description['fullpath']
        # this block's type
        self.type = description['type'].split(':')[-2]
        self._cfg = {'type': self.type,
                    'fullpath': self.fullpath}
        # logger for messages associated with this block
        self.logger = logging.getLogger(self.type)

    def __setattr__(self, a, v):
        """
        Sets the arguments associated with a register

        :param a: Register specified by an offset value
        :type a: int
        :param v: value to be written
        :type v: int
        """
        try:
            index = self.REGISTERS[a]
            self.mmio.array[index] = np.uint32(v)
        except KeyError:
            super().__setattr__(a, v)

    def __getattr__(self, a):
        """
        Gets the arguments associated with a register

        :param a: register name
        :type a: str
        :return: Register arguments
        :rtype: *args object
        """
        try:
            index = self.REGISTERS[a]
            return self.mmio.array[index]
        except KeyError:
            return super().__getattribute__(a)

    def __getitem__(self, key):
        return self._cfg[key]
    
    def __setitem__(self, key, value):
        self._cfg[key] = value

    @property
    def cfg(self):
        return self._cfg
    
    def configure(self):
        pass

    def configure_connections(self, soc):
        self.soc = soc
        # trace the RFDC port this IP connects to

class Metadata:
    """
    Provides information about the connections between IP blocks, extracted from the HWH file.
    The HWH parser is very different between PYNQ 2.6/2.7 and 3.0+, so this class serves as a common interface.
    """
    def __init__(self, soc):
        # We will use the HWH parser to extract information about signal connections between blocks.
        # system graph object, if available
        self.systemgraph = None
        # root element of the HWH file
        self.xml = None
        # parsers for signals and busses, using system graph or XML as appropriate
        self.sigparser = None
        self.busparser = None

        if hasattr(soc, 'systemgraph'):
            # PYNQ 3.0 and higher have a "system graph"
            self.systemgraph = soc.systemgraph
            self.xml = soc.systemgraph._root
        else:
            self.sigparser = soc.parser
            # Since the HWH parser doesn't parse buses, we also make our own BusParser.
            self.xml = soc.parser.root
        # TODO: We shouldn't need to use BusParser for PYNQ 3.0, but we think there's a bug in how pynqmetadata handles axis_switch.
        self.busparser = BusParser(self.xml)

        self.timestamp = self.xml.get('TIMESTAMP')

    def trace_sig(self, blockname, portname):
        if self.systemgraph is not None:
            dests = self.systemgraph.blocks[blockname].ports[portname].destinations()
            result = []
            for port, block in dests.items():
                blockname = block.parent().name
                if blockname==self.systemgraph.name:
                    result.append([port])
                else:
                    result.append([blockname, port])
            return result

        return self._trace_net(self.sigparser, blockname, portname)

    def trace_bus(self, blockname, portname):
        return self._trace_net(self.busparser, blockname, portname)

    def _trace_net(self, parser, blockname, portname):
        """
        Find the block and port that connect to this block and port.
        If you expect to only get one block+port as a result, you can assign the result to ((block, port),)

        :param parser: HWH parser object (from Overlay.parser, or BusParser)
        :param blockname: the IP block of interest
        :type blockname: string
        :param portname: the port we want to trace
        :type portname: string

        :return: a list of [block, port] pairs, or just [port] for ports of the top-level design
        :rtype: list
        """
        fullport = blockname+"/"+portname
        # the net connected to this port
        netname = parser.pins[fullport]
        if netname == '__NOC__':
            return []
        # get the list of other ports on this net, discard the port we started at and ILA ports
        return [x.split('/') for x in parser.nets[netname] if x != fullport and 'system_ila_' not in x]

    def get_fclk(self, blockname, portname):
        """
        Find the frequency of a clock port.

        :param parser: HWH parser object (from Overlay.parser, or BusParser)
        :param blockname: the IP block of interest
        :type blockname: string
        :param portname: the port we want to trace
        :type portname: string

        :return: frequency in MHz
        :rtype: float
        """
        xmlpath = "./MODULES/MODULE[@FULLNAME='/{0}']/PORTS/PORT[@NAME='{1}']".format(
            blockname, portname)
        port = self.xml.find(xmlpath)
        return float(port.get('CLKFREQUENCY'))/1e6

    def get_param(self, blockname, parname):
        """
        Find the value of an IP parameter. This works for all IPs, including those that do not show up in ip_dict because they're not addressable.

        :param parser: HWH parser object (from Overlay.parser, or BusParser)
        :param blockname: the IP block of interest
        :type blockname: string
        :param parname: the parameter of interest
        :type parname: string

        :return: parameter value
        :rtype: string
        """
        xmlpath = "./MODULES/MODULE[@FULLNAME='/{0}']/PARAMETERS/PARAMETER[@NAME='{1}']".format(
            blockname, parname)
        param = self.xml.find(xmlpath)
        return param.get('VALUE')

    def mod2type(self, blockname):
        if self.systemgraph is not None:
            return self.systemgraph.blocks[blockname].vlnv.name
        return self.busparser.mod2type[blockname]

    def mod2rev(self, blockname):
        return self.busparser.mod2rev[blockname]

    def trace_back(self, start_block, start_port, goal_types):
        """Follow the AXI-Stream bus backwards from a given block and port.
        Raise an error if none of the requested IP types is found.
        Return None if we run into an unconnected input port.

        Parameters
        ----------
        start_block : str
            The fullpath for the block to start tracing from.
        start_port : str
            The name of the input port to start tracing from,
        goal_types : list of str
            IP types that we're interested in.

        Returns
        -------
        str
            The fullpath for the block we found.
        str
            The output port on the block we found.
        str
            The IP type we found.
        """
        next_block = start_block
        next_port = start_port
        while True:
            trace_result = self.trace_bus(next_block, next_port)
            # if we hit an unconnected port, return False
            if len(trace_result)==0:
                return None
            # ((next_block, port),) = trace_result
            next_block = ""
            for i in range(len(trace_result[0]) - 1):
                next_block += trace_result[0][i] + '/'
            next_block = next_block[:-1]
            next_port = trace_result[0][-1]
            next_type = self.mod2type(next_block)
            if next_type in goal_types:
                return (next_block, next_port, next_type)
            elif next_type in ["axis_clock_converter", "axis_dwidth_converter", "axis_register_slice", "axis_broadcaster"]:
                next_port = 'S_AXIS'
            elif next_type == "axis_cdcsync_v1":
                # port name is of the form 'm4_axis' - follow corresponding input 's4_axis'
                next_port = 's' + next_port[1:]
            elif next_type == "sg_translator":
                next_port = 's_tproc_axis'
            elif next_type == "axis_resampler_2x1_v1":
                next_port = 's_axis'
            elif next_type == 'axis_sg_pulse':
                next_port = 'S_AXIS'
            else:
                raise RuntimeError("failed to trace back from %s - unrecognized IP block %s" % (start_block, next_block))

    def trace_forward(self, start_block, start_port, goal_types, block_number=-1):
        """Follow the AXI-Stream bus forwards from a given block and port.
        If a broadcaster is encountered, follow all outputs.
        Raise an error if ~=1 matching block is found.

        Parameters
        ----------
        start_block : str
            The fullpath for the block to start tracing from.
        start_port : str
            The name of the output port to start itracing from,
        goal_types : list of str
            IP types that we're interested in.
        block_number : int
            number of block to find.

        Returns
        -------
        str
            The fullpath for the block we found.
        str
            The input port on the block we found.
        str
            The IP type we found.
        """
        to_check = [(start_block, start_port)]
        found = []
        dead_ends = []

        while to_check:
            block, port = to_check.pop(0)
            # ((block, port),) = self.trace_bus(block, port)
            trace_result = self.trace_bus(block, port)
            block = ""
            for i in range(len(trace_result[0]) - 1):
                block += trace_result[0][i] + '/'
            block = block[:-1]
            port = trace_result[0][-1]
            block_type = self.mod2type(block)
            if block_type in goal_types:
                found.append((block, port, block_type))
            elif block_type == "axis_broadcaster":
                for iOut in range(int(self.get_param(block, 'NUM_MI'))):
                    to_check.append((block, "M%02d_AXIS" % (iOut)))
            elif block_type == "axis_clock_converter":
                to_check.append((block, "M_AXIS"))
            elif block_type == "axis_register_slice":
                to_check.append((block, "M_AXIS"))
            elif block_type == "axis_register_slice_nb":
                to_check.append((block, "m_axis"))
            elif block_type in ["smartconnect", "axi_interconnect"]:
                to_check.append((block, "M00_AXI"))
            elif block_type == "axis_data_fifo":
                to_check.append((block, "M_AXIS"))
            elif block_type == "fifo_generator":
                to_check.append((block, "M_AXIS"))
            elif block_type == "axis_chirp_mux":
                to_check.append((block, "M00_AXIS"))
            elif block_type == "axis_wf_mux":
                to_check.append((block, "M00_AXIS"))
            else:
                dead_ends.append(block)
        if (block_number != -1) and (len(found) != block_number):
            raise RuntimeError("traced forward from %s for one block of type %s, but found %s (and dead ends %s)" % (start_block, goal_types, found, dead_ends))
        return found

class BusParser:
    """Parses the HWH XML file to extract information on the buses connecting IP blocks.
    """
    def __init__(self, root):
        """
        Matching all the buses in the modules from the HWH file.
        This is essentially a copy of the HWH parser's match_nets() and match_pins(),
        but working on buses instead of signals.

        In addition, there's a map from module names to module types.

        :param root: HWH XML tree (from Overlay.parser.root)
        """
        self.nets = {}
        self.pins = {}
        self.mod2type = {}
        self.mod2rev = {}
        for module in root.findall('./MODULES/MODULE'):
            fullpath = module.get('FULLNAME').lstrip('/')
            self.mod2type[fullpath] = module.get('MODTYPE')
            self.mod2rev[fullpath] = int(module.get('COREREVISION'))
            for bus in module.findall('./BUSINTERFACES/BUSINTERFACE'):
                port = fullpath + '/' + bus.get('NAME')
                busname = bus.get('BUSNAME')
                self.pins[port] = busname
                if busname in self.nets:
                    self.nets[busname] |= set([port])
                else:
                    self.nets[busname] = set([port])


