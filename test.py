import sys
# import matplotlib.pyplot as plt
from client import QRNGClient
import numpy as np

client = QRNGClient()
client.connect("192.168.2.99", 8888, "qrng")
# client.reload_bitstream("qrng.bit")

# lient.upload_file("D:\\github_quantaser\\QSS024A_QRNG\\qrng\\qrng1.bit")
# client.upload_file("D:\\github_quantaser\\QSS024A_QRNG\\qrng\\qrng1.hwh")
# client.reload_bitstream("qrng1.bit")
client.start_sampler(1024, 1, True, True)
data = client.get_sampler_data()
client.set_rng_paramter('out_data0', 399) # for SNR in ip0 setting
print("port0 = ", client.get_rng_paramter('out_data0'))
client.start_qrng('out_data1', 1 , True, 32768)
trimbit0 = client.get_rng_paramter('in_data0')

client.start_qrng('out_data1', 1 , True, 32768)
data = client.get_qrng_data(32768)
for i in range(9):
    client.start_sampler(32768, 1, False, True)
    data = np.append(data, client.get_qrng_data(32768))
print(len(data))




