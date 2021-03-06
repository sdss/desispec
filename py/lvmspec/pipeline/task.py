#
# See top-level LICENSE.rst file for Copyright information
#
# -*- coding: utf-8 -*-
"""
lvmspec.pipeline.task
===========================

Classes and functions for pipeline tasks
"""

from __future__ import absolute_import, division, print_function

import os
import sys
import re

from redrock.external.desi import rrdesi

from lvmutil.log import get_logger, DEBUG
from ..util import option_list
from ..parallel import default_nproc
from .. import io

from ..scripts import bootcalib
from ..scripts import specex
from ..scripts import extract
from ..scripts import fiberflat
from ..scripts import sky as skypkg
from ..scripts import stdstars
from ..scripts import fluxcalibration as fluxcal
from ..scripts import procexp
#from ..scripts import zfind

from .common import *
from .graph import *


class Worker(object):
    """
    Class representing the properties of a pipeline task worker.

    This is a base class that simply defines the API.  Each
    pipeline task worker has some default options, and a maximum
    number of UNIX processes it can use.  For example if extracting
    20 DESI bundles with at most one process per bundle, then it might
    return 20 for this number.  A worker has a "run" method which takes
    some inputs and produces some outputs.
    """
    def __init__(self):
        pass

    def max_nproc(self):
        """
        The maximum number of processes to use for this worker.
        """
        return 1

    def task_time(self):
        """
        An estimate in minutes for the time needed to complete one task
        using the maximum number of supported processes.  A zero value
        indicates no information.
        """
        return 0

    def default_options(self):
        """
        The default options dictionary for this worker.
        """
        return {}

    def run(self, grph, task, opts, comm=None):
        """
        Run the specified task.

        This runs the task, with the given options, using the
        specified communicator.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        raise RuntimeError("Worker base class should never run a task!")


class WorkerBootcalib(Worker):
    """
    Bootstrap the trace locations from arc images.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 30

    def default_options(self):
        opts = {}
        opts["trace-only"] = False
        opts["legendre-degree"] = 5
        opts["triplet-matching"] = True
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Run the bootstrap.

        The inputs to this step include *all* the arcs and flats for the
        night.  We sort them into the list of arcs and the list of
        flats, and simply choose the first one of each for now.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("Bootcalib worker should only be called with one process")

        log = get_logger()

        node = grph[task]
        night, obj = graph_night_split(task)
        (temp, band, spec) = graph_name_split(obj)
        cam = "{}{}".format(band, spec)

        arcs = []
        flats = []
        for input in node["in"]:
            inode = grph[input]
            if inode["flavor"] == "arc":
                arcs.append(input)
            elif inode["flavor"] == "flat":
                flats.append(input)
        if len(arcs) == 0:
            raise RuntimeError("no arc images found!")
        if len(flats) == 0:
            raise RuntimeError("no flat images found!")
        firstarc = sorted(arcs)[0]
        firstflat = sorted(flats)[0]

        arcpath = graph_path(firstarc)
        flatpath = graph_path(firstflat)
        outpath = graph_path(task)

        #qapath = io.findfile("qa_bootcalib", night=night, camera=cam, band=band, spectrograph=spec)

        # build list of options
        options = {}
        options["fiberflat"] = flatpath
        options["arcfile"] = arcpath
        #options["qafile"] = qapath
        options["outfile"] = outpath
        options.update(opts)
        optarray = option_list(options)

        # at debug level, log the equivalent commandline
        com = ["RUN", "desi_bootcalib"]
        com.extend(optarray)
        log.info(" ".join(com))

        args = bootcalib.parse(optarray)

        bootcalib.main(args)

        return


