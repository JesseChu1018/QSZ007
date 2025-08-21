import Pyro4
import Pyro4.naming
from qrng.qrng import QRNG

def start_server(ns_host, ns_port=8888, proxy_name='qrng', **kwargs):
    """Initializes the QickSoc and starts a Pyro4 proxy server.

    Parameters
    ----------
    ns_host : str
        hostname or IP address of the nameserver
        if the nameserver is running on the QICK board, "localhost" is fine
    ns_port : int
        the port number you used when starting the nameserver
    proxy_name : str
        name for the QickSoc proxy
        multiple boards can use the same nameserver, but must have different names
    kwargs : optional named arguments
        any other options will be passed to the QickSoc constructor;
        see QickSoc documentation for details

    Returns
    -------
    """
    Pyro4.config.REQUIRE_EXPOSE = False
    Pyro4.config.SERIALIZER = "pickle"
    Pyro4.config.SERIALIZERS_ACCEPTED=set(['pickle'])
    Pyro4.config.PICKLE_PROTOCOL_VERSION=4

    print("looking for nameserver . . .")
    ns = Pyro4.locateNS(host=ns_host, port=ns_port)
    print("found nameserver")

    # if we have multiple network interfaces, we want to register the daemon using the IP address that faces the nameserver
    host = Pyro4.socketutil.getInterfaceAddress(ns._pyroUri.host)
    # if the nameserver is running on the QICK, the above will usually return the loopback address - not useful

    daemon = Pyro4.Daemon(host=host)

    # if you want to use a different firmware image or set some initialization options, you would do that here
    qrng = QRNG(bitfile=kwargs.get('bitfile', None),download=True)
    print("initialized QICK")

    # register the QickSoc in the daemon (so the daemon exposes the QickSoc over Pyro4)
    # and in the nameserver (so the client can find the QickSoc)
    ns.register(proxy_name, daemon.register(qrng))

    # register in the daemon all the objects we expose as properties of the QickSoc
    # we don't register them in the nameserver, since they are only meant to be accessed through the QickSoc proxy
    # https://pyro4.readthedocs.io/en/stable/servercode.html#autoproxying
    # https://github.com/irmen/Pyro4/blob/master/examples/autoproxy/server.py

                    
    print("starting daemon")
    daemon.requestLoop() # this will run forever until interrupted

