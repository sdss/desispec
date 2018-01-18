# Script for generating QA for a full exposure
from __future__ import absolute_import, division

from lvmutil.log import get_logger
import argparse

from lvmspec.qa import __offline_qa_version__

def parse(options=None):
    parser = argparse.ArgumentParser(description="Generate Exposure Level QA [v{:s}]".format(__offline_qa_version__))
    parser.add_argument('--expid', type = int, required=True, help='Exposure ID')
    parser.add_argument('--qatype', type = str, required=True,
                        help="Type of QA to generate [fiberflat]")
    parser.add_argument('--channels', type=str, help="List of channels to include. Default = b,r,z]")
    parser.add_argument('--reduxdir', type = str, default = None, metavar = 'PATH',
                        help = 'Override default path ($LVM_SPECTRO_REDUX/$SPECPROD) to processed data.')


    args = None
    if options is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(options)
    return args


def main(args) :

    from lvmspec.io import meta
    from lvmspec.qa.qa_plots import exposure_fiberflat
    log=get_logger()

    log.info("starting")
    if args.reduxdir is None:
        specprod_dir = meta.specprod_root()
    else:
        specprod_dir = args.reduxdir
    if args.channels is None:
        channels = ['b','r','z']
    else:
        channels = [iarg for iarg in args.channels.split(',')]

    # Fiber QA
    if args.qatype == 'fibermap':
        for channel in channels:
            exposure_fiberflat(channel, args.expid, 'meanflux')