class WorkerSpecex(Worker):
    """
    Estimate the PSF from arc images.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 20

    def task_time(self):
        return 15 # in general faster but convergence slower for some realizations


    def default_options(self):
        opts = {}
        opts["trace-deg-wave"] = 7
        opts["trace-deg-x"] = 7
        opts["trace-prior-deg"] = 4

        # to get the lampline location, look in our path for specex
        # and use that install prefix to find the data directory.
        # if that directory does not exist, use a default NERSC
        # location.
        opts["lamplines"] = "/project/projectdirs/desi/software/edison/specex/specex-0.3.9/data/specex_linelist_desi.txt"
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exefile = os.path.join(path, "desi_psf_fit")
            if os.path.isfile(exefile) and os.access(exefile, os.X_OK):
                specexdir = os.path.join(path, "..", "data")
                opts["lamplines"] = os.path.join(specexdir, "specex_linelist_desi.txt")

        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Run the PSF estimation.

        This calls the MPI wrapper around calls to the (serial, but
        threaded) libspecex routines which do a per-bundle estimate.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        nproc = 1
        rank = 0
        if comm is not None:
            nproc = comm.size
            rank = comm.rank

        log = get_logger()

        node = grph[task]
        night, obj = graph_night_split(task)
        (temp, band, spec, expid) = graph_name_split(obj)
        cam = "{}{}".format(band, spec)

        pix = []
        inpsf = []
        for input in node["in"]:

            print("DEBUG  input , grph[input]=", input,grph[input])

            inode = grph[input]
            if inode["type"] == "psfboot":
                inpsf.append(input)
            elif inode["type"] == "pix":
                pix.append(input)
        if len(inpsf) != 1:
            raise RuntimeError("specex needs exactly one input psf file")
        if len(pix) != 1:
            raise RuntimeError("specex needs exactly one input image file")
        inpsffile = graph_path(inpsf[0])
        imgfile = graph_path(pix[0])
        outfile = graph_path(task)

        options = {}
        options["input-image"] = imgfile
        options["input-psf"]   = inpsffile
        options["output-psf"]  = outfile
        #if log.getEffectiveLevel() == DEBUG:
        #    options["debug"] = True

        if len(opts) > 0:
            extarray = option_list(opts)
            options["extra"] = " ".join(extarray)

        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        if rank == 0:
            com = ["RUN", "desi_compute_psf"]
            com.extend(optarray)
            log.info(" ".join(com))

        args = specex.parse(optarray)
        specex.main(args, comm=comm)

        return


class WorkerSpecexCombine(Worker):
    """
    Combine multiple PSF estimates into one.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 2 # fast


    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Run the PSF combining.

        This is a serial call to libspecex to combine the PSF files.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("PSFCombine worker should only be called with one process")

        log = get_logger()

        node = grph[task]

        outfile = graph_path(task)
        infiles = []
        for input in node["in"]:
            infiles.append(graph_path(input))

        specex.mean_psf(infiles, outfile)

        return


class WorkerSpecter(Worker):
    """
    Extract a frame using Specter"s ex2d.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 20 # 20 bundles per camera

    def task_time(self):
        return 15 # 8 minute per bundle of 25 fibers on edison, but can be slower


    def default_options(self):
        opts = {}
        opts["regularize"] = 0.0
        opts["nwavestep"] = 50
        opts["verbose"] = False
        opts["wavelength_b"] = "3579.0,5939.0,0.8"
        opts["wavelength_r"] = "5635.0,7731.0,0.8"
        opts["wavelength_z"] = "7445.0,9824.0,0.8"
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Run the extraction.

        This calls the MPI wrapper around calls to ex2d, assigning
        one or more bundles to each process.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        nproc = 1
        rank = 0
        if comm is not None:
            nproc = comm.size
            rank = comm.rank

        log = get_logger()

        node = grph[task]
        night, obj = graph_night_split(task)
        (temp, band, spec, expid) = graph_name_split(obj)
        cam = "{}{}".format(band, spec)

        pix = []
        psf = []
        fm = []
        band = None
        for input in node["in"]:
            inode = grph[input]
            if inode["type"] == "psfnight":
                psf.append(input)
            elif inode["type"] == "pix":
                pix.append(input)
                band = inode["band"]
            elif inode["type"] == "fibermap":
                fm.append(input)
        if len(psf) != 1:
            raise RuntimeError("extraction needs exactly one psfnight file")
        if len(pix) != 1:
            raise RuntimeError("extraction needs exactly one image file")
        if len(fm) != 1:
            raise RuntimeError("extraction needs exactly one fibermap file")

        imgfile = graph_path(pix[0])
        psffile = graph_path(psf[0])
        fmfile = graph_path(fm[0])
        outfile = graph_path(task)

        options = {}
        options["input"] = imgfile
        options["fibermap"] = fmfile
        options["psf"] = psffile
        options["output"] = outfile

        # extract the wavelength range from the options, depending on the band

        optscopy = copy.deepcopy(opts)
        wkey = "wavelength_{}".format(band)
        wave = optscopy[wkey]
        del optscopy["wavelength_b"]
        del optscopy["wavelength_r"]
        del optscopy["wavelength_z"]
        optscopy["wavelength"] = wave

        options.update(optscopy)
        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        if rank == 0:
            com = ["RUN", "desi_extract_spectra"]
            com.extend(optarray)
            log.info(" ".join(com))

        args = extract.parse(optarray)
        extract.main_mpi(args, comm=comm)

        return


class WorkerFiberflat(Worker):
    """
    Compute the 1D fiberflat.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 4 # 2 minutes on edison


    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Compute the fiberflat.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("Fiberflat worker should only be called with one process")

        log = get_logger()

        node = grph[task]
        night, obj = graph_night_split(task)
        (temp, band, spec, expid) = graph_name_split(obj)
        cam = "{}{}".format(band, spec)

        if len(node["in"]) != 1:
            raise RuntimeError("fiberflat should have only one input frame")
        framefile = graph_path(node["in"][0])
        outfile = graph_path(task)

        #qafile, qafig = qa_path(outfile)

        options = {}
        options["infile"] = framefile
        #options["qafile"] = qafile
        #options["qafig"] = qafig
        options["outfile"] = outfile
        options.update(opts)
        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        com = ["RUN", "desi_compute_fiberflat"]
        com.extend(optarray)
        log.info(" ".join(com))

        args = fiberflat.parse(optarray)
        fiberflat.main(args)

        return


