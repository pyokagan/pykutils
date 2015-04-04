from setuptools import setup


setup(name='pykutils',
      version='0',
      description="pyokagan's personal utilities collection",
      long_description=open('README.rst').read(),
      url='https://github.com/pyokagan/pykutils',
      author='Paul Tan',
      author_email='pyokagan@pyokagan.name',
      license='MIT',
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Environment :: Console',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: MIT License',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3.2',
          'Programming Language :: Python :: 3.3',
          'Programming Language :: Python :: 3.4',
      ],
      keywords='',
      packages=['pykutils'],
      entry_points={
          'console_scripts': [
              'btrup=pykutils.btrup:main',
          ]
      })
