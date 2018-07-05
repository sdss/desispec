"""
Regroup spectra by healpix
"""

from __future__ import absolute_import, division, print_function
import os, sys, time

import numpy as np

from desiutil.log import get_logger

from .. import io
from ..pixgroup import FrameLite, SpectraLite
from ..pixgroup import (get_exp2healpix_map, add_missing_frames,
        frames2spectra, update_frame_cache)

def parse(options=None):
    import argparse

    parser = argparse.ArgumentParser(usage = "{prog} [options]")
    parser.add_argument("--reduxdir", type=str,  help="input redux dir; overrides $DESI_SPECTRO_REDUX/$SPECPROD")
    parser.add_argument("--nights", type=str,  help="YEARMMDD to add")
    parser.add_argument("--nside", type=int,default=64,help="input spectra healpix nside")
    parser.add_argument("-o", "--outdir", type=str,  help="output directory")
    parser.add_argument("--mpi", action="store_true",
            help="Use MPI for parallelism")

    if options is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(options)

    return args

def main(args=None, comm=None):

    log = get_logger()

    if args is None:
        args = parse()

    login_node = ('NERSC_HOST' in os.environ) & \
                 ('SLURM_JOB_NAME' not in os.environ)

    if comm:
        rank = comm.rank
        size = comm.size
    elif args.mpi and not login_node: 
        from mpi4py import MPI
        comm = MPI.COMM_WORLD
        rank = comm.rank
        size = comm.size
    else:
        rank = 0
        size = 1

    if args.nights:
        nights = [int(night) for night in args.nights.split(',')]
    else:
        nights = None

    #- Get table NIGHT EXPID SPECTRO HEALPIX NTARGETS 
    t0 = time.time()
    exp2pix = get_exp2healpix_map(nights=nights, comm=comm,
                                  specprod_dir=args.reduxdir)
    assert len(exp2pix) > 0
    if rank == 0:
        dt = time.time() - t0
        log.debug('Exposure to healpix mapping took {:.1f} sec'.format(dt))
        sys.stdout.flush()

    allpix = sorted(set(exp2pix['HEALPIX']))
    mypix = np.array_split(allpix, size)[rank]
    log.info('Rank {} will process {} pixels'.format(rank, len(mypix)))
    sys.stdout.flush()

    frames = dict()
    for pix in mypix:
        iipix = np.where(exp2pix['HEALPIX'] == pix)[0]
        ntargets = np.sum(exp2pix['NTARGETS'][iipix])
        log.info('Rank {} pix {} with {} targets on {} spectrograph exposures'.format(
            rank, pix, ntargets, len(iipix)))
        sys.stdout.flush()
        framekeys = list()
        for i in iipix:
            night = exp2pix['NIGHT'][i]
            expid = exp2pix['EXPID'][i]
            spectro = exp2pix['SPECTRO'][i]
            for band in ['b', 'r', 'z']:
                camera = band + str(spectro)
                framefile = io.findfile('cframe', night, expid, camera,
                        specprod_dir=args.reduxdir)
                if os.path.exists(framefile):
                    framekeys.append((night, expid, camera))
                else:
                    #- print warning if file is missing, but proceed;
                    #- will use add_missing_frames later.
                    log.warning('missing {}; will use blank data'.format(framefile))

        #- Identify any frames that are already in pre-existing output file
        specfile = io.findfile('spectra', nside=args.nside, groupname=pix,
                specprod_dir=args.reduxdir)
        if args.outdir:
            specfile = os.path.join(args.outdir, os.path.basename(specfile))

        oldspectra = None
        if os.path.exists(specfile):
            oldspectra = SpectraLite.read(specfile)
            fm = oldspectra.fibermap
            for night, expid, spectro in set(zip(fm['NIGHT'], fm['EXPID'], fm['SPECTROID'])):
                for band in ['b', 'r', 'z']:
                    camera = band + str(spectro)
                    if (night, expid, camera) in framekeys:
                        framekeys.remove((night, expid, camera))

        if len(framekeys) == 0:
            log.info('pix {} already has all exposures; moving on'.format(pix))
            continue

        #- Load new frames to add
        log.info('pix {} has {} frames to add'.format(pix, len(framekeys)))
        update_frame_cache(frames, framekeys, specprod_dir=args.reduxdir)

        #- add any missing frames
        add_missing_frames(frames)

        #- convert individual FrameLite objects into SpectraLite
        newspectra = frames2spectra(frames, pix)

        #- Combine with any previous spectra if needed
        if oldspectra:
            spectra = oldspectra + newspectra
        else:
            spectra = newspectra

        #- Write new spectra file
        header = dict(HPXNSIDE=args.nside, HPXPIXEL=pix, HPXNEST=True)
        spectra.write(specfile, header=header)
    
    if rank == 0:
        dt = time.time() - t0
        log.info('Done in {:.1f} minutes'.format(dt/60))