class WorkerSky(Worker):
    """
    Compute the sky model.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 2 # less than 1 minute

    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Compute the sky model.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("Sky worker should only be called with one process")

        log = get_logger()

        node = grph[task]

        frm = []
        flat = []
        for input in node["in"]:
            inode = grph[input]
            if inode["type"] == "frame":
                frm.append(input)
            elif inode["type"] == "fiberflat":
                flat.append(input)
        if len(frm) != 1:
            raise RuntimeError("sky needs exactly one frame file")
        if len(flat) != 1:
            raise RuntimeError("sky needs exactly one fiberflat file")

        framefile = graph_path(frm[0])
        flatfile = graph_path(flat[0])
        outfile = graph_path(task)

        #qafile, qafig = qa_path(outfile)

        options = {}
        options["infile"] = framefile
        options["fiberflat"] = flatfile
        #options["qafile"] = qafile
        #options["qafig"] = qafig
        options["outfile"] = outfile
        options.update(opts)
        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        com = ["RUN", "desi_compute_sky"]
        com.extend(optarray)
        log.info(" ".join(com))

        args = skypkg.parse(optarray)
        skypkg.main(args)

        return


class WorkerStdstars(Worker):
    """
    Compute the standard stars for use in flux calibration.
    """
    def __init__(self, opts):
        self.starmodels = None
        if "starmodels" in opts:
            self.starmodels = opts["starmodels"]
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 15 # less than 4 min on edison but can vary quite a bit


    def default_options(self):
        log = get_logger()
        opts = {}
        if self.starmodels is not None:
            opts["starmodels"] = self.starmodels
        else:
            if "LVM_ROOT" in os.environ:
                opts["starmodels"] = os.environ["LVM_ROOT"]+"/spectro/templates/star_templates/v2.1/star_templates_v2.1.fits"
            else:
                log.warning("$LVM_ROOT not set; using NERSC default /project/projectdirs/desi")
                opts["starmodels"] = "/project/projectdirs/desi/spectro/templates/star_templates/v2.1/star_templates_v2.1.fits"
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Compute the std stars.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("Stdstars worker should only be called with one process")

        log = get_logger()

        node = grph[task]

        frm = []
        flat = []
        sky = []
        flatexp = None
        specgrph = None
        for input in node["in"]:
            inode = grph[input]
            if inode["type"] == "frame":
                frm.append(input)
                specgrph = inode["spec"]
            elif inode["type"] == "fiberflat":
                flat.append(input)
                flatexp = inode["id"]
            elif inode["type"] == "sky":
                sky.append(input)

        outfile = graph_path(task)

        #qafile, qafig = qa_path(outfile)

        framefiles = [graph_path(x) for x in frm]
        skyfiles = [graph_path(x) for x in sky]
        flatfiles = [graph_path(x) for x in flat]

        options = {}
        options["frames"] = framefiles
        options["skymodels"] = skyfiles
        options["fiberflats"] = flatfiles
        options["outfile"] = outfile
        # Do not mix MPI and multiprocessing.  We are already
        # running this across many processes per node, so do
        # not need multiprocessing.
        #options["ncpu"] = str(default_nproc)
        options["ncpu"] = "1"
        #- TODO: no QA for fitting standard stars yet

        options.update(opts)
        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        com = ["RUN", "desi_fit_stdstars"]
        com.extend(optarray)
        log.info(" ".join(com))

        args = stdstars.parse(optarray)
        stdstars.main(args)

        return


class WorkerFluxcal(Worker):
    """
    Compute the flux calibration.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 2 # this is fast

    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Compute the flux calibration.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("Fluxcal worker should only be called with one process")

        log = get_logger()

        node = grph[task]

        frm = []
        flat = []
        sky = []
        star = []
        for input in node["in"]:
            inode = grph[input]
            if inode["type"] == "frame":
                frm.append(input)
            elif inode["type"] == "fiberflat":
                flat.append(input)
            elif inode["type"] == "sky":
                sky.append(input)
            elif inode["type"] == "stdstars":
                star.append(input)
        if len(frm) != 1:
            raise RuntimeError("fluxcal needs exactly one frame file")
        if len(flat) != 1:
            raise RuntimeError("fluxcal needs exactly one fiberflat file")
        if len(sky) != 1:
            raise RuntimeError("fluxcal needs exactly one sky file")
        if len(star) != 1:
            raise RuntimeError("fluxcal needs exactly one star file")

        framefile = graph_path(frm[0])
        flatfile = graph_path(flat[0])
        skyfile = graph_path(sky[0])
        starfile = graph_path(star[0])
        outfile = graph_path(task)

        #qafile, qafig = qa_path(outfile)

        options = {}
        options["infile"] = framefile
        options["fiberflat"] = flatfile
        #options["qafile"] = qafile
        #options["qafig"] = qafig
        options["sky"] = skyfile
        options["models"] = starfile
        options["outfile"] = outfile
        options.update(opts)
        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        com = ["RUN", "desi_compute_fluxcalibration"]
        com.extend(optarray)
        log.info(" ".join(com))

        args = fluxcal.parse(optarray)
        fluxcal.main(args)

        return


