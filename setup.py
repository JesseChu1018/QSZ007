from setuptools import setup, find_packages
from Cython.Build import cythonize
setup(
    name='qrng',
    version='0.1.0',
    author='Quantaser Photonics Co. Ltd.',
    author_email='tina@quantaser.com',
    description='Quantaser QRNG project.',
    install_requires =['pynq==2.7.0',
                       "numpy==1.21.6",
                       "matplotlib==3.1.2",
                       "Pyro4==4.82"],
    packages=['qrng'],
    package_data={'qrng': ['*.hwh', '*.bit']},
    ext_modules=cythonize("qrng/*.py", compiler_directives={'language_level' : "3"})
)