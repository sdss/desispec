#
# See top-level LICENSE.rst file for Copyright information
#
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

from collections import OrderedDict

from ..defs import (task_name_sep, task_state_to_int, task_int_to_state)

from ...util import option_list

from ...io import findfile

from .base import (BaseTask, task_classes)

from desiutil.log import get_logger

import sys,re,os,copy

# NOTE: only one class in this file should have a name that starts with "Task".

class TaskFluxCalib(BaseTask):
    """Class containing the properties of a sky fit task.
    """
    def __init__(self):
        super(TaskFluxCalib, self).__init__()
        # then put int the specifics of this class
        # _cols must have a state
        self._type = "fluxcalib"
        self._cols = [
            "night",
            "band",
            "spec",
            "expid",
            "state"
        ]
        self._coltypes = [
            "integer",
            "text",
            "integer",
            "integer",
            "integer"
        ]
        # _name_fields must also be in _cols
        self._name_fields  = ["night","band","spec","expid"]
        self._name_formats = ["08d","s","d","08d"]

    def _paths(self, name):
        """See BaseTask.paths.
        """
        props = self.name_split(name)
        camera = "{}{}".format(props["band"], props["spec"])
        return [ findfile("calib", night=props["night"], expid=props["expid"],
            camera=camera, groupname=None, nside=None, band=props["band"],
            spectrograph=props["spec"]) ]

    def _deps(self, name, db, inputs):
        """See BaseTask.deps.
        """
        from .base import task_classes
        props = self.name_split(name)
        deptasks = {
            "infile" : task_classes["extract"].name_join(props),
            "fiberflat" : task_classes["fiberflatnight"].name_join(props),
            "sky" : task_classes["sky"].name_join(props),
            "models" : task_classes["starfit"].name_join(props)
        }
        return deptasks

    def _run_max_procs(self):
        # This is a serial task.
        return 1

    def _run_time(self, name, procs, db):
        # Run time on one proc on machine with scale factor == 1.0
        return 3

    def _run_defaults(self):
        """See BaseTask.run_defaults.
        """
        opts = {}
        return opts


    def _option_list(self, name, opts):
        """Build the full list of options.

        This includes appending the filenames and incorporating runtime
        options.
        """
        from .base import task_classes, task_type

        deps = self.deps(name)
        options = {}
        options["infile"]    = task_classes["extract"].paths(deps["infile"])[0]
        options["fiberflat"] = task_classes["fiberflatnight"].paths(deps["fiberflat"])[0]
        options["sky"]       = task_classes["sky"].paths(deps["sky"])[0]
        options["models"]    = task_classes["starfit"].paths(deps["models"])[0]
        options["outfile"]   = self.paths(name)[0]

        options.update(opts)
        return option_list(options)

    def _run_cli(self, name, opts, procs, db):
        """See BaseTask.run_cli.
        """
        entry = "desi_compute_fluxcalibration"
        optlist = self._option_list(name, opts)
        com = "{} {}".format(entry, " ".join(optlist))
        return com

    def _run(self, name, opts, comm, db):
        """See BaseTask.run.
        """
        from ...scripts import fluxcalibration
        optlist = self._option_list(name, opts)
        args = fluxcalibration.parse(optlist)
        fluxcalibration.main(args)
        return

    def postprocessing(self, db, name, cur):
        """For successful runs, postprocessing on DB"""
        # run getready on all fierflatnight with same night,band,spec
        props = self.name_split(name)
        log  = get_logger()
        tt="cframe"
        cmd = "select name from {} where night={} and expid={} and spec={} and band='{}' and state=0".format(tt,props["night"],props["expid"],props["spec"],props["band"])
        cur.execute(cmd)
        tasks = [ x for (x,) in cur.fetchall() ]
        log.debug("checking {}".format(tasks))
        for task in tasks :
            task_classes[tt].getready( db=db,name=task,cur=cur)