class WorkerProcexp(Worker):
    """
    Apply the calibration to a frame.
    """
    def __init__(self, opts):
        super(Worker, self).__init__()


    def max_nproc(self):
        return 1

    def task_time(self):
        return 1 # fast

    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Apply the calibration.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        if comm is not None:
            if comm.size > 1:
                raise RuntimeError("Procexp worker should only be called with one process")

        log = get_logger()

        node = grph[task]

        frm = []
        flat = []
        sky = []
        cal = []
        for input in node["in"]:
            inode = grph[input]
            if inode["type"] == "frame":
                frm.append(input)
            elif inode["type"] == "fiberflat":
                flat.append(input)
            elif inode["type"] == "sky":
                sky.append(input)
            elif inode["type"] == "calib":
                cal.append(input)
        if len(frm) != 1:
            raise RuntimeError("procexp needs exactly one frame file")
        if len(flat) != 1:
            raise RuntimeError("procexp needs exactly one fiberflat file")
        if len(sky) != 1:
            raise RuntimeError("procexp needs exactly one sky file")
        if len(cal) != 1:
            raise RuntimeError("procexp needs exactly one calib file")

        framefile = graph_path(frm[0])
        flatfile = graph_path(flat[0])
        skyfile = graph_path(sky[0])
        calfile = graph_path(cal[0])
        outfile = graph_path(task)

        options = {}
        options["infile"] = framefile
        options["fiberflat"] = flatfile
        options["sky"] = skyfile
        options["calib"] = calfile
        options["outfile"] = outfile
        options.update(opts)
        optarray = option_list(options)

        # at debug level, write out the equivalent commandline
        com = ["RUN", "desi_process_exposure"]
        com.extend(optarray)
        log.info(" ".join(com))

        args = procexp.parse(optarray)
        procexp.main(args)

        return


class WorkerRedmonster(Worker):
    """
    Use Redmonster to classify spectra and compute redshifts.
    """
    def __init__(self, opts):
        self.nproc = 192
        if "nproc" in opts:
            self.nproc = opts["nproc"]
        super(Worker, self).__init__()


    def max_nproc(self):
        return self.nproc

    def task_time(self):
        return 30


    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Run Redmonster on a spectral group.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        nproc = 1
        rank = 0
        if comm is not None:
            nproc = comm.size
            rank = comm.rank

        log = get_logger()

        node = grph[task]

        spectra = node["spectra"]
        outfile = graph_path(task)
        #qafile, qafig = qa_path(outfile)
        options = {}
        options["brick"] = spectra
        options["outfile"] = outfile
        #- TODO: no QA for desi_zfind yet
        options.update(opts)
        optarray = option_list(options)

        # write out the equivalent commandline
        if rank == 0:
            com = ["RUN", "desi_zfind"]
            com.extend(optarray)
            log.info(" ".join(com))

        args = zfind.parse(optarray)
        zfind.main(args, comm=comm)

        return


class WorkerRedrock(Worker):
    """
    Use Redrock to classify spectra and compute redshifts.
    """
    def __init__(self, opts):
        self.nodes = 1
        self.nodeprocs = 1
        self.specpermin = 20
        if "nodes" in opts:
            self.nodes = int(opts["nodes"])
        if "nodeprocs" in opts:
            self.nodeprocs = int(opts["nodeprocs"])
        if "spec_per_minute" in opts:
            self.specpermin = opts["spec_per_minute"]
        super(Worker, self).__init__()

    def max_nproc(self):
        return (self.nodes * self.nodeprocs)

    def max_nodes(self):
        return self.nodes

    def node_procs(self):
        return self.nodeprocs

    def spec_per_min(self):
        return self.specpermin

    def task_time(self):
        return 30 # this really depends on the size of the spectra file??

    def default_options(self):
        opts = {}
        return opts


    def run(self, grph, task, opts, comm=None):
        """
        Run Redrock on a spectral group.

        Args:
            grph (dict): pruned graph with this task and dependencies.
            task (str): the name of this task.
            opts (dict): options to use for this task.
            comm (mpi4py.MPI.Comm): optional MPI communicator.
        """
        nproc = 1
        rank = 0
        if comm is not None:
            nproc = comm.size
            rank = comm.rank

        log = get_logger()

        node = grph[task]

        spectra = "spectra-{}-{}".format(node["nside"], node["pixel"])
        specfile = graph_path(spectra)

        outfile = graph_path(task)
        outdir = os.path.dirname(outfile)
        details = os.path.join(outdir, "rrdetails_{}.h5".format(task))

        options = {}
        options["output"] = details
        options["zbest"] = outfile
        options.update(opts)
        optarray = option_list(options)
        optarray.append(specfile)

        # write out the equivalent commandline
        if rank == 0:
            com = ["RUN", "rrdesi_mpi"]
            com.extend(optarray)
            log.info(" ".join(com))

        failcount = 0
        try:
            rrdesi(options=optarray, comm=comm)
        except:
            failcount += 1
            sys.stdout.flush()

        if comm is not None:
            failcount = comm.allreduce(failcount)

        if failcount > 0:
            # all processes throw
            raise RuntimeError("some redshifts failed for task "
                "{}".format(task))

        return


class WorkerNoop(Worker):
    """
    Fake Worker that simply creates the output files for a task.
    """
    def __init__(self, opts):
        self.defaults = opts
        super(Worker, self).__init__()

    def max_nproc(self):
        return 1

    def task_time(self):
        return 5

    def default_options(self):
        return self.defaults

    def run(self, grph, task, opts, comm=None):
        nproc = 1
        rank = 0
        if comm is not None:
            nproc = comm.size
            rank = comm.rank
        log = get_logger()
        node = grph[task]
        p = graph_path(task)
        log.info("NOOP Worker creating {}".format(p))
        with open(p, "w") as f:
            f.write("NOOP\n")
        return


def get_worker(step, name, opts):
    """
    Instantiate a worker.

    This is a factory function that instantiates a worker
    for a pipeline step, and passes any extra arguments to
    the worker constructor.

    Args:
        step (str): the pipeline step name.
        name (str): the name of the class, Worker<name>.
        opts (dict): extra options to pass to the worker
            constructor.

    Returns (Worker):
        an instance of a worker class.
    """
    if name is None:
        name = default_workers[step]
    classname = "Worker{}".format(name)

    thismodule = sys.modules[__name__]
    workerclass = getattr(thismodule, classname)
    worker = workerclass(opts)
    return worker


def default_options(extra={}):
    """
    Get the default options for all workers.

    Args:
        extra (dict): optional extra options to add to the
            default options for each worker class.

    Returns (dict):
        the default options dictionary, suitable for writing
        to the default options.yaml file.
    """

    log = get_logger()

    allopts = {}

    for step in step_types:
        defwork = default_workers[step]
        allopts["{}_worker".format(step)] = defwork
        if defwork in extra:
            allopts["{}_worker_opts".format(step)] = extra[defwork]
        else:
            allopts["{}_worker_opts".format(step)] = {}
        worker = get_worker(step, None, {})
        allopts[step] = worker.default_options()

    return allopts
